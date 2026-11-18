-- These are reviewed maximum reservations, never actual supplier charges or user credits.
CREATE TABLE ops.provider_budgets (
 id uuid PRIMARY KEY, quote_id uuid NOT NULL UNIQUE, user_id uuid NOT NULL,
 input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 scope_sha256 text NOT NULL CHECK(scope_sha256 ~ '^[0-9a-f]{64}$'),
 canonical_scope text NOT NULL CHECK(octet_length(canonical_scope)<=65536),
 scope_document jsonb NOT NULL CHECK(jsonb_typeof(scope_document)='object'),
 currency text NOT NULL CHECK(currency='USD'),
 generate_ceiling numeric NOT NULL CHECK(generate_ceiling>0 AND generate_ceiling<1000000),
 detect_ceiling numeric NOT NULL CHECK(detect_ceiling>0 AND detect_ceiling<1000000),
 total_ceiling numeric NOT NULL CHECK(total_ceiling>=generate_ceiling+detect_ceiling AND total_ceiling<1000000),
 authorized_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','blocked','closed')),
 operation_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_id,quote_id) REFERENCES app.quotes(user_id,id),
 CHECK(expires_at>authorized_at AND expires_at<=authorized_at+interval '24 hours')
);
CREATE TABLE ops.provider_budget_holds (
 attempt_id uuid PRIMARY KEY REFERENCES app.provider_attempts(id),
 authorization_id uuid NOT NULL REFERENCES ops.provider_budgets(id),
 step text NOT NULL CHECK(step IN ('generate','detect')),
 ceiling numeric NOT NULL CHECK(ceiling>0 AND ceiling<1000000),
 outcome text NOT NULL DEFAULT 'reserved' CHECK(outcome IN ('reserved','known','uncertain','failed')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(authorization_id,step)
);
ALTER TABLE ops.provider_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_budgets FORCE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_budget_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_budget_holds FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.provider_budgets,ops.provider_budget_holds FROM PUBLIC;
GRANT SELECT,INSERT ON ops.provider_budgets,ops.provider_budget_holds TO app_executor;
GRANT UPDATE(state) ON ops.provider_budgets TO app_executor;
GRANT UPDATE(outcome) ON ops.provider_budget_holds TO app_executor;
GRANT SELECT ON ops.provider_budgets,ops.provider_budget_holds TO ops_admin;
CREATE POLICY budget_executor ON ops.provider_budgets TO app_executor USING(true) WITH CHECK(true);
CREATE POLICY hold_executor ON ops.provider_budget_holds TO app_executor USING(true) WITH CHECK(true);
CREATE POLICY budget_operator ON ops.provider_budgets FOR SELECT TO ops_admin USING(true);
CREATE POLICY hold_operator ON ops.provider_budget_holds FOR SELECT TO ops_admin USING(true);

CREATE FUNCTION ops.guard_provider_budget_metadata() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'provider_budget_immutable'; END IF;
 IF TG_TABLE_NAME='provider_budgets' THEN
  IF to_jsonb(NEW)-'state' IS DISTINCT FROM to_jsonb(OLD)-'state' THEN RAISE EXCEPTION 'provider_budget_immutable'; END IF;
 ELSE
  IF to_jsonb(NEW)-'outcome' IS DISTINCT FROM to_jsonb(OLD)-'outcome' THEN RAISE EXCEPTION 'provider_budget_immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ops.guard_provider_budget_metadata() FROM PUBLIC;
CREATE TRIGGER provider_budget_immutable BEFORE UPDATE OR DELETE ON ops.provider_budgets FOR EACH ROW EXECUTE FUNCTION ops.guard_provider_budget_metadata();
CREATE TRIGGER provider_hold_immutable BEFORE UPDATE OR DELETE ON ops.provider_budget_holds FOR EACH ROW EXECUTE FUNCTION ops.guard_provider_budget_metadata();

CREATE FUNCTION ops.register_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; q app.quotes; prior ops.audit_events; field text; u uuid; starts timestamptz; finishes timestamptz; g numeric; v numeric; cap numeric;
BEGIN
 IF p_id IS NULL OR p_quote IS NULL OR p_operation IS NULL OR p_canonical IS NULL OR octet_length(p_canonical)>65536
 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_provider_budget'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 IF NOT (d ?& ARRAY['schema_version','authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation','targets','bfl_model','gemini_model','max_generate_calls','max_detect_calls','currency','generate_ceiling','detect_ceiling','total_ceiling'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(d) k WHERE k NOT IN ('schema_version','authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation','targets','bfl_model','gemini_model','max_generate_calls','max_detect_calls','currency','generate_ceiling','detect_ceiling','total_ceiling')) THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 FOREACH field IN ARRAY ARRAY['authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','bfl_model','gemini_model','currency','generate_ceiling','detect_ceiling','total_ceiling'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 END LOOP;
 FOREACH field IN ARRAY ARRAY['schema_version','max_generate_calls','max_detect_calls'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'number' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 END LOOP;
 IF d->>'schema_version' IS DISTINCT FROM '2' OR d->>'max_generate_calls' IS DISTINCT FROM '1' OR d->>'max_detect_calls' IS DISTINCT FROM '1'
 OR d->>'bfl_model' IS DISTINCT FROM 'flux-3-image' OR d->>'gemini_model' IS DISTINCT FROM 'gemini-3.8-flash'
 OR d->>'currency' IS DISTINCT FROM 'USD' OR (d->>'authorization_id')::uuid IS DISTINCT FROM p_id
 OR length(trim(d->>'authorized_by')) NOT BETWEEN 1 AND 160 OR length(d->>'source_path') NOT BETWEEN 1 AND 1000
 OR jsonb_typeof(d->'generation') IS DISTINCT FROM 'object' OR jsonb_typeof(d->'targets') IS DISTINCT FROM 'array'
 THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 FOREACH field IN ARRAY ARRAY['generate_ceiling','detect_ceiling','total_ceiling'] LOOP
  IF d->>field !~ '^(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?$' THEN RAISE EXCEPTION 'invalid_provider_ceiling'; END IF;
 END LOOP;
 g:=(d->>'generate_ceiling')::numeric; v:=(d->>'detect_ceiling')::numeric; cap:=(d->>'total_ceiling')::numeric;
 IF g<=0 OR v<=0 OR cap<g+v THEN RAISE EXCEPTION 'invalid_provider_ceiling'; END IF;
 IF d->>'authorized_at' !~ '(Z|\+00:00)$' OR d->>'expires_at' !~ '(Z|\+00:00)$' THEN RAISE EXCEPTION 'invalid_provider_window'; END IF;
 starts:=(d->>'authorized_at')::timestamptz; finishes:=(d->>'expires_at')::timestamptz;
 IF finishes<=starts OR finishes>starts+interval '24 hours' THEN RAISE EXCEPTION 'invalid_provider_window'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'provider_budget_registered' OR prior.target_id<>p_id OR prior.evidence_digest IS DISTINCT FROM p_sha
  OR prior.reason<>p_reason OR prior.after_state->>'quote_id' IS DISTINCT FROM p_quote::text
  OR NOT EXISTS(SELECT 1 FROM ops.provider_budgets WHERE id=p_id AND quote_id=p_quote AND scope_sha256=p_sha) THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_id;
 END IF;
 IF clock_timestamp()<starts OR clock_timestamp()>=finishes THEN RAISE EXCEPTION 'provider_budget_expired'; END IF;
 u:=(d->>'user_id')::uuid;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO q FROM app.quotes WHERE id=p_quote AND user_id=u FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'quote_not_found'; END IF;
 IF q.consumed_task_id IS NOT NULL OR EXISTS(SELECT 1 FROM app.generation_tasks WHERE quote_id=q.id) THEN RAISE EXCEPTION 'provider_budget_quote_consumed'; END IF;
 IF q.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'quote_expired'; END IF;
 IF q.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock' THEN RAISE EXCEPTION 'provider_budget_requires_mock_quote'; END IF;
 IF q.input_sha256 IS DISTINCT FROM d->>'quote_input_sha256' THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 IF q.input_snapshot->>'product_id' IS DISTINCT FROM (d->>'product_id')::uuid::text
 OR q.input_snapshot->>'source_asset_version_id' IS DISTINCT FROM (d->>'source_asset_version_id')::uuid::text
 OR q.input_snapshot->'_execution'->>'source_sha256' IS DISTINCT FROM d->>'source_sha256'
 OR q.input_snapshot->'generation' IS DISTINCT FROM d->'generation' OR q.input_snapshot->'targets' IS DISTINCT FROM d->'targets'
 THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM ops.provider_budgets WHERE id=p_id OR quote_id=p_quote) THEN RAISE EXCEPTION 'provider_budget_already_registered'; END IF;
 INSERT INTO ops.provider_budgets(id,quote_id,user_id,input_sha256,scope_sha256,canonical_scope,scope_document,currency,generate_ceiling,detect_ceiling,total_ceiling,authorized_at,expires_at,operation_id)
 VALUES(p_id,q.id,u,q.input_sha256,p_sha,p_canonical,d,'USD',g,v,cap,starts,finishes,p_operation);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'provider_budget_registered','provider_budget',p_id,p_reason,p_sha,jsonb_build_object('quote_id',q.id,'profile','offline_protocol_mock','currency','USD','generate_ceiling',g::text,'detect_ceiling',v::text,'total_ceiling',cap::text),p_operation);
 RETURN p_id;
END $$;
ALTER FUNCTION ops.register_provider_budget(uuid,uuid,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.register_provider_budget(uuid,uuid,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.register_provider_budget(uuid,uuid,text,text,uuid,text) TO ops_admin;

CREATE FUNCTION ops.reserve_provider_budget(p_task uuid,p_attempt uuid,p_step text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; b ops.provider_budgets; amount numeric; held numeric;
BEGIN
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task;
 IF NOT FOUND THEN RAISE EXCEPTION 'task_not_found'; END IF;
 SELECT * INTO b FROM ops.provider_budgets WHERE quote_id=t.quote_id FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 IF b.state<>'open' THEN RAISE EXCEPTION 'provider_budget_blocked'; END IF;
 IF clock_timestamp()<b.authorized_at OR clock_timestamp()>=b.expires_at THEN RAISE EXCEPTION 'provider_budget_expired'; END IF;
 IF b.user_id<>t.user_id OR b.input_sha256 IS DISTINCT FROM t.input_sha256
 OR t.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
 THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 IF p_step IS NULL OR p_step NOT IN ('generate','detect') OR NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id AND step=p_step) THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step=p_step)
 OR EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND outcome IN ('reserved','uncertain','failed')) THEN RAISE EXCEPTION 'provider_budget_exhausted'; END IF;
 amount:=CASE WHEN p_step='generate' THEN b.generate_ceiling ELSE b.detect_ceiling END;
 SELECT COALESCE(sum(ceiling),0) INTO held FROM ops.provider_budget_holds WHERE authorization_id=b.id;
 IF held+amount>b.total_ceiling THEN RAISE EXCEPTION 'provider_budget_exhausted'; END IF;
 INSERT INTO ops.provider_budget_holds(attempt_id,authorization_id,step,ceiling) VALUES(p_attempt,b.id,p_step,amount);
END $$;
ALTER FUNCTION ops.reserve_provider_budget(uuid,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.reserve_provider_budget(uuid,uuid,text) FROM PUBLIC;

CREATE FUNCTION ops.observe_provider_budget_attempt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE identity uuid;
BEGIN
 SELECT authorization_id INTO identity FROM ops.provider_budget_holds WHERE attempt_id=NEW.id;
 IF identity IS NULL THEN RETURN NEW; END IF;
 PERFORM 1 FROM ops.provider_budgets WHERE id=identity FOR UPDATE;
 UPDATE ops.provider_budget_holds SET outcome=CASE WHEN NEW.status='uncertain' THEN 'uncertain' WHEN NEW.status='failed' THEN 'failed' WHEN NEW.provider_id IS NOT NULL THEN 'known' ELSE outcome END WHERE attempt_id=NEW.id;
 IF NEW.status IN ('uncertain','failed') THEN UPDATE ops.provider_budgets SET state='blocked' WHERE id=identity AND state='open'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION ops.observe_provider_budget_attempt() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.observe_provider_budget_attempt() FROM PUBLIC;
CREATE TRIGGER provider_budget_attempt AFTER UPDATE OF status,provider_id ON app.provider_attempts FOR EACH ROW EXECUTE FUNCTION ops.observe_provider_budget_attempt();

CREATE FUNCTION ops.observe_provider_budget_task() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.status IN ('succeeded','cancelled') THEN UPDATE ops.provider_budgets SET state='closed' WHERE quote_id=NEW.quote_id AND state IN ('open','blocked');
 ELSIF NEW.status IN ('uncertain','failed') THEN UPDATE ops.provider_budgets SET state='blocked' WHERE quote_id=NEW.quote_id AND state='open'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION ops.observe_provider_budget_task() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.observe_provider_budget_task() FROM PUBLIC;
CREATE TRIGGER provider_budget_task AFTER UPDATE OF status ON app.generation_tasks FOR EACH ROW EXECUTE FUNCTION ops.observe_provider_budget_task();

CREATE FUNCTION ops.provider_budget_snapshot(p_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('authorization_id',b.id,'quote_id',b.quote_id,'scope_sha256',b.scope_sha256,'profile','offline_protocol_mock','currency',b.currency,'state',b.state,'expires_at',b.expires_at,'total_ceiling',b.total_ceiling::text,
 'reserved_ceiling_total',(SELECT COALESCE(sum(h.ceiling),0)::text FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id),
 'actual_spending_verified',false,'holds',COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt_id',h.attempt_id,'step',h.step,'ceiling',h.ceiling::text,'outcome',h.outcome) ORDER BY h.created_at,h.attempt_id) FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id),'[]'::jsonb))
 FROM ops.provider_budgets b WHERE b.id=p_id
$$;
ALTER FUNCTION ops.provider_budget_snapshot(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.provider_budget_snapshot(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.provider_budget_snapshot(uuid) TO ops_admin;

-- Same function OID/ACL: no unbudgeted helper remains callable by the Worker.
CREATE OR REPLACE FUNCTION app.record_intent(p_task uuid,p_owner text,p_fence bigint,p_step text,p_request_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a uuid;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_step NOT IN ('generate','detect') OR p_request_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_intent'; END IF;
 IF (p_step='generate' AND t.phase<>'submitting') OR (p_step='detect' AND (t.phase<>'detecting' OR t.final_version_id IS NULL)) THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step=p_step) THEN RAISE EXCEPTION 'intent_exists' USING ERRCODE='55000'; END IF;
 INSERT INTO app.provider_attempts(user_id,task_id,step,provider,model,status,request_hash,local_request_id)
 VALUES(t.user_id,t.id,p_step,CASE WHEN t.input_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_step='generate' THEN 'bfl_mock' ELSE 'gemini_mock' END ELSE 'fake' END,CASE WHEN t.input_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_step='generate' THEN 'flux-3-image' ELSE 'gemini-3.8-flash' END ELSE CASE WHEN p_step='generate' THEN t.input_snapshot->'_execution'->>'provider_model' ELSE 'fake-facts-v1' END END,'intent_recorded',p_request_hash,gen_random_uuid()) RETURNING id INTO a;
 PERFORM ops.reserve_provider_budget(t.id,a,p_step);
 RETURN a;
END $$;

ALTER FUNCTION app.record_intent(uuid,text,bigint,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_intent(uuid,text,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_intent(uuid,text,bigint,text,text) TO app_worker;

CREATE FUNCTION ops.provider_quote_review(p_user uuid,p_quote uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q app.quotes;
BEGIN
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO q FROM app.quotes WHERE id=p_quote AND user_id=p_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'quote_not_found'; END IF;
 IF q.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock' THEN RAISE EXCEPTION 'provider_budget_requires_mock_quote'; END IF;
 RETURN jsonb_build_object('quote_id',q.id,'user_id',q.user_id,'input_sha256',q.input_sha256,'input_snapshot',q.input_snapshot,'expires_at',q.expires_at);
END $$;
ALTER FUNCTION ops.provider_quote_review(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.provider_quote_review(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.provider_quote_review(uuid,uuid) TO ops_admin;
