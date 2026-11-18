-- Qwen rehearsals remain offline_protocol_mock. No paid HTTP path is enabled.
ALTER TABLE ops.provider_budgets DROP CONSTRAINT provider_budgets_schema_version_check;
ALTER TABLE ops.provider_budgets ADD CONSTRAINT provider_budgets_schema_version_check CHECK(schema_version IN (2,3,4));
ALTER TABLE ops.provider_budgets DROP CONSTRAINT provider_budget_version_fields;
ALTER TABLE ops.provider_budgets ADD CONSTRAINT provider_budget_version_fields CHECK(
 (schema_version=2 AND currency IS NOT NULL AND generate_ceiling IS NOT NULL AND detect_ceiling IS NOT NULL AND total_ceiling IS NOT NULL)
 OR (schema_version IN (3,4) AND currency IS NULL AND generate_ceiling IS NULL AND detect_ceiling IS NULL AND total_ceiling IS NULL));
CREATE OR REPLACE FUNCTION ops.prepare_runtime_quote(p_user uuid,p_source uuid,p_id uuid,p_text text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q app.quotes; prior ops.audit_events; d jsonb; execution jsonb; entry jsonb; account jsonb;
 mutable text[]:=ARRAY['provider_profile','provider_model','price_version','provider_runtime_sha256','provider_models','provider_accounts','provider_endpoints'];
BEGIN
 IF p_user IS NULL OR p_source IS NULL OR p_id IS NULL OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_text IS NULL OR octet_length(p_text)>1048576 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_text,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 d:=p_text::jsonb; execution:=d->'_execution';
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR jsonb_typeof(execution) IS DISTINCT FROM 'object'
 OR NOT(execution ?& mutable) OR execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
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
CREATE OR REPLACE FUNCTION app.create_quote(p_id uuid,p_input_hash text,p_snapshot_hash text,p_snapshot jsonb,p_rules jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; product uuid; source uuid; appearance uuid; v app.asset_versions;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 IF u IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.users WHERE id=u AND status='active') THEN RAISE EXCEPTION 'inactive_user'; END IF;
 IF p_input_hash !~ '^[0-9a-f]{64}$' OR p_snapshot_hash !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_snapshot)<>'object' THEN RAISE EXCEPTION 'invalid_quote'; END IF;
 product:=(p_snapshot->>'product_id')::uuid;
 source:=(p_snapshot->>'source_asset_version_id')::uuid;
 appearance:=(p_snapshot->>'appearance_profile_id')::uuid;
 PERFORM 1 FROM app.products WHERE user_id=u AND id=product AND archived_at IS NULL FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'product_unavailable'; END IF;
 SELECT av.* INTO v FROM app.asset_versions av JOIN app.assets a ON a.user_id=av.user_id AND a.id=av.asset_id
 WHERE av.user_id=u AND av.product_id=product AND av.id=source AND av.canonical AND a.status='ready';
 IF NOT FOUND THEN RAISE EXCEPTION 'source_unavailable'; END IF;
 IF p_snapshot->'_execution'->>'source_sha256' IS DISTINCT FROM v.sha256 THEN RAISE EXCEPTION 'source_hash_mismatch'; END IF;
 IF appearance IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.appearance_profiles WHERE user_id=u AND product_id=product AND id=appearance AND archived_at IS NULL) THEN RAISE EXCEPTION 'appearance_unavailable'; END IF;
 IF COALESCE(p_snapshot->'_execution'->>'provider_profile','offline') NOT IN ('offline','offline_protocol_mock') THEN RAISE EXCEPTION 'unsupported_provider_profile'; END IF;
 INSERT INTO app.quotes(id,user_id,input_hash,input_sha256,input_snapshot,price_version,provider_model,rules_snapshot,credits,expires_at)
 VALUES(p_id,u,p_input_hash,p_snapshot_hash,p_snapshot,CASE WHEN p_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN 'trial-protocol-mock-v1' ELSE 'trial-fake-v1' END,CASE WHEN p_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN 'qwen-image-3.0-protocol-mock' ELSE 'flux-3-image-protocol-mock' END ELSE 'fake-v1' END,p_rules,16,now()+interval '10 minutes');
 RETURN p_id;
END $$;
CREATE OR REPLACE FUNCTION ops.register_unit_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
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
 IF q.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN RAISE EXCEPTION 'provider_budget_model_mismatch'; END IF;
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
CREATE FUNCTION ops.register_bailian_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
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
 OR q.input_snapshot->'_execution' ? 'provider_price_versions'
 OR d->'step_budgets'->'generate'->>'currency' IS DISTINCT FROM 'CNY'
 OR d->'step_budgets'->'detect'->>'currency' NOT IN ('USD','HUBWAY_BALANCE_UNIT')
 THEN RAISE EXCEPTION 'provider_budget_model_mismatch'; END IF;
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
CREATE OR REPLACE FUNCTION ops.register_provider_budget(p_id uuid,p_quote uuid,p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d jsonb; q app.quotes; prior ops.audit_events; field text; u uuid; starts timestamptz; finishes timestamptz; g numeric; v numeric; cap numeric;
BEGIN
 IF p_id IS NULL OR p_quote IS NULL OR p_operation IS NULL OR p_canonical IS NULL OR octet_length(p_canonical)>65536
 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_provider_budget'; END IF;
 d:=p_canonical::jsonb;
 IF d->>'schema_version'='4' THEN RETURN ops.register_bailian_provider_budget(p_id,p_quote,p_canonical,p_sha,p_operation,p_reason); END IF;
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
 IF q.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN RAISE EXCEPTION 'provider_budget_model_mismatch'; END IF;
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
 IF NOT FOUND THEN
  IF t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
  RETURN; END IF;
 IF (t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0') IS DISTINCT FROM (b.schema_version=4) THEN
  IF b.schema_version=4 OR t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 END IF;
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
CREATE OR REPLACE FUNCTION app.record_intent(p_task uuid,p_owner text,p_fence bigint,p_step text,p_request_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a uuid;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_step NOT IN ('generate','detect') OR p_request_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_intent'; END IF;
 IF (p_step='generate' AND t.phase<>'submitting') OR (p_step='detect' AND (t.phase<>'detecting' OR t.final_version_id IS NULL)) THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step=p_step) THEN RAISE EXCEPTION 'intent_exists' USING ERRCODE='55000'; END IF;
 INSERT INTO app.provider_attempts(user_id,task_id,step,provider,model,status,request_hash,local_request_id)
 VALUES(t.user_id,t.id,p_step,CASE WHEN t.input_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_step='generate' THEN CASE WHEN t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN 'bailian_mock' ELSE 'bfl_mock' END ELSE 'gemini_mock' END ELSE 'fake' END,CASE WHEN t.input_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_step='generate' THEN CASE WHEN t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN 'qwen-image-3.0' ELSE 'flux-3-image' END ELSE 'gemini-3.8-flash' END ELSE CASE WHEN p_step='generate' THEN t.input_snapshot->'_execution'->>'provider_model' ELSE 'fake-facts-v1' END END,'intent_recorded',p_request_hash,gen_random_uuid()) RETURNING id INTO a;
 PERFORM ops.reserve_provider_budget(t.id,a,p_step);
 RETURN a;
END $$;

ALTER FUNCTION app.record_intent(uuid,text,bigint,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_intent(uuid,text,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_intent(uuid,text,bigint,text,text) TO app_worker;
CREATE OR REPLACE FUNCTION app.record_protocol_observation(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid,p_stage text,p_usage jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; previous jsonb; saved ops.detection_archives; field text;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id;
 IF NOT FOUND OR a.provider NOT IN ('bfl_mock','gemini_mock','bailian_mock') THEN RAISE EXCEPTION 'protocol_attempt_required'; END IF;
 IF p_stage IS NULL OR p_stage NOT IN ('intent','response') OR p_usage IS NULL OR jsonb_typeof(p_usage)<>'object' OR octet_length(p_usage::text)>8192 THEN RAISE EXCEPTION 'invalid_observation'; END IF;
 IF p_stage='response' AND (a.provider_id IS NULL OR p_usage->>'response_digest' IS DISTINCT FROM a.response_digest) THEN RAISE EXCEPTION 'receipt_observation_mismatch'; END IF;
 IF p_stage='response' AND a.step='detect' THEN
  SELECT * INTO saved FROM ops.detection_archives WHERE attempt_id=a.id AND user_id=t.user_id AND task_id=t.id;
  IF NOT FOUND THEN RAISE EXCEPTION 'detection_archive_required'; END IF;
  FOREACH field IN ARRAY ARRAY['usage','billing_usage_integrity','reported_model_version','sdk_version','final_sha256'] LOOP
   IF COALESCE(p_usage->field,'null'::jsonb)::text IS DISTINCT FROM COALESCE(saved.document->field,'null'::jsonb)::text
   THEN RAISE EXCEPTION 'detection_observation_archive_mismatch'; END IF;
  END LOOP;
  IF saved.representation_version=2 THEN
   FOREACH field IN ARRAY ARRAY['archive_version','wire_response_sha256','response_structure_integrity'] LOOP
    IF (p_usage->field)::text IS DISTINCT FROM (saved.document->field)::text THEN RAISE EXCEPTION 'detection_observation_archive_mismatch'; END IF;
   END LOOP;
  END IF;
 END IF;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,usage,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,p_stage||'_observation',a.provider,a.model,NULL,NULL,'unknown',p_usage,'offline-protocol-mock','no-real-price')
 ON CONFLICT(attempt_id,cost_record_kind) DO NOTHING;
 SELECT usage INTO previous FROM app.task_cost_records WHERE attempt_id=a.id AND cost_record_kind=p_stage||'_observation';
 IF previous IS DISTINCT FROM p_usage THEN RAISE EXCEPTION 'observation_conflict'; END IF;
END $$;
ALTER FUNCTION app.record_protocol_observation(uuid,text,bigint,uuid,text,jsonb) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_protocol_observation(uuid,text,bigint,uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_protocol_observation(uuid,text,bigint,uuid,text,jsonb) TO app_worker;
