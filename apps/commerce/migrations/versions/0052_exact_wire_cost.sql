-- Exact 24-decimal quantity times 8-decimal price uses integer 10^32 scaling.
-- Ceil at 8 decimals uses integer division by 10^24; no rounded division.
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
   AND raw->>'quantity' ~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,24})?$' THEN q:=jsonb_build_object('bfl_credit',(raw->>'quantity')::numeric); END IF;
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
    term_scaled:=quantity*(rate->>'amount')::numeric*100000000000000000000000000000000;
    per_units:=(rate->>'per')::numeric;
    numerator:=numerator*per_units+term_scaled*denominator; denominator:=denominator*per_units;
   END LOOP;
   scaled_amount:=div(numerator+denominator*1000000000000000000000000-1,denominator*1000000000000000000000000);
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
 jsonb_build_object('price_id',p.id,'price_document_sha256',p.document_sha256,'evidence_kind',p.document->>'evidence_kind','response_digest',a.response_digest,'calculation_version','exact-rational-24-decimal-v2','quantities',q,'reason',reason,'ceiling_state',ceiling,'actual_spending_verified',false),
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

