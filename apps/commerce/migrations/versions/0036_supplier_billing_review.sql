-- Private read-only preflight context. No invoice confirmation or financial mutation.
CREATE FUNCTION ops.supplier_billing_context(p_task uuid,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; p ops.supplier_price_versions; h ops.provider_budget_holds;
 binding jsonb; account jsonb; origin text; request_id text; estimate jsonb; receipt_ok boolean;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u;
 IF NOT FOUND OR a.provider_id IS NULL OR a.response_digest IS NULL OR a.provider NOT IN ('bfl_mock','gemini_mock') THEN RAISE EXCEPTION 'bill_known_protocol_receipt_required'; END IF;
 SELECT * INTO h FROM ops.provider_budget_holds WHERE attempt_id=a.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'bill_budget_required'; END IF;
 binding:=t.input_snapshot->'_execution'->'provider_price_versions'->a.step;
 SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
 IF NOT FOUND THEN RAISE EXCEPTION 'bill_frozen_price_required'; END IF;
 account:=t.input_snapshot->'_execution'->'provider_accounts'->a.step;
 origin:=CASE WHEN a.step='generate' THEN split_part(t.input_snapshot->'_execution'->'provider_endpoints'->>'generate','/v1/',1)
 ELSE split_part(t.input_snapshot->'_execution'->'provider_endpoints'->>'detect','/v1beta/',1) END;
 request_id:=a.provider_id;
 IF a.step='detect' THEN SELECT document->>'provider_response_id' INTO request_id FROM ops.detection_archives WHERE attempt_id=a.id AND user_id=u AND task_id=t.id; END IF;
 SELECT jsonb_build_object('cost_status',c.cost_status,'amount',c.amount::text,'currency',c.currency) INTO estimate
 FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e ON e.cost_record_id=c.id WHERE e.attempt_id=a.id AND e.user_id=u AND e.task_id=t.id;
 receipt_ok:=EXISTS(SELECT 1 FROM ops.provider_receipts WHERE user_id=u AND task_id=t.id AND attempt_id=a.id AND provider_id=a.provider_id AND response_digest=a.response_digest);
 RETURN jsonb_build_object('profile',t.input_snapshot->'_execution'->>'provider_profile','receipt_applied',receipt_ok,
 'price_scope_verified',p.merchant_fingerprint IS NOT DISTINCT FROM account->>'merchant_fingerprint'
 AND p.billing_group IS NOT DISTINCT FROM account->>'billing_group' AND p.document->>'currency' IS NOT DISTINCT FROM h.currency
 AND p.model=a.model AND p.endpoint_origin IS NOT DISTINCT FROM origin,
 'scope',jsonb_build_object('task_id',t.id,'attempt_id',a.id,'quote_input_sha256',t.input_sha256,'provider',p.provider,'model',a.model,
 'endpoint_origin',origin,'merchant_fingerprint',p.merchant_fingerprint,'billing_group',p.billing_group,
 'provider_request_id',request_id,'response_digest',a.response_digest,'currency',h.currency),
 'step_ceiling',h.ceiling::text,'estimate',estimate,'actual_spending_verified',false);
END $$;
ALTER FUNCTION ops.supplier_billing_context(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.supplier_billing_context(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.supplier_billing_context(uuid,uuid) TO ops_admin;
