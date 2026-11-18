-- Delay Qwen estimates until the terminal query, and keep submit observations immutable.
ALTER TABLE ops.supplier_cost_estimates DROP CONSTRAINT supplier_cost_estimates_observation_kind_check;
ALTER TABLE ops.supplier_cost_estimates ADD CONSTRAINT supplier_cost_estimates_observation_kind_check CHECK(observation_kind IN ('response_observation','result_observation'));
ALTER TABLE ops.supplier_cost_estimates DROP CONSTRAINT supplier_cost_estimates_calculation_version_check;
ALTER TABLE ops.supplier_cost_estimates ADD CONSTRAINT supplier_cost_estimates_calculation_version_check CHECK(calculation_version IN ('exact-rational-v1','exact-rational-24-decimal-v2'));
ALTER TABLE ops.supplier_cost_estimates ALTER COLUMN calculation_version SET DEFAULT 'exact-rational-24-decimal-v2';
CREATE OR REPLACE FUNCTION ops.prepare_runtime_quote(p_user uuid,p_source uuid,p_id uuid,p_text text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q app.quotes; prior ops.audit_events; d jsonb; execution jsonb; entry jsonb; account jsonb;
 mutable text[]:=ARRAY['provider_profile','provider_model','price_version','provider_runtime_sha256','provider_models','provider_accounts','provider_endpoints','provider_price_versions'];
BEGIN
 IF p_user IS NULL OR p_source IS NULL OR p_id IS NULL OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_text IS NULL OR octet_length(p_text)>1048576 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_text,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 d:=p_text::jsonb; execution:=d->'_execution';
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR jsonb_typeof(execution) IS DISTINCT FROM 'object'
 OR NOT(execution ?& array_remove(mutable,'provider_price_versions')) OR execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
 OR execution->>'provider_model' IS DISTINCT FROM (execution->'provider_models'->>'generate')||'-protocol-mock'
 OR execution->>'price_version' IS DISTINCT FROM 'trial-protocol-mock-v1'
 OR jsonb_typeof(execution->'provider_runtime_sha256') IS DISTINCT FROM 'string'
 OR execution->>'provider_runtime_sha256' !~ '^[0-9a-f]{64}$'
 OR execution->'provider_models' NOT IN ('{"generate":"flux-3-image","detect":"gemini-3.8-flash"}'::jsonb,'{"generate":"qwen-image-3.0","detect":"gemini-3.8-flash"}'::jsonb) OR execution->'provider_models' IS NULL
 OR jsonb_typeof(execution->'provider_endpoints') IS DISTINCT FROM 'object'
 OR NOT(execution->'provider_endpoints' ?& ARRAY['generate','detect'])
 OR (SELECT count(*) FROM jsonb_object_keys(execution->'provider_endpoints'))<>2
 OR jsonb_typeof(execution->'provider_endpoints'->'generate') IS DISTINCT FROM 'string'
 OR (execution->'provider_models'->>'generate'='flux-3-image' AND execution->'provider_endpoints'->>'generate' IS DISTINCT FROM 'https://api.bfl.ai/v1/flux-3-image')
 OR (execution->'provider_models'->>'generate'='qwen-image-3.0' AND execution->'provider_endpoints'->>'generate' !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com/api/v1/services/aigc/image-generation/generation$')
 OR jsonb_typeof(execution->'provider_endpoints'->'detect') IS DISTINCT FROM 'string'
 OR execution->'provider_endpoints'->>'detect' NOT IN ('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent','https://hubway.cc/v1beta/models/gemini-3.8-flash:generateContent','https://z.hubway.cc/v1beta/models/gemini-3.8-flash:generateContent')
 OR jsonb_typeof(execution->'provider_accounts') IS DISTINCT FROM 'object'
 OR NOT(execution->'provider_accounts' ?& ARRAY['generate','detect'])
 OR (SELECT count(*) FROM jsonb_object_keys(execution->'provider_accounts'))<>2 THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 FOR entry IN SELECT value FROM jsonb_each(execution->'provider_accounts') LOOP
  account:=entry;
  IF jsonb_typeof(account) IS DISTINCT FROM 'object' OR NOT(account ?& ARRAY['merchant_fingerprint','billing_group'])
  OR (SELECT count(*) FROM jsonb_object_keys(account))<>2
  OR jsonb_typeof(account->'merchant_fingerprint') IS DISTINCT FROM 'string' OR account->>'merchant_fingerprint' !~ '^[0-9a-f]{64}$'
  OR jsonb_typeof(account->'billing_group') IS DISTINCT FROM 'string' OR length(account->>'billing_group') NOT BETWEEN 1 AND 160
  OR trim(account->>'billing_group')='' OR account->>'billing_group' ~ '[[:cntrl:]]' THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 END LOOP;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'runtime_quote_prepare' OR prior.target_id IS DISTINCT FROM p_id OR prior.reason IS DISTINCT FROM p_reason
  OR prior.evidence_digest IS DISTINCT FROM execution->>'provider_runtime_sha256'
  OR prior.before_state->>'source_quote_id' IS DISTINCT FROM p_source::text
  OR prior.after_state->>'user_id' IS DISTINCT FROM p_user::text
  OR prior.after_state->>'input_sha256' IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_id;
 END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO q FROM app.quotes WHERE id=p_source AND user_id=p_user FOR SHARE;
 IF NOT FOUND OR COALESCE(q.input_snapshot->'_execution'->>'provider_profile','offline')<>'offline'
 OR q.expires_at<=clock_timestamp() OR q.consumed_task_id IS NOT NULL THEN RAISE EXCEPTION 'runtime_quote_source_unavailable'; END IF;
 IF (d-'_execution') IS DISTINCT FROM (q.input_snapshot-'_execution')
 OR (execution-mutable) IS DISTINCT FROM ((q.input_snapshot->'_execution')-mutable)
 THEN RAISE EXCEPTION 'runtime_quote_business_mismatch'; END IF;
 PERFORM app.create_quote(p_id,q.input_hash,p_sha,d,q.rules_snapshot);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'runtime_quote_prepare','quote',p_id,p_reason,execution->>'provider_runtime_sha256',jsonb_build_object('source_quote_id',p_source),
 jsonb_build_object('user_id',p_user,'input_sha256',p_sha,'profile','offline_protocol_mock','paid_calls',0),p_operation);
 RETURN p_id;
END $$;
ALTER FUNCTION ops.prepare_runtime_quote(uuid,uuid,uuid,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.prepare_runtime_quote(uuid,uuid,uuid,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.prepare_runtime_quote(uuid,uuid,uuid,text,text,uuid,text) TO ops_admin;
CREATE OR REPLACE FUNCTION app.validate_supplier_price_scope() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE execution jsonb; entry record; p ops.supplier_price_versions; account jsonb; expected_endpoint text;
BEGIN
 execution:=NEW.input_snapshot->'_execution';
 IF (execution ? 'provider_price_versions') IS DISTINCT FROM true THEN RETURN NEW; END IF;
 IF execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
 OR jsonb_typeof(execution->'provider_price_versions') IS DISTINCT FROM 'object'
 OR execution->'provider_price_versions'='{}'::jsonb THEN RAISE EXCEPTION 'invalid_price_scope'; END IF;
 IF execution->'provider_models'->>'generate'='qwen-image-3.0' AND (
 NOT(execution->'provider_price_versions' ?& ARRAY['generate','detect']) OR (SELECT count(*) FROM jsonb_object_keys(execution->'provider_price_versions'))<>2) THEN RAISE EXCEPTION 'invalid_price_scope'; END IF;
 FOR entry IN SELECT * FROM jsonb_each(execution->'provider_price_versions') LOOP
  IF entry.key NOT IN ('generate','detect') OR jsonb_typeof(entry.value) IS DISTINCT FROM 'object'
  OR NOT(entry.value ?& ARRAY['id','document_sha256'])
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(entry.value) k WHERE k NOT IN ('id','document_sha256'))
  OR jsonb_typeof(entry.value->'id') IS DISTINCT FROM 'string'
  OR jsonb_typeof(entry.value->'document_sha256') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_price_scope'; END IF;
  SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(entry.value->>'id')::uuid AND document_sha256=entry.value->>'document_sha256';
  IF NOT FOUND OR now()<p.valid_from OR now()>=p.valid_until
  OR p.provider<>(CASE WHEN entry.key='generate' THEN CASE WHEN execution->'provider_models'->>'generate'='qwen-image-3.0' THEN 'aliyun_bailian' ELSE 'bfl' END ELSE 'gemini' END) THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
  account:=execution->'provider_accounts'->entry.key;
  IF jsonb_typeof(account) IS DISTINCT FROM 'object' OR NOT(account ?& ARRAY['merchant_fingerprint','billing_group'])
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(account) k WHERE k NOT IN ('merchant_fingerprint','billing_group'))
  OR jsonb_typeof(account->'merchant_fingerprint') IS DISTINCT FROM 'string' OR jsonb_typeof(account->'billing_group') IS DISTINCT FROM 'string'
  OR account->>'merchant_fingerprint' IS DISTINCT FROM p.merchant_fingerprint
  OR account->>'billing_group' IS DISTINCT FROM p.billing_group THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
  expected_endpoint:=p.endpoint_origin||CASE WHEN entry.key='generate' THEN CASE WHEN p.provider='aliyun_bailian' THEN '/api/v1/services/aigc/image-generation/generation' ELSE '/v1/flux-3-image' END ELSE '/v1beta/models/gemini-3.8-flash:generateContent' END;
  IF execution->'provider_endpoints'->>entry.key IS DISTINCT FROM expected_endpoint THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
ALTER FUNCTION app.validate_supplier_price_scope() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.validate_supplier_price_scope() FROM PUBLIC;
CREATE OR REPLACE FUNCTION ops.register_bailian_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; q app.quotes; prior ops.audit_events; field text; u uuid; starts timestamptz; finishes timestamptz; g numeric; v numeric; cap numeric; item jsonb; unit text; step text; price ops.supplier_price_versions; binding jsonb;
BEGIN
 IF p_id IS NULL OR p_quote IS NULL OR p_operation IS NULL OR p_canonical IS NULL OR octet_length(p_canonical)>65536
 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_provider_budget'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 IF NOT (d ?& ARRAY['schema_version','authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation','targets','generation_model','detection_model','max_generate_calls','max_detect_calls','step_budgets','currency_totals'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(d) k WHERE k NOT IN ('schema_version','authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation','targets','generation_model','detection_model','max_generate_calls','max_detect_calls','step_budgets','currency_totals')) THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 FOREACH field IN ARRAY ARRAY['authorization_id','user_id','product_id','source_asset_version_id','authorized_by','authorized_at','expires_at','source_path','source_sha256','quote_input_sha256','generation_model','detection_model'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 END LOOP;
 FOREACH field IN ARRAY ARRAY['schema_version','max_generate_calls','max_detect_calls'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'number' THEN RAISE EXCEPTION 'invalid_provider_scope'; END IF;
 END LOOP;
 IF d->>'schema_version' IS DISTINCT FROM '4' OR d->>'max_generate_calls' IS DISTINCT FROM '1' OR d->>'max_detect_calls' IS DISTINCT FROM '1'
 OR d->>'generation_model' IS DISTINCT FROM 'qwen-image-3.0' OR d->>'detection_model' IS DISTINCT FROM 'gemini-3.8-flash'
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
 IF q.input_snapshot->'_execution'->'provider_models' IS DISTINCT FROM '{"generate":"qwen-image-3.0","detect":"gemini-3.8-flash"}'::jsonb
 OR jsonb_typeof(q.input_snapshot->'_execution'->'provider_runtime_sha256') IS DISTINCT FROM 'string'

 OR d->'step_budgets'->'generate'->>'currency' IS DISTINCT FROM 'CNY'
 OR d->'step_budgets'->'detect'->>'currency' NOT IN ('USD','HUBWAY_BALANCE_UNIT')
 THEN RAISE EXCEPTION 'provider_budget_model_mismatch'; END IF;
 IF q.input_snapshot->'_execution' ? 'provider_price_versions' THEN
  IF jsonb_typeof(q.input_snapshot->'_execution'->'provider_price_versions') IS DISTINCT FROM 'object'
  OR NOT(q.input_snapshot->'_execution'->'provider_price_versions' ?& ARRAY['generate','detect'])
  OR (SELECT count(*) FROM jsonb_object_keys(q.input_snapshot->'_execution'->'provider_price_versions'))<>2 THEN RAISE EXCEPTION 'provider_budget_price_unit_mismatch'; END IF;
  FOREACH step IN ARRAY ARRAY['generate','detect'] LOOP
   binding:=q.input_snapshot->'_execution'->'provider_price_versions'->step;
   SELECT * INTO price FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
   IF NOT FOUND OR price.document->>'currency' IS DISTINCT FROM d->'step_budgets'->step->>'currency'
   OR price.provider IS DISTINCT FROM (CASE WHEN step='generate' THEN 'aliyun_bailian' ELSE 'gemini' END)
   OR clock_timestamp()<price.valid_from OR clock_timestamp()>=price.valid_until THEN RAISE EXCEPTION 'provider_budget_price_unit_mismatch'; END IF;
  END LOOP;
 END IF;
 PERFORM ops.assert_runtime_configuration(q.input_snapshot);
 IF EXISTS(SELECT 1 FROM ops.provider_budgets WHERE id=p_id OR quote_id=p_quote) THEN RAISE EXCEPTION 'provider_budget_already_registered'; END IF;
 INSERT INTO ops.provider_budgets(id,quote_id,user_id,input_sha256,scope_sha256,canonical_scope,scope_document,schema_version,currency,generate_ceiling,detect_ceiling,total_ceiling,authorized_at,expires_at,operation_id)
 VALUES(p_id,q.id,u,q.input_sha256,p_sha,p_canonical,d,4,NULL,NULL,NULL,NULL,starts,finishes,p_operation);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'provider_budget_registered','provider_budget',p_id,p_reason,p_sha,jsonb_build_object('quote_id',q.id,'profile','offline_protocol_mock','schema_version',4,'step_budgets',d->'step_budgets','currency_totals',d->'currency_totals'),p_operation);
 RETURN p_id;
END $$;
ALTER FUNCTION ops.register_bailian_provider_budget(uuid,uuid,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.register_bailian_provider_budget(uuid,uuid,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.register_bailian_provider_budget(uuid,uuid,text,text,uuid,text) TO ops_admin;
CREATE OR REPLACE FUNCTION app.record_supplier_estimate(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; p ops.supplier_price_versions; observation app.task_cost_records;
 existing app.task_cost_records; e ops.supplier_cost_estimates; b ops.provider_budgets; h ops.provider_budget_holds;
 binding jsonb; q jsonb; raw jsonb; rate jsonb; names text[]; field text; amount numeric:=0; quantity numeric;
 status text:='unknown'; reason text:='usage_incomplete'; currency text; ceiling text:='unknown'; identity uuid; total numeric; unit_cap numeric;
 numerator numeric:=0; denominator numeric:=1; term_scaled numeric; per_units numeric; scaled_amount numeric;
 prompt numeric; cached numeric; candidates numeric; thoughts numeric; tools numeric; summed numeric;
 result_archive ops.qwen_result_archives; observation_kind text:='response_observation';
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'attempt_not_found'; END IF;
 binding:=t.input_snapshot->'_execution'->'provider_price_versions'->a.step;
 IF binding IS NULL THEN RETURN NULL; END IF;
 IF a.provider='bailian_mock' THEN
  SELECT * INTO result_archive FROM ops.qwen_result_archives WHERE attempt_id=a.id AND task_id=t.id AND user_id=t.user_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  observation_kind:='result_observation';
 END IF;
 SELECT c.* INTO existing FROM app.task_cost_records c JOIN ops.supplier_cost_estimates e0 ON e0.cost_record_id=c.id WHERE e0.attempt_id=a.id AND e0.user_id=t.user_id;
 IF FOUND THEN RETURN jsonb_build_object('id',existing.id,'cost_status',existing.cost_status,'amount',existing.amount::text,'currency',existing.currency,'actual_spending_verified',false); END IF;
 IF a.provider_id IS NULL OR a.response_digest IS NULL THEN RAISE EXCEPTION 'estimate_receipt_required'; END IF;
 SELECT * INTO p FROM ops.supplier_price_versions WHERE id=(binding->>'id')::uuid AND document_sha256=binding->>'document_sha256';
 IF NOT FOUND OR p.model IS DISTINCT FROM a.model OR p.provider IS DISTINCT FROM (CASE WHEN a.step='generate' THEN CASE WHEN a.provider='bailian_mock' THEN 'aliyun_bailian' ELSE 'bfl' END ELSE 'gemini' END)
 THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
 SELECT * INTO observation FROM app.task_cost_records WHERE user_id=t.user_id AND task_id=t.id AND attempt_id=a.id AND cost_record_kind=observation_kind;
 IF NOT FOUND OR observation.usage->>'response_digest' IS DISTINCT FROM a.response_digest THEN RAISE EXCEPTION 'estimate_observation_required'; END IF;
 SELECT * INTO h FROM ops.provider_budget_holds WHERE attempt_id=a.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 SELECT * INTO b FROM ops.provider_budgets WHERE id=h.authorization_id FOR UPDATE;
 -- The quote validator froze identity; re-check it so no caller-supplied estimate can substitute a merchant.
 IF t.input_snapshot->'_execution'->'provider_accounts'->a.step->>'merchant_fingerprint' IS DISTINCT FROM p.merchant_fingerprint
 OR t.input_snapshot->'_execution'->'provider_accounts'->a.step->>'billing_group' IS DISTINCT FROM p.billing_group THEN RAISE EXCEPTION 'price_scope_mismatch'; END IF;
 IF a.intent_at<p.valid_from OR a.intent_at>=p.valid_until THEN reason:='price_not_effective';
 ELSE
  IF p.provider='aliyun_bailian' THEN
   IF observation.usage->>'qwen_result_archive_id' IS DISTINCT FROM result_archive.id::text
   OR observation.usage->>'wire_response_sha256' IS DISTINCT FROM result_archive.document->>'wire_response_sha256'
   OR observation.usage->>'generated_sha256' IS DISTINCT FROM result_archive.document->>'generated_sha256'
   OR observation.usage->'quantities' IS DISTINCT FROM result_archive.document->'quantities'
   OR observation.usage->>'billing_usage_integrity' IS DISTINCT FROM result_archive.document->>'billing_usage_integrity'
   THEN RAISE EXCEPTION 'qwen_result_observation_mismatch'; END IF;
   IF result_archive.document->>'billing_usage_integrity'='complete' THEN q:=result_archive.document->'quantities'; END IF;
  ELSIF p.provider='bfl' THEN
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
 INSERT INTO ops.supplier_cost_estimates(cost_record_id,user_id,task_id,attempt_id,response_observation_id,response_digest,price_id,price_document_sha256,ceiling_state,observation_kind,calculation_version)
 VALUES(identity,t.user_id,t.id,a.id,observation.id,a.response_digest,p.id,p.document_sha256,ceiling,observation_kind,'exact-rational-24-decimal-v2');
 IF ceiling<>'within_ceiling' THEN UPDATE ops.provider_budgets SET state='blocked' WHERE id=b.id AND state='open'; END IF;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'supplier_cost_estimated','task',t.id,'derived from frozen reviewed price and immutable supplier observation',p.document_sha256,
 jsonb_build_object('attempt_id',a.id,'cost_record_id',identity,'cost_status',status,'amount',(amount::numeric(20,8))::text,'currency',currency,'ceiling_state',ceiling,'actual_spending_verified',false),identity);
 RETURN jsonb_build_object('id',identity,'cost_status',status,'amount',(amount::numeric(20,8))::text,'currency',currency,'actual_spending_verified',false);
END $$;
ALTER FUNCTION app.record_supplier_estimate(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_supplier_estimate(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_supplier_estimate(uuid,text,bigint,uuid) TO app_worker;
