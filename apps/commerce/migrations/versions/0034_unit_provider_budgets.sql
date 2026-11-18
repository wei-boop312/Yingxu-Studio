-- Unit-separated authorization; no FX conversion and no cross-unit monetary total.
ALTER TABLE ops.provider_budgets ADD COLUMN schema_version integer NOT NULL DEFAULT 2 CHECK(schema_version IN (2,3));
ALTER TABLE ops.provider_budgets ALTER COLUMN currency DROP NOT NULL;
ALTER TABLE ops.provider_budgets ALTER COLUMN generate_ceiling DROP NOT NULL;
ALTER TABLE ops.provider_budgets ALTER COLUMN detect_ceiling DROP NOT NULL;
ALTER TABLE ops.provider_budgets ALTER COLUMN total_ceiling DROP NOT NULL;
ALTER TABLE ops.provider_budgets ADD CONSTRAINT provider_budget_version_fields CHECK(
 (schema_version=2 AND currency IS NOT NULL AND generate_ceiling IS NOT NULL AND detect_ceiling IS NOT NULL AND total_ceiling IS NOT NULL)
 OR (schema_version=3 AND currency IS NULL AND generate_ceiling IS NULL AND detect_ceiling IS NULL AND total_ceiling IS NULL));
ALTER TABLE ops.provider_budget_holds ADD COLUMN currency text NOT NULL DEFAULT 'USD'
 CHECK(currency IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT'));
CREATE TABLE ops.provider_step_limits (
 authorization_id uuid NOT NULL REFERENCES ops.provider_budgets(id),
 step text NOT NULL CHECK(step IN ('generate','detect')),
 currency text NOT NULL CHECK(currency IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT')),
 ceiling numeric NOT NULL CHECK(ceiling>0 AND ceiling<1000000), PRIMARY KEY(authorization_id,step)
);
CREATE TABLE ops.provider_total_limits (
 authorization_id uuid NOT NULL REFERENCES ops.provider_budgets(id),
 currency text NOT NULL CHECK(currency IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT')),
 ceiling numeric NOT NULL CHECK(ceiling>0 AND ceiling<1000000), PRIMARY KEY(authorization_id,currency)
);
INSERT INTO ops.provider_step_limits SELECT id,'generate',currency,generate_ceiling FROM ops.provider_budgets;
INSERT INTO ops.provider_step_limits SELECT id,'detect',currency,detect_ceiling FROM ops.provider_budgets;
INSERT INTO ops.provider_total_limits SELECT id,currency,total_ceiling FROM ops.provider_budgets;
ALTER TABLE ops.provider_step_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_step_limits FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.provider_step_limits FROM PUBLIC;
GRANT SELECT,INSERT ON ops.provider_step_limits TO app_executor;
GRANT SELECT ON ops.provider_step_limits TO ops_admin;
CREATE POLICY step_limit_executor ON ops.provider_step_limits TO app_executor USING(true) WITH CHECK(true);
CREATE POLICY step_limit_operator ON ops.provider_step_limits FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_step_limit BEFORE UPDATE OR DELETE ON ops.provider_step_limits FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
ALTER TABLE ops.provider_total_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_total_limits FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.provider_total_limits FROM PUBLIC;
GRANT SELECT,INSERT ON ops.provider_total_limits TO app_executor;
GRANT SELECT ON ops.provider_total_limits TO ops_admin;
CREATE POLICY total_limit_executor ON ops.provider_total_limits TO app_executor USING(true) WITH CHECK(true);
CREATE POLICY total_limit_operator ON ops.provider_total_limits FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_total_limit BEFORE UPDATE OR DELETE ON ops.provider_total_limits FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE FUNCTION ops.initialize_provider_limits() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.schema_version=2 THEN
  INSERT INTO ops.provider_step_limits VALUES(NEW.id,'generate',NEW.currency,NEW.generate_ceiling),(NEW.id,'detect',NEW.currency,NEW.detect_ceiling);
  INSERT INTO ops.provider_total_limits VALUES(NEW.id,NEW.currency,NEW.total_ceiling);
 ELSE
  INSERT INTO ops.provider_step_limits
  SELECT NEW.id,k,v->>'currency',(v->>'ceiling')::numeric FROM jsonb_each(NEW.scope_document->'step_budgets') AS e(k,v);
  INSERT INTO ops.provider_total_limits
  SELECT NEW.id,v->>'currency',(v->>'ceiling')::numeric FROM jsonb_array_elements(NEW.scope_document->'currency_totals') AS e(v);
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION ops.initialize_provider_limits() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.initialize_provider_limits() FROM PUBLIC;
CREATE TRIGGER initialize_provider_limits AFTER INSERT ON ops.provider_budgets FOR EACH ROW EXECUTE FUNCTION ops.initialize_provider_limits();
CREATE FUNCTION ops.register_unit_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; q app.quotes; prior ops.audit_events; field text; u uuid; starts timestamptz; finishes timestamptz; g numeric; v numeric; cap numeric; item jsonb; unit text; step text; price ops.supplier_price_versions; binding jsonb;
BEGIN
 IF p_id IS NULL OR p_quote IS NULL OR p_operation IS NULL OR p_canonical IS NULL OR octet_length(p_canonical)>65536
 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_provider_budget'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 IF NOT (d ?& ARRAY['schema_version','authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation','targets','bfl_model','gemini_model','max_generate_calls','max_detect_calls','step_budgets','currency_totals'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(d) k WHERE k NOT IN ('schema_version','authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation','targets','bfl_model','gemini_model','max_generate_calls','max_detect_calls','step_budgets','currency_totals')) THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 FOREACH field IN ARRAY ARRAY['authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','bfl_model','gemini_model'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 END LOOP;
 FOREACH field IN ARRAY ARRAY['schema_version','max_generate_calls','max_detect_calls'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'number' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 END LOOP;
 IF d->>'schema_version' IS DISTINCT FROM '3' OR d->>'max_generate_calls' IS DISTINCT FROM '1' OR d->>'max_detect_calls' IS DISTINCT FROM '1'
 OR d->>'bfl_model' IS DISTINCT FROM 'flux-3-image' OR d->>'gemini_model' IS DISTINCT FROM 'gemini-3.8-flash'
 OR (d->>'authorization_id')::uuid IS DISTINCT FROM p_id
 OR length(trim(d->>'authorized_by')) NOT BETWEEN 1 AND 160 OR length(d->>'source_path') NOT BETWEEN 1 AND 1000
 OR jsonb_typeof(d->'generation') IS DISTINCT FROM 'object' OR jsonb_typeof(d->'targets') IS DISTINCT FROM 'array'
 THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 IF jsonb_typeof(d->'step_budgets') IS DISTINCT FROM 'object' OR jsonb_typeof(d->'currency_totals') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'invalid_provider_units'; END IF;
 IF NOT ((d->'step_budgets') ?& ARRAY['generate','detect']) OR (SELECT count(*) FROM jsonb_object_keys(d->'step_budgets'))<>2
 OR jsonb_array_length(d->'currency_totals') NOT BETWEEN 1 AND 2 THEN RAISE EXCEPTION 'invalid_provider_units'; END IF;
 FOR item IN SELECT value FROM jsonb_each(d->'step_budgets') UNION ALL SELECT value FROM jsonb_array_elements(d->'currency_totals') LOOP
  IF jsonb_typeof(item) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_provider_units'; END IF;
  IF NOT(item ?& ARRAY['currency','ceiling']) OR (SELECT count(*) FROM jsonb_object_keys(item))<>2
  OR jsonb_typeof(item->'currency') IS DISTINCT FROM 'string' OR jsonb_typeof(item->'ceiling') IS DISTINCT FROM 'string'
  OR item->>'currency' NOT IN ('USD','CNY','BFL_CREDIT','HUBWAY_BALANCE_UNIT') OR item->>'ceiling' !~ '^(0|[1-9][0-9]{0,5})([.][0-9]{1,6})?$'
  THEN RAISE EXCEPTION 'invalid_provider_units'; END IF;
  IF (item->>'ceiling')::numeric<=0 THEN RAISE EXCEPTION 'invalid_provider_ceiling'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'currency') FROM jsonb_array_elements(d->'currency_totals'))<>jsonb_array_length(d->'currency_totals')
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(d->'currency_totals') c WHERE NOT EXISTS(SELECT 1 FROM jsonb_each(d->'step_budgets') e WHERE e.value->>'currency'=c->>'currency'))
 OR EXISTS(SELECT 1 FROM jsonb_each(d->'step_budgets') e WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(d->'currency_totals') c WHERE c->>'currency'=e.value->>'currency'))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(d->'currency_totals') c WHERE (c->>'ceiling')::numeric<(SELECT sum((e.value->>'ceiling')::numeric) FROM jsonb_each(d->'step_budgets') e WHERE e.value->>'currency'=c->>'currency'))
 THEN RAISE EXCEPTION 'invalid_provider_ceiling'; END IF;
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
 FOREACH step IN ARRAY ARRAY['generate','detect'] LOOP
  binding:=q.input_snapshot->'_execution'->'provider_price_versions'->step;
  SELECT * INTO price FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
  IF NOT FOUND OR price.document->>'currency' IS DISTINCT FROM d->'step_budgets'->step->>'currency'
  OR price.provider IS DISTINCT FROM (CASE WHEN step='generate' THEN 'bfl' ELSE 'gemini' END)
  OR clock_timestamp()<price.valid_from OR clock_timestamp()>=price.valid_until
  THEN RAISE EXCEPTION 'provider_budget_price_unit_mismatch'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM ops.provider_budgets WHERE id=p_id OR quote_id=p_quote) THEN RAISE EXCEPTION 'provider_budget_already_registered'; END IF;
 INSERT INTO ops.provider_budgets(id,quote_id,user_id,input_sha256,scope_sha256,canonical_scope,scope_document,schema_version,currency,generate_ceiling,detect_ceiling,total_ceiling,authorized_at,expires_at,operation_id)
 VALUES(p_id,q.id,u,q.input_sha256,p_sha,p_canonical,d,3,NULL,NULL,NULL,NULL,starts,finishes,p_operation);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'provider_budget_registered','provider_budget',p_id,p_reason,p_sha,jsonb_build_object('quote_id',q.id,'profile','offline_protocol_mock','schema_version',3,'step_budgets',d->'step_budgets','currency_totals',d->'currency_totals'),p_operation);
 RETURN p_id;
END $$;
ALTER FUNCTION ops.register_unit_provider_budget(uuid,uuid,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.register_unit_provider_budget(uuid,uuid,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.register_unit_provider_budget(uuid,uuid,text,text,uuid,text) TO ops_admin;

CREATE OR REPLACE FUNCTION ops.register_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; q app.quotes; prior ops.audit_events; field text; u uuid; starts timestamptz; finishes timestamptz; g numeric; v numeric; cap numeric;
BEGIN
 IF p_id IS NULL OR p_quote IS NULL OR p_operation IS NULL OR p_canonical IS NULL OR octet_length(p_canonical)>65536
 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_provider_budget'; END IF;
 d:=p_canonical::jsonb;
 IF d->>'schema_version'='3' THEN RETURN ops.register_unit_provider_budget(p_id,p_quote,p_canonical,p_sha,p_operation,p_reason); END IF;
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

CREATE OR REPLACE FUNCTION ops.reserve_provider_budget(p_task uuid,p_attempt uuid,p_step text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; b ops.provider_budgets; amount numeric; held numeric; unit text; cap numeric;
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
 SELECT ceiling,currency INTO amount,unit FROM ops.provider_step_limits WHERE authorization_id=b.id AND step=p_step;
 IF NOT FOUND THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 SELECT ceiling INTO cap FROM ops.provider_total_limits WHERE authorization_id=b.id AND currency=unit;
 IF NOT FOUND THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 SELECT COALESCE(sum(ceiling),0) INTO held FROM ops.provider_budget_holds WHERE authorization_id=b.id AND currency=unit;
 IF held+amount>cap THEN RAISE EXCEPTION 'provider_budget_exhausted'; END IF;
 INSERT INTO ops.provider_budget_holds(attempt_id,authorization_id,step,ceiling,currency) VALUES(p_attempt,b.id,p_step,amount,unit);
END $$;
ALTER FUNCTION ops.reserve_provider_budget(uuid,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.reserve_provider_budget(uuid,uuid,text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION app.record_supplier_estimate(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; p ops.supplier_price_versions; observation app.task_cost_records;
 existing app.task_cost_records; e ops.supplier_cost_estimates; b ops.provider_budgets; h ops.provider_budget_holds;
 binding jsonb; q jsonb; raw jsonb; rate jsonb; names text[]; field text; amount numeric:=0; quantity numeric;
 status text:='unknown'; reason text:='usage_incomplete'; currency text; ceiling text:='unknown'; identity uuid; total numeric; unit_cap numeric;
 numerator numeric:=0; denominator numeric:=1; term_scaled numeric; per_units numeric; scaled_amount numeric;
 prompt numeric; cached numeric; candidates numeric; thoughts numeric; tools numeric; summed numeric;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'attempt_not_found'; END IF;
 binding:=t.input_snapshot->'_execution'->'provider_price_versions'->a.step;
 IF binding IS NULL THEN RETURN NULL; END IF;
 SELECT c.* INTO existing FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e0 ON e0.cost_record_id=c.id WHERE e0.attempt_id=a.id AND e0.user_id=t.user_id;
 IF FOUND THEN RETURN jsonb_build_object('id',existing.id,'cost_status',existing.cost_status,'amount',existing.amount::text,'currency',existing.currency,'actual_spending_verified',false); END IF;
 IF a.provider_id IS NULL OR a.response_digest IS NULL THEN RAISE EXCEPTION 'estimate_receipt_required'; END IF;
 SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
 IF NOT FOUND OR p.model IS DISTINCT FROM a.model OR p.provider IS DISTINCT FROM (CASE WHEN a.step='generate' THEN 'bfl' ELSE 'gemini' END)
 THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
 SELECT * INTO observation FROM app.task_cost_records WHERE user_id=t.user_id AND task_id=t.id AND attempt_id=a.id AND cost_record_kind='response_observation';
 IF NOT FOUND OR observation.usage->>'response_digest' IS DISTINCT FROM a.response_digest THEN RAISE EXCEPTION 'estimate_observation_required'; END IF;
 SELECT * INTO h FROM ops.provider_budget_holds WHERE attempt_id=a.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 SELECT * INTO b FROM ops.provider_budgets WHERE id=h.authorization_id FOR UPDATE;
 -- The quote validator froze identity; re-check it so no caller-supplied estimate can substitute a merchant.
 IF t.input_snapshot->'_execution'->'provider_accounts'->a.step->>'merchant_fingerprint' IS DISTINCT FROM p.merchant_fingerprint
 OR t.input_snapshot->'_execution'->'provider_accounts'->a.step->>'billing_group' IS DISTINCT FROM p.billing_group THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
 IF a.intent_at<p.valid_from OR a.intent_at>=p.valid_until THEN reason:='price_not_effective';
 ELSE
  IF p.provider='bfl' THEN
   raw:=observation.usage->'provider_cost';
   IF raw->>'unit'='BFL_CREDIT' AND raw->>'validation'='reported' AND jsonb_typeof(raw->'quantity')='string'
   AND raw->>'quantity' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,8})?$' THEN q:=jsonb_build_object('bfl_credit',(raw->>'quantity')::numeric); END IF;
  ELSE
   raw:=observation.usage->'usage';
   IF observation.usage->>'reported_model_version' IS DISTINCT FROM p.model THEN reason:='reported_model_unverified';
   ELSIF observation.usage->>'billing_usage_integrity'='complete' AND jsonb_typeof(raw)='object' THEN
    q:='{}'::jsonb;
    FOREACH field IN ARRAY ARRAY['prompt_token_count','cached_content_token_count','candidates_token_count','thoughts_token_count','tool_use_prompt_token_count','total_token_count'] LOOP
     IF jsonb_typeof(raw->field) IS DISTINCT FROM 'number' OR raw->>field !~ '^[0-9]+$' THEN q:=NULL; EXIT; END IF;
     IF (raw->>field)::numeric>1000000000000 THEN q:=NULL; EXIT; END IF;
    END LOOP;
    IF q IS NOT NULL THEN
     prompt:=(raw->>'prompt_token_count')::numeric; cached:=(raw->>'cached_content_token_count')::numeric;
     candidates:=(raw->>'candidates_token_count')::numeric; thoughts:=(raw->>'thoughts_token_count')::numeric;
     tools:=(raw->>'tool_use_prompt_token_count')::numeric; summed:=(raw->>'total_token_count')::numeric;
     IF cached>prompt OR tools<>0 OR summed<>prompt+candidates+thoughts+tools THEN q:=NULL;
     ELSE q:=jsonb_build_object('input_tokens',prompt-cached,'cached_input_tokens',cached,'output_tokens',candidates,'thinking_tokens',thoughts); END IF;
    END IF;
   END IF;
  END IF;
  IF q IS NOT NULL THEN
   names:=ARRAY(SELECT value->>'measure' FROM jsonb_array_elements(p.document->'rates'));
   IF (SELECT count(*) FROM jsonb_object_keys(q))<>cardinality(names) OR EXISTS(SELECT 1 FROM jsonb_object_keys(q) key WHERE NOT(key=ANY(names))) THEN
    reason:='incomplete_or_unpriced_usage'; q:=NULL;
   END IF;
  END IF;
  IF q IS NOT NULL THEN
   FOR rate IN SELECT value FROM jsonb_array_elements(p.document->'rates') LOOP
    quantity:=(q->>(rate->>'measure'))::numeric;
    term_scaled:=quantity*(rate->>'amount')::numeric*10000000000000000;
    per_units:=(rate->>'per')::numeric;
    numerator:=numerator*per_units+term_scaled*denominator; denominator:=denominator*per_units;
   END LOOP;
   scaled_amount:=div(numerator+denominator*100000000-1,denominator*100000000);
   amount:=scaled_amount/100000000;
   IF amount>=1000000000000 THEN reason:='estimate_overflow'; ELSE status:='estimated'; reason:='reviewed_table_estimate_not_invoice'; currency:=p.document->>'currency'; END IF;
  END IF;
 END IF;
 IF status='unknown' THEN amount:=NULL; currency:=NULL; END IF;
 SELECT l.ceiling INTO unit_cap FROM ops.provider_total_limits l WHERE l.authorization_id=b.id AND l.currency=h.currency;
 IF status='estimated' AND currency=h.currency AND unit_cap IS NOT NULL THEN
  SELECT COALESCE(sum(c.amount),0) INTO total FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e0 ON e0.cost_record_id=c.id
  WHERE c.user_id=t.user_id AND c.task_id=t.id AND c.cost_status='estimated' AND c.currency=h.currency;
  ceiling:=CASE WHEN amount>h.ceiling OR total+amount>unit_cap THEN 'over_ceiling' ELSE 'within_ceiling' END;
 END IF;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,usage,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,'estimate',a.provider,a.model,currency,amount,status,
 jsonb_build_object('price_id',p.id,'price_document_sha256',p.document_sha256,'evidence_kind',p.document->>'evidence_kind','response_digest',a.response_digest,'calculation_version','exact-rational-v1','quantities',q,'reason',reason,'ceiling_state',ceiling,'actual_spending_verified',false),
 'archived-supplier-price',p.version) RETURNING id INTO identity;
 INSERT INTO ops.supplier_cost_estimates(cost_record_id,user_id,task_id,attempt_id,response_observation_id,response_digest,price_id,price_document_sha256,ceiling_state)
 VALUES(identity,t.user_id,t.id,a.id,observation.id,a.response_digest,p.id,p.document_sha256,ceiling);
 IF ceiling<>'within_ceiling' THEN UPDATE ops.provider_budgets SET state='blocked' WHERE id=b.id AND state='open'; END IF;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'supplier_cost_estimated','task',t.id,'derived from frozen reviewed price and immutable supplier observation',p.document_sha256,
 jsonb_build_object('attempt_id',a.id,'cost_record_id',identity,'cost_status',status,'amount',(amount::numeric(20,8))::text,'currency',currency,'ceiling_state',ceiling,'actual_spending_verified',false),identity);
 RETURN jsonb_build_object('id',identity,'cost_status',status,'amount',(amount::numeric(20,8))::text,'currency',currency,'actual_spending_verified',false);
END $$;
ALTER FUNCTION app.record_supplier_estimate(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_supplier_estimate(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_supplier_estimate(uuid,text,bigint,uuid) TO app_worker;

CREATE OR REPLACE FUNCTION ops.provider_budget_snapshot(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ops.provider_budgets;
BEGIN
 SELECT * INTO b FROM ops.provider_budgets WHERE id=p_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',b.user_id::text,true);
 RETURN jsonb_build_object('authorization_id',b.id,'quote_id',b.quote_id,'scope_sha256',b.scope_sha256,'profile','offline_protocol_mock','schema_version',b.schema_version,'currency',b.currency,'state',b.state,'expires_at',b.expires_at,'total_ceiling',b.total_ceiling::text,
 'reserved_ceiling_total',CASE WHEN b.schema_version=2 THEN (SELECT COALESCE(sum(h.ceiling),0)::text FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id) ELSE NULL END,
 'actual_spending_verified',false,
 'known_estimate_subtotal',(SELECT sum(c.amount)::text FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e ON e.cost_record_id=c.id JOIN ops.provider_budget_holds h ON h.attempt_id=e.attempt_id WHERE h.authorization_id=b.id AND c.cost_status='estimated' AND c.currency=b.currency),
 'estimate_coverage_complete',EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step='generate')
 AND EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step='detect') AND NOT EXISTS(
 SELECT 1 FROM ops.provider_budget_holds h LEFT JOIN ops.supplier_cost_estimates e ON e.attempt_id=h.attempt_id LEFT JOIN app.task_cost_records c ON c.id=e.cost_record_id
 WHERE h.authorization_id=b.id AND (c.id IS NULL OR c.cost_status<>'estimated' OR c.currency IS DISTINCT FROM h.currency)),
 'blocked_for_cost_review',EXISTS(SELECT 1 FROM ops.supplier_cost_estimates e JOIN app.generation_tasks t ON t.id=e.task_id AND t.user_id=e.user_id WHERE t.quote_id=b.quote_id AND e.ceiling_state<>'within_ceiling'),
 'limits_by_currency',COALESCE((SELECT jsonb_agg(jsonb_build_object('currency',l.currency,'total_ceiling',l.ceiling::text,
 'reserved_ceiling_total',(SELECT COALESCE(sum(h.ceiling),0)::text FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id AND h.currency=l.currency),
 'known_estimate_subtotal',(SELECT sum(c.amount)::text FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e ON e.cost_record_id=c.id JOIN ops.provider_budget_holds h ON h.attempt_id=e.attempt_id WHERE h.authorization_id=b.id AND h.currency=l.currency AND c.currency=l.currency AND c.cost_status='estimated')) ORDER BY l.currency)
 FROM ops.provider_total_limits l WHERE l.authorization_id=b.id),'[]'::jsonb),
 'step_limits',COALESCE((SELECT jsonb_agg(jsonb_build_object('step',l.step,'currency',l.currency,'ceiling',l.ceiling::text) ORDER BY l.step) FROM ops.provider_step_limits l WHERE l.authorization_id=b.id),'[]'::jsonb),
 'holds',COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt_id',h.attempt_id,'step',h.step,'currency',h.currency,'ceiling',h.ceiling::text,'outcome',h.outcome) ORDER BY h.created_at,h.attempt_id) FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id),'[]'::jsonb));
END $$;
