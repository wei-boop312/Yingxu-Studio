ALTER TABLE ops.supplier_price_versions ADD CONSTRAINT supplier_price_digest_unique UNIQUE(id,document_sha256);
ALTER TABLE app.task_cost_records ADD CONSTRAINT cost_record_owned_kind UNIQUE(user_id,task_id,attempt_id,id,cost_record_kind);
CREATE TABLE ops.supplier_cost_estimates (
 cost_record_id uuid PRIMARY KEY, user_id uuid NOT NULL, task_id uuid NOT NULL, attempt_id uuid NOT NULL UNIQUE,
 cost_kind text NOT NULL DEFAULT 'estimate' CHECK(cost_kind='estimate'),
 response_observation_id uuid NOT NULL, observation_kind text NOT NULL DEFAULT 'response_observation' CHECK(observation_kind='response_observation'),
 response_digest text NOT NULL CHECK(response_digest ~ '^[0-9a-f]{64}$'),
 price_id uuid NOT NULL, price_document_sha256 text NOT NULL,
 ceiling_state text NOT NULL CHECK(ceiling_state IN ('within_ceiling','over_ceiling','unknown')),
 calculation_version text NOT NULL DEFAULT 'exact-rational-v1' CHECK(calculation_version='exact-rational-v1'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_id,task_id,attempt_id,cost_record_id,cost_kind) REFERENCES app.task_cost_records(user_id,task_id,attempt_id,id,cost_record_kind),
 FOREIGN KEY(user_id,task_id,attempt_id,response_observation_id,observation_kind) REFERENCES app.task_cost_records(user_id,task_id,attempt_id,id,cost_record_kind),
 FOREIGN KEY(price_id,price_document_sha256) REFERENCES ops.supplier_price_versions(id,document_sha256)
);
ALTER TABLE ops.supplier_cost_estimates ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.supplier_cost_estimates FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.supplier_cost_estimates FROM PUBLIC;
GRANT SELECT,INSERT ON ops.supplier_cost_estimates TO app_executor;
GRANT SELECT ON ops.supplier_cost_estimates TO ops_admin;
CREATE POLICY supplier_estimate_executor ON ops.supplier_cost_estimates TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY supplier_estimate_operator ON ops.supplier_cost_estimates FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_supplier_estimate BEFORE UPDATE OR DELETE ON ops.supplier_cost_estimates FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION app.record_supplier_estimate(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; p ops.supplier_price_versions; observation app.task_cost_records;
 existing app.task_cost_records; e ops.supplier_cost_estimates; b ops.provider_budgets; h ops.provider_budget_holds;
 binding jsonb; q jsonb; raw jsonb; rate jsonb; names text[]; field text; amount numeric:=0; quantity numeric;
 status text:='unknown'; reason text:='usage_incomplete'; currency text; ceiling text:='unknown'; identity uuid; total numeric;
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
 IF status='estimated' AND currency=b.currency THEN
  SELECT COALESCE(sum(c.amount),0) INTO total FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e0 ON e0.cost_record_id=c.id
  WHERE c.user_id=t.user_id AND c.task_id=t.id AND c.cost_status='estimated' AND c.currency=b.currency;
  ceiling:=CASE WHEN amount>h.ceiling OR total+amount>b.total_ceiling THEN 'over_ceiling' ELSE 'within_ceiling' END;
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

CREATE FUNCTION app.guard_supplier_price_intent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; p ops.supplier_price_versions; binding jsonb;
BEGIN
 SELECT * INTO t FROM app.generation_tasks WHERE id=NEW.task_id AND user_id=NEW.user_id;
 IF EXISTS(SELECT 1 FROM ops.supplier_cost_estimates WHERE task_id=t.id AND user_id=t.user_id AND ceiling_state<>'within_ceiling')
 THEN RAISE EXCEPTION 'provider_budget_cost_review_required'; END IF;
 binding:=t.input_snapshot->'_execution'->'provider_price_versions'->NEW.step;
 IF binding IS NOT NULL THEN
  SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
  IF NOT FOUND OR clock_timestamp()<p.valid_from OR clock_timestamp()>=p.valid_until THEN RAISE EXCEPTION 'provider_price_expired'; END IF;
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION app.guard_supplier_price_intent() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.guard_supplier_price_intent() FROM PUBLIC;
CREATE TRIGGER supplier_price_intent_guard BEFORE INSERT ON app.provider_attempts FOR EACH ROW EXECUTE FUNCTION app.guard_supplier_price_intent();

CREATE FUNCTION ops.guard_supplier_estimate_reopen() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.state='open' AND OLD.state<>'open' THEN
  PERFORM set_config('app.user_id',NEW.user_id::text,true);
  IF EXISTS(SELECT 1 FROM ops.supplier_cost_estimates e JOIN app.generation_tasks t ON t.id=e.task_id AND t.user_id=e.user_id
  WHERE t.quote_id=NEW.quote_id AND e.ceiling_state<>'within_ceiling') THEN RAISE EXCEPTION 'supplier_cost_review_cannot_reopen'; END IF;
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION ops.guard_supplier_estimate_reopen() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.guard_supplier_estimate_reopen() FROM PUBLIC;
CREATE TRIGGER supplier_estimate_reopen_guard BEFORE UPDATE OF state ON ops.provider_budgets FOR EACH ROW EXECUTE FUNCTION ops.guard_supplier_estimate_reopen();

CREATE FUNCTION ops.supplier_task_costs(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 RETURN jsonb_build_object('task_id',p_task,'actual_spending_verified',false,'estimates',COALESCE((
 SELECT jsonb_agg(jsonb_build_object('id',c.id,'attempt_id',e.attempt_id,'price_id',e.price_id,'price_document_sha256',e.price_document_sha256,'cost_status',c.cost_status,'amount',c.amount::text,'currency',c.currency,'ceiling_state',e.ceiling_state,'calculation_version',e.calculation_version,'reason',c.usage->>'reason','evidence_kind',c.usage->>'evidence_kind'))
 FROM ops.supplier_cost_estimates e JOIN app.task_cost_records c ON c.id=e.cost_record_id WHERE e.task_id=p_task AND e.user_id=u),'[]'::jsonb));
END $$;
ALTER FUNCTION ops.supplier_task_costs(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.supplier_task_costs(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.supplier_task_costs(uuid) TO ops_admin;

-- Existing API and owner remain; aggregate estimates without claiming actual billing.
CREATE OR REPLACE FUNCTION ops.provider_budget_snapshot(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ops.provider_budgets;
BEGIN
 SELECT * INTO b FROM ops.provider_budgets WHERE id=p_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',b.user_id::text,true);
 RETURN jsonb_build_object('authorization_id',b.id,'quote_id',b.quote_id,'scope_sha256',b.scope_sha256,'profile','offline_protocol_mock','currency',b.currency,'state',b.state,'expires_at',b.expires_at,'total_ceiling',b.total_ceiling::text,
 'reserved_ceiling_total',(SELECT COALESCE(sum(h.ceiling),0)::text FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id),
 'actual_spending_verified',false,
 'known_estimate_subtotal',(SELECT sum(c.amount)::text FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e ON e.cost_record_id=c.id JOIN ops.provider_budget_holds h ON h.attempt_id=e.attempt_id WHERE h.authorization_id=b.id AND c.cost_status='estimated' AND c.currency=b.currency),
 'estimate_coverage_complete',EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step='generate')
 AND EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step='detect') AND NOT EXISTS(
 SELECT 1 FROM ops.provider_budget_holds h LEFT JOIN ops.supplier_cost_estimates e ON e.attempt_id=h.attempt_id LEFT JOIN app.task_cost_records c ON c.id=e.cost_record_id
 WHERE h.authorization_id=b.id AND (c.id IS NULL OR c.cost_status<>'estimated' OR c.currency IS DISTINCT FROM b.currency)),
 'blocked_for_cost_review',EXISTS(SELECT 1 FROM ops.supplier_cost_estimates e JOIN app.generation_tasks t ON t.id=e.task_id AND t.user_id=e.user_id WHERE t.quote_id=b.quote_id AND e.ceiling_state<>'within_ceiling'),
 'holds',COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt_id',h.attempt_id,'step',h.step,'ceiling',h.ceiling::text,'outcome',h.outcome) ORDER BY h.created_at,h.attempt_id) FROM ops.provider_budget_holds h WHERE h.authorization_id=b.id),'[]'::jsonb));
END $$;
