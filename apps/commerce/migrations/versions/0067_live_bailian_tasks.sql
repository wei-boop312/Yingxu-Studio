-- Explicit user-authorized live tasks; old mock and Fake paths remain separate.
CREATE FUNCTION ops.activate_live_provider(p_user uuid,p_runtime_sha text,p_rates jsonb,p_evidence jsonb,p_enabled boolean,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ops.provider_runtime_versions; binding jsonb; value jsonb; field text; prior ops.audit_events;
BEGIN
 IF NOT pg_has_role(session_user,'ops_admin','member') THEN RAISE EXCEPTION 'operator_required'; END IF;
 IF p_user IS NULL OR p_operation IS NULL OR p_enabled IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'invalid_live_policy'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 IF NOT EXISTS(SELECT 1 FROM app.users WHERE id=p_user AND status='active') THEN RAISE EXCEPTION 'inactive_user'; END IF;
 SELECT * INTO r FROM ops.provider_runtime_versions WHERE document_sha256=p_runtime_sha;
 IF NOT FOUND OR r.document->>'schema_version'<>'3' THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 binding:=ops.validated_runtime_binding(r.canonical_document,r.document_sha256);
 IF jsonb_typeof(p_rates) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(p_rates))<>2 OR NOT(p_rates ?& ARRAY['generate','detect'])
 OR jsonb_typeof(p_rates->'generate') IS DISTINCT FROM 'object' OR jsonb_typeof(p_rates->'detect') IS DISTINCT FROM 'object'
 OR NOT(p_rates->'generate' ?& ARRAY['qima_input_1k','qima_input_2k','qima_output_1k','qima_output_2k']) OR (SELECT count(*) FROM jsonb_object_keys(p_rates->'generate'))<>4
 OR NOT(p_rates->'detect' ?& ARRAY['input_token','output_token','input_token_cache']) OR (SELECT count(*) FROM jsonb_object_keys(p_rates->'detect'))<>3
 THEN RAISE EXCEPTION 'invalid_live_reference_rates'; END IF;
 FOR value IN SELECT j.value FROM jsonb_each(p_rates->'generate') j UNION ALL SELECT j.value FROM jsonb_each(p_rates->'detect') j LOOP
  IF jsonb_typeof(value) IS DISTINCT FROM 'string' OR value#>>'{}' !~ '^(0|[1-9][0-9]{0,5})([.][0-9]{1,8})?$' THEN RAISE EXCEPTION 'invalid_live_reference_rates'; END IF;
 END LOOP;
 IF jsonb_typeof(p_evidence) IS DISTINCT FROM 'object' OR NOT(p_evidence ?& ARRAY['generate','detect']) OR (SELECT count(*) FROM jsonb_object_keys(p_evidence))<>2 THEN RAISE EXCEPTION 'invalid_live_price_evidence'; END IF;
 FOREACH field IN ARRAY ARRAY['generate','detect'] LOOP
  IF jsonb_typeof(p_evidence->field) IS DISTINCT FROM 'string' OR p_evidence->>field !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_live_price_evidence'; END IF;
 END LOOP;
 PERFORM pg_advisory_xact_lock(hashtextextended('live-policy:'||p_user::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'live_provider_policy' OR prior.target_id<>p_user OR prior.evidence_digest<>p_runtime_sha OR prior.after_state->'enabled' IS DISTINCT FROM to_jsonb(p_enabled) OR prior.after_state->'reference_rates' IS DISTINCT FROM p_rates OR prior.after_state->'price_evidence_sha256' IS DISTINCT FROM p_evidence THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_operation;
 END IF;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id,created_at)
 VALUES(session_user,'live_provider_policy','user',p_user,p_reason,p_runtime_sha,jsonb_build_object('enabled',p_enabled,'runtime_sha256',p_runtime_sha,'execution_binding',binding,'reference_rates',p_rates,'price_evidence_sha256',p_evidence,'currency','CNY','spending_limit_cny',NULL,'authorization_ref','user_20261115_no_spending_limit'),p_operation,clock_timestamp());
 RETURN p_operation;
END $$;

CREATE FUNCTION ops.live_policy(p_user uuid) RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT after_state FROM ops.audit_events WHERE action='live_provider_policy' AND target_type='user' AND target_id=p_user ORDER BY created_at DESC,id DESC LIMIT 1 $$;

CREATE FUNCTION ops.assert_live_snapshot(p_snapshot jsonb,p_user uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy jsonb; e jsonb;
BEGIN
 e:=p_snapshot->'_execution';policy:=ops.live_policy(p_user);
 IF policy IS NULL OR policy->'enabled' IS DISTINCT FROM 'true'::jsonb OR e->>'provider_runtime_sha256' IS DISTINCT FROM policy->>'runtime_sha256'
 OR e->'live_reference_rates' IS DISTINCT FROM policy->'reference_rates' OR e->'live_price_evidence_sha256' IS DISTINCT FROM policy->'price_evidence_sha256'
 THEN RAISE EXCEPTION 'live_provider_not_authorized'; END IF;
END $$;

CREATE FUNCTION app.live_provider_configuration() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; policy jsonb; r ops.provider_runtime_versions;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;IF u IS NULL THEN RAISE EXCEPTION 'identity_required'; END IF;
 policy:=ops.live_policy(u);
 IF policy IS NULL OR policy->'enabled' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'live_provider_not_authorized'; END IF;
 SELECT * INTO r FROM ops.provider_runtime_versions WHERE document_sha256=policy->>'runtime_sha256';
 IF NOT FOUND OR EXISTS(SELECT 1 FROM ops.provider_runtime_revocations WHERE runtime_sha256=r.document_sha256) THEN RAISE EXCEPTION 'provider_runtime_revoked'; END IF;
 RETURN jsonb_build_object('binding',ops.validated_runtime_binding(r.canonical_document,r.document_sha256),'reference_rates',policy->'reference_rates','price_evidence_sha256',policy->'price_evidence_sha256','currency','CNY','spending_limit_cny',NULL);
END $$;

CREATE OR REPLACE FUNCTION ops.assert_runtime_configuration(p_snapshot jsonb)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE execution jsonb; stored ops.provider_runtime_versions; binding jsonb; field text;
BEGIN
 execution:=p_snapshot->'_execution';
 IF execution IS NULL OR NOT COALESCE(execution ? 'provider_runtime_sha256',false) THEN RETURN; END IF;
 IF jsonb_typeof(execution->'provider_runtime_sha256') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('provider-runtime:'||(execution->>'provider_runtime_sha256'),0));
 SELECT * INTO stored FROM ops.provider_runtime_versions WHERE document_sha256=execution->>'provider_runtime_sha256';
 IF NOT FOUND THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 IF EXISTS(SELECT 1 FROM ops.provider_runtime_revocations WHERE runtime_sha256=stored.document_sha256) THEN RAISE EXCEPTION 'provider_runtime_revoked'; END IF;
 binding:=ops.validated_runtime_binding(stored.canonical_document,stored.document_sha256);
 IF execution->>'provider_profile' NOT IN ('offline_protocol_mock','bailian_live') OR execution->>'provider_profile' IS NULL THEN RAISE EXCEPTION 'runtime_profile_not_enabled'; END IF;
 FOREACH field IN ARRAY ARRAY['provider_runtime_sha256','provider_models','provider_endpoints','provider_accounts'] LOOP
  IF execution->field IS DISTINCT FROM binding->field THEN RAISE EXCEPTION 'registered_runtime_binding_mismatch'; END IF;
 END LOOP;
 IF execution->>'provider_profile'='bailian_live' THEN PERFORM ops.assert_live_snapshot(p_snapshot,NULLIF(current_setting('app.user_id',true),'')::uuid); END IF;
END $function$;

CREATE OR REPLACE FUNCTION app.create_quote(p_id uuid, p_input_hash text, p_snapshot_hash text, p_snapshot jsonb, p_rules jsonb)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
 IF COALESCE(p_snapshot->'_execution'->>'provider_profile','offline') NOT IN ('offline','offline_protocol_mock','bailian_live') THEN RAISE EXCEPTION 'unsupported_provider_profile'; END IF;
 IF p_snapshot->'_execution'->>'provider_profile'='bailian_live' THEN PERFORM ops.assert_runtime_configuration(p_snapshot); END IF;
 INSERT INTO app.quotes(id,user_id,input_hash,input_sha256,input_snapshot,price_version,provider_model,rules_snapshot,credits,expires_at)
 VALUES(p_id,u,p_input_hash,p_snapshot_hash,p_snapshot,CASE WHEN p_snapshot->'_execution'->>'provider_profile'='bailian_live' THEN 'bailian-live-reference-v1' WHEN p_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN 'trial-protocol-mock-v1' ELSE 'trial-fake-v1' END,CASE WHEN p_snapshot->'_execution'->>'provider_profile'='bailian_live' THEN 'qwen-image-3.0' WHEN p_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN 'qwen-image-3.0-protocol-mock' ELSE 'flux-3-image-protocol-mock' END ELSE 'fake-v1' END,p_rules,16,now()+interval '10 minutes');
 RETURN p_id;
END $function$;

CREATE OR REPLACE FUNCTION ops.claim_task(p_task uuid, p_owner text, p_profile text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE u uuid; t app.generation_tasks;
BEGIN
 IF p_profile IS NULL OR p_profile NOT IN ('offline','offline_protocol_mock','bailian_live') THEN RAISE EXCEPTION 'invalid_worker_profile'; END IF;
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_owner'; END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task AND state<>'manual';
 IF u IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF COALESCE(t.input_snapshot->'_execution'->>'provider_profile','offline') IS DISTINCT FROM p_profile THEN RETURN NULL; END IF;
 IF t.status IN ('succeeded','failed','cancelled','uncertain') OR (t.lease_until IS NOT NULL AND t.lease_until>clock_timestamp()) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts a WHERE a.task_id=t.id AND a.provider_id IS NULL AND a.status IN ('intent_recorded','uncertain')
 AND NOT(a.step='detect' AND t.phase IN ('validating_rules','finalizing')
 AND EXISTS(SELECT 1 FROM ops.detection_manual_deliveries d JOIN app.asset_versions v ON v.id=d.final_version_id AND v.user_id=d.user_id
 WHERE d.task_id=t.id AND d.user_id=t.user_id AND d.attempt_id=a.id AND d.final_version_id=t.final_version_id AND d.final_sha256=v.sha256))) THEN
  UPDATE app.generation_tasks SET status='uncertain',lease_owner=NULL,lease_until=NULL,revision=revision+1,updated_at=now(),error_code='submission_outcome_unknown' WHERE id=t.id;
  UPDATE ops.task_dispatch SET state='uncertain',lease_until=NULL WHERE task_id=t.id;
  INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','uncertain','revision',t.revision+1));
  RETURN jsonb_build_object('id',t.id,'status','uncertain');
 END IF;
 UPDATE app.generation_tasks SET status='processing',phase=CASE WHEN status='queued' THEN 'submitting' ELSE phase END,
  lease_owner=p_owner,lease_until=clock_timestamp()+interval '60 seconds',fencing_token=fencing_token+1,revision=revision+1,updated_at=now()
 WHERE id=t.id RETURNING * INTO t;
 UPDATE ops.task_dispatch SET state='processing',lease_until=t.lease_until,fencing_token=t.fencing_token WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status',t.status,'phase',t.phase,'revision',t.revision));
 RETURN jsonb_build_object('id',t.id,'status',t.status,'phase',t.phase,'fencing_token',t.fencing_token);
END $function$;

CREATE FUNCTION ops.task_provider_profile(p_task uuid) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; result text;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;IF u IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT COALESCE(input_snapshot->'_execution'->>'provider_profile','offline') INTO result FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION ops.validate_delivery(p_outbox uuid, p_task uuid, p_profile text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE u uuid;
BEGIN
 SELECT user_id INTO u FROM ops.outbox WHERE id=p_outbox AND task_id=p_task;
 IF u IS NULL THEN RETURN false; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 RETURN EXISTS(SELECT 1 FROM ops.outbox o JOIN ops.task_dispatch d ON d.task_id=o.task_id AND d.user_id=o.user_id
 JOIN app.generation_tasks t ON t.id=d.task_id AND t.user_id=d.user_id WHERE o.id=p_outbox AND o.task_id=p_task AND d.state<>'manual'
 AND o.event_type<>'task.manual' AND p_profile='bailian_live' AND COALESCE(t.input_snapshot->'_execution'->>'provider_profile','offline') IN ('offline','bailian_live'));
END $function$;

CREATE OR REPLACE FUNCTION app.record_intent(p_task uuid, p_owner text, p_fence bigint, p_step text, p_request_hash text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE t app.generation_tasks; a uuid;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_step NOT IN ('generate','detect') OR p_request_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_intent'; END IF;
 IF (p_step='generate' AND t.phase<>'submitting') OR (p_step='detect' AND (t.phase<>'detecting' OR t.final_version_id IS NULL)) THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step=p_step) THEN RAISE EXCEPTION 'intent_exists' USING ERRCODE='55000'; END IF;
 IF t.input_snapshot->'_execution'->>'provider_profile'='bailian_live' THEN
  PERFORM ops.assert_runtime_configuration(t.input_snapshot);
  INSERT INTO app.provider_attempts(user_id,task_id,step,provider,model,status,request_hash,local_request_id)
  VALUES(t.user_id,t.id,p_step,CASE WHEN p_step='generate' THEN 'bailian_live' ELSE 'bailian_vision_live' END,t.input_snapshot->'_execution'->'provider_models'->>p_step,'intent_recorded',p_request_hash,gen_random_uuid()) RETURNING id INTO a;
  RETURN a;
 END IF;
 INSERT INTO app.provider_attempts(user_id,task_id,step,provider,model,status,request_hash,local_request_id)
 VALUES(t.user_id,t.id,p_step,CASE WHEN t.input_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_step='generate' THEN CASE WHEN t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN 'bailian_mock' ELSE 'bfl_mock' END ELSE 'gemini_mock' END ELSE 'fake' END,CASE WHEN t.input_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN CASE WHEN p_step='generate' THEN CASE WHEN t.input_snapshot->'_execution'->'provider_models'->>'generate'='qwen-image-3.0' THEN 'qwen-image-3.0' ELSE 'flux-3-image' END ELSE 'gemini-3.8-flash' END ELSE CASE WHEN p_step='generate' THEN t.input_snapshot->'_execution'->>'provider_model' ELSE 'fake-facts-v1' END END,'intent_recorded',p_request_hash,gen_random_uuid()) RETURNING id INTO a;
 PERFORM ops.reserve_provider_budget(t.id,a,p_step);
 RETURN a;
END $function$;

CREATE OR REPLACE FUNCTION app.append_generation_receipt(p_task uuid, p_attempt uuid, p_provider_id text, p_digest text, p_canonical text, p_sha text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; d jsonb; raw bytea; body jsonb;
 field text; endpoint text; identity uuid; existing ops.generation_archives;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u;
 IF NOT FOUND OR a.step<>'generate' OR a.provider NOT IN ('bfl_mock','bailian_mock','bailian_live')
 OR COALESCE(t.input_snapshot->'_execution'->>'provider_profile','') NOT IN ('offline_protocol_mock','bailian_live')
 THEN RAISE EXCEPTION 'generation_archive_scope_mismatch'; END IF;
 IF p_provider_id IS NULL OR p_provider_id !~ '^[A-Za-z0-9_-]{1,200}$'
 OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$'
 OR p_canonical IS NULL OR octet_length(p_canonical)>2097152 OR p_sha IS NULL
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha
 OR NOT (p_canonical IS JSON OBJECT WITH UNIQUE KEYS)
 THEN RAISE EXCEPTION 'invalid_generation_archive'; END IF;
 d:=p_canonical::jsonb;
 IF NOT(d ?& ARRAY['archive_version','provider','model','endpoint_origin','provider_id','polling_url','response_digest','wire_response_sha256','response_body_base64'])
 OR (SELECT count(*) FROM jsonb_object_keys(d))<>9
 OR jsonb_typeof(d->'archive_version') IS DISTINCT FROM 'number' OR d->>'archive_version' IS DISTINCT FROM '1'
 THEN RAISE EXCEPTION 'invalid_generation_archive'; END IF;
 FOREACH field IN ARRAY ARRAY['provider','model','endpoint_origin','provider_id','polling_url','response_digest','wire_response_sha256','response_body_base64'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_generation_archive'; END IF;
 END LOOP;
 IF d->>'provider_id' IS DISTINCT FROM p_provider_id OR d->>'response_digest' IS DISTINCT FROM p_digest
 OR d->>'wire_response_sha256' IS DISTINCT FROM p_digest OR d->>'model' IS DISTINCT FROM a.model
 OR d->>'provider' IS DISTINCT FROM (CASE WHEN a.provider='bfl_mock' THEN 'bfl' ELSE 'aliyun_bailian' END)
 OR length(d->>'response_body_base64') NOT BETWEEN 4 AND 1398104 OR d->>'response_body_base64' !~ '^[A-Za-z0-9+/]*={0,2}$'
 THEN RAISE EXCEPTION 'generation_archive_scope_mismatch'; END IF;
 raw:=decode(d->>'response_body_base64','base64');
 IF octet_length(raw) NOT BETWEEN 1 AND 1048576 OR encode(sha256(raw),'hex') IS DISTINCT FROM p_digest
 OR translate(encode(raw,'base64'),chr(10)||chr(13),'') IS DISTINCT FROM d->>'response_body_base64'
 OR NOT (convert_from(raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 THEN RAISE EXCEPTION 'generation_archive_wire_mismatch'; END IF;
 body:=convert_from(raw,'UTF8')::jsonb;
 endpoint:=t.input_snapshot->'_execution'->'provider_endpoints'->>'generate';
 IF a.provider='bfl_mock' THEN
  IF a.model<>'flux-3-image' OR d->>'endpoint_origin'<>'https://api.bfl.ai'
  OR jsonb_typeof(body->'id') IS DISTINCT FROM 'string' OR jsonb_typeof(body->'polling_url') IS DISTINCT FROM 'string'
  OR COALESCE(endpoint,'https://api.bfl.ai/v1/flux-3-image')<>'https://api.bfl.ai/v1/flux-3-image'
  OR body->>'id' IS DISTINCT FROM p_provider_id OR body->>'polling_url' IS DISTINCT FROM d->>'polling_url'
  OR d->>'polling_url' !~ '^https://api([.][a-z0-9-]+)?[.]bfl[.]ai([:]443)?/v1/get_result[?]id=[A-Za-z0-9_-]{1,200}$'
  OR split_part(d->>'polling_url','?id=',2) IS DISTINCT FROM p_provider_id THEN RAISE EXCEPTION 'generation_archive_scope_mismatch'; END IF;
 ELSE
  IF a.model<>'qwen-image-3.0' OR d->>'endpoint_origin' !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com$'
  OR jsonb_typeof(body->'output') IS DISTINCT FROM 'object' OR jsonb_typeof(body->'output'->'task_id') IS DISTINCT FROM 'string'
  OR endpoint IS DISTINCT FROM (d->>'endpoint_origin')||'/api/v1/services/aigc/image-generation/generation'
  OR d->>'polling_url' IS DISTINCT FROM (d->>'endpoint_origin')||'/api/v1/tasks/'||p_provider_id
  OR body->'output'->>'task_id' IS DISTINCT FROM p_provider_id
  OR body->'output'->>'task_status' NOT IN ('PENDING','RUNNING','SUCCEEDED')
  OR jsonb_typeof(body->'output'->'task_status') IS DISTINCT FROM 'string'
  OR body->>'code' IS NOT NULL THEN RAISE EXCEPTION 'generation_archive_scope_mismatch'; END IF;
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('generation-archive:'||a.id::text,0));
 identity:=app.append_receipt(t.id,a.id,p_provider_id,p_digest,d->>'polling_url');
 SELECT * INTO existing FROM ops.generation_archives WHERE attempt_id=a.id;
 IF FOUND THEN
  IF existing.document_sha256 IS DISTINCT FROM p_sha OR existing.receipt_id IS DISTINCT FROM identity
  THEN RAISE EXCEPTION 'generation_archive_conflict'; END IF;
  RETURN identity;
 END IF;
 INSERT INTO ops.generation_archives(attempt_id,user_id,task_id,receipt_id,provider_id,response_digest,request_sha256,document_sha256,canonical_document,document)
 VALUES(a.id,u,t.id,identity,p_provider_id,p_digest,a.request_hash,p_sha,p_canonical,d);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'generation_response_archived','task',t.id,'preserved original async submission response; no resubmission',p_sha,
 jsonb_build_object('attempt_id',a.id,'receipt_id',identity,'response_digest',p_digest,'request_sha256',a.request_hash),identity);
 RETURN identity;
END $function$;

CREATE OR REPLACE FUNCTION app.append_qwen_result(p_task uuid, p_owner text, p_fence bigint, p_attempt uuid, p_text text, p_sha text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE t app.generation_tasks; a app.provider_attempts; r ops.provider_receipts; d jsonb; existing ops.qwen_result_archives;
 identity uuid; field text;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id;
 IF NOT FOUND OR a.step<>'generate' OR a.provider NOT IN ('bailian_mock','bailian_live') OR a.model<>'qwen-image-3.0'
 OR a.provider_id IS NULL OR a.response_digest IS NULL OR COALESCE(t.input_snapshot->'_execution'->>'provider_profile','') NOT IN ('offline_protocol_mock','bailian_live')
 THEN RAISE EXCEPTION 'qwen_result_scope_mismatch'; END IF;
 SELECT * INTO r FROM ops.provider_receipts WHERE attempt_id=a.id AND task_id=t.id AND user_id=t.user_id AND provider_id=a.provider_id AND response_digest=a.response_digest;
 IF NOT FOUND THEN RAISE EXCEPTION 'qwen_submission_receipt_required'; END IF;
 d:=ops.validated_qwen_result(p_text,p_sha,split_part(t.input_snapshot->'_execution'->'provider_endpoints'->>'generate','/api/v1/',1),a.provider_id,a.response_digest,(t.input_snapshot->'generation'->'output'->>'width')::integer,(t.input_snapshot->'generation'->'output'->>'height')::integer);
 SELECT * INTO existing FROM ops.qwen_result_archives WHERE attempt_id=a.id;
 IF FOUND THEN
  -- A fresh signed URL/request ID may change query bytes. Never replace the original.
  -- The downloaded bytes, canonical image and exact usage must still match.
  FOREACH field IN ARRAY ARRAY['archive_version','model','endpoint_origin','provider_id','submission_response_sha256','downloaded_sha256','generated_sha256','width','height','input_image_count','billing_usage_integrity','quantities'] LOOP
   IF existing.document->field IS DISTINCT FROM d->field THEN RAISE EXCEPTION 'qwen_result_conflict'; END IF;
  END LOOP;
  RETURN existing.id;
 END IF;
 INSERT INTO ops.qwen_result_archives(attempt_id,user_id,task_id,receipt_id,provider_id,submission_response_sha256,canonical_document,document_sha256,document)
 VALUES(a.id,t.user_id,t.id,r.id,a.provider_id,a.response_digest,p_text,p_sha,d) RETURNING id INTO identity;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,usage,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,'result_observation',a.provider,a.model,NULL,NULL,'unknown',
 jsonb_build_object('transport',CASE WHEN a.provider='bailian_live' THEN 'bailian_live' ELSE 'offline_mock' END,'response_digest',a.response_digest,'submission_response_sha256',a.response_digest,'wire_response_sha256',d->'wire_response_sha256','generated_sha256',d->'generated_sha256','billing_usage_integrity',d->'billing_usage_integrity','quantities',d->'quantities','actual_spending_verified',false,'qwen_result_archive_id',identity),
 'original-qwen-query-not-invoice','no-real-price');
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'qwen_result_archived','task',t.id,'preserved original successful query and image meters; not an invoice',p_sha,jsonb_build_object('attempt_id',a.id,'wire_response_sha256',d->'wire_response_sha256','generated_sha256',d->'generated_sha256'),identity);
 RETURN identity;
END $function$;

ALTER TABLE ops.detection_archives DROP CONSTRAINT detection_archives_representation_version_check;
ALTER TABLE ops.detection_archives ADD CONSTRAINT detection_archives_representation_version_check CHECK(representation_version IN (1,2,3));
ALTER TABLE ops.detection_archives DROP CONSTRAINT detection_archive_representation;
ALTER TABLE ops.detection_archives ADD CONSTRAINT detection_archive_representation CHECK(
 (representation_version=1 AND wire_body_sha256 IS NULL AND NOT(document ? 'archive_version'))
 OR (representation_version=2 AND wire_body_sha256 IS NOT NULL AND document->>'archive_version'='2' AND jsonb_typeof(document->'archive_version')='number' AND wire_body_sha256=document->>'wire_response_sha256')
 OR (representation_version=3 AND wire_body_sha256 IS NOT NULL AND document->>'archive_version'='1' AND jsonb_typeof(document->'archive_version')='number' AND document->>'provider'='aliyun_bailian_vision' AND document->>'transport'='bailian_live' AND wire_body_sha256=document->>'wire_response_sha256'));
CREATE FUNCTION app.append_live_detection_receipt(p_task uuid,p_attempt uuid,p_provider_id text,p_digest text,p_canonical text,p_sha text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; final app.asset_versions; d jsonb; raw bytea; body jsonb; identity uuid; existing ops.detection_archives; field text;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u;
 IF NOT FOUND OR a.step<>'detect' OR a.provider<>'bailian_vision_live' OR a.model<>'qwen3.8-flash' OR t.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'bailian_live' THEN RAISE EXCEPTION 'detection_archive_scope_mismatch'; END IF;
 IF p_provider_id IS DISTINCT FROM 'qwen-vision-'||a.id::text OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR p_canonical IS NULL OR octet_length(p_canonical)>4194304 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha OR NOT(p_canonical IS JSON OBJECT WITH UNIQUE KEYS) THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 d:=p_canonical::jsonb;
 IF NOT(d ?& ARRAY['archive_version','provider','model','endpoint_origin','provider_id','provider_response_id','response_digest','response_body_base64','wire_response_sha256','final_sha256','width','height','facts','facts_schema_sha256','system_prompt_sha256','usage','billing_usage_integrity','quantities','response_structure_integrity','manual_review_required','cost','response_format','transport','reported_model','request_sha256']) OR (SELECT count(*) FROM jsonb_object_keys(d))<>25 THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 SELECT * INTO final FROM app.asset_versions WHERE id=t.final_version_id AND user_id=u;
 IF NOT FOUND OR d->>'provider_id' IS DISTINCT FROM p_provider_id OR d->>'model' IS DISTINCT FROM a.model OR d->>'provider' IS DISTINCT FROM 'aliyun_bailian_vision'
 OR d->>'transport' IS DISTINCT FROM 'bailian_live' OR d->>'archive_version' IS DISTINCT FROM '1' OR jsonb_typeof(d->'archive_version') IS DISTINCT FROM 'number'
 OR d->>'endpoint_origin' IS DISTINCT FROM split_part(t.input_snapshot->'_execution'->'provider_endpoints'->>'detect','/compatible-mode/',1)
 OR d->>'final_sha256' IS DISTINCT FROM final.sha256 OR d->>'width' IS DISTINCT FROM final.width::text OR d->>'height' IS DISTINCT FROM final.height::text
 OR d->'facts'->>'image_width' IS DISTINCT FROM final.width::text OR d->'facts'->>'image_height' IS DISTINCT FROM final.height::text
 OR d->>'response_digest' IS DISTINCT FROM p_digest OR d->>'wire_response_sha256' IS DISTINCT FROM p_digest
 OR jsonb_typeof(d->'facts') IS DISTINCT FROM 'object' OR jsonb_typeof(d->'manual_review_required') IS DISTINCT FROM 'boolean'
 OR d->>'response_format' IS DISTINCT FROM 'json_object_with_local_facts_validation' THEN RAISE EXCEPTION 'detection_archive_scope_mismatch'; END IF;
 FOREACH field IN ARRAY ARRAY['facts_schema_sha256','system_prompt_sha256','request_sha256'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' OR d->>field !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 END LOOP;
 IF (d->'facts'->'analysis_complete' IS DISTINCT FROM 'true'::jsonb OR d->'facts'->'uncertainties' IS DISTINCT FROM '[]'::jsonb) AND d->'manual_review_required' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'detection_manual_review_required'; END IF;
 IF length(d->>'response_body_base64') NOT BETWEEN 4 AND 1398104 OR d->>'response_body_base64' !~ '^[A-Za-z0-9+/]*={0,2}$' THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 raw:=decode(d->>'response_body_base64','base64');
 IF octet_length(raw) NOT BETWEEN 1 AND 1048576 OR translate(encode(raw,'base64'),chr(10)||chr(13),'') IS DISTINCT FROM d->>'response_body_base64' OR encode(sha256(raw),'hex') IS DISTINCT FROM p_digest THEN RAISE EXCEPTION 'detection_archive_wire_digest_mismatch'; END IF;
 IF d->>'response_structure_integrity'='valid' THEN
  IF NOT(convert_from(raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS) THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
  body:=convert_from(raw,'UTF8')::jsonb;
  IF body->>'id' IS DISTINCT FROM d->>'provider_response_id' OR body->>'model' IS DISTINCT FROM d->>'reported_model' THEN RAISE EXCEPTION 'detection_archive_response_identity_mismatch'; END IF;
 ELSIF d->>'response_structure_integrity'<>'invalid' OR d->'manual_review_required' IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('detection-archive:'||a.id::text,0));
 identity:=app.append_receipt(t.id,a.id,p_provider_id,p_digest,NULL);
 SELECT * INTO existing FROM ops.detection_archives WHERE attempt_id=a.id;
 IF FOUND THEN
  IF existing.document_sha256 IS DISTINCT FROM p_sha OR existing.receipt_id IS DISTINCT FROM identity THEN RAISE EXCEPTION 'detection_archive_conflict'; END IF;
  RETURN identity;
 END IF;
 INSERT INTO ops.detection_archives(attempt_id,user_id,task_id,receipt_id,provider_id,response_digest,final_sha256,document_sha256,canonical_document,document,representation_version,wire_body_sha256)
 VALUES(a.id,u,t.id,identity,p_provider_id,p_digest,final.sha256,p_sha,p_canonical,d,3,p_digest);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'live_detection_response_archived','task',t.id,'original live Qwen wire and conservative facts; no resubmission',p_sha,jsonb_build_object('attempt_id',a.id,'receipt_id',identity,'representation_version',3,'final_sha256',final.sha256),identity);
 RETURN identity;
END $$;
CREATE OR REPLACE FUNCTION app.append_detection_receipt(p_task uuid, p_attempt uuid, p_provider_id text, p_digest text, p_canonical text, p_sha text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; final app.asset_versions; doc jsonb;
 existing ops.detection_archives; identity uuid; field text; sdk jsonb; body bytea; version integer:=1; wire_sha text;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u;
 IF FOUND AND a.provider='bailian_vision_live' THEN RETURN app.append_live_detection_receipt(p_task,p_attempt,p_provider_id,p_digest,p_canonical,p_sha); END IF;
 IF NOT FOUND OR a.step<>'detect' OR a.provider<>'gemini_mock' OR a.model<>'gemini-3.8-flash'
 OR t.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
 THEN RAISE EXCEPTION 'detection_archive_scope_mismatch'; END IF;
 IF p_provider_id IS NULL OR p_provider_id IS DISTINCT FROM 'gemini-'||a.id::text OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$'
 OR p_canonical IS NULL OR octet_length(p_canonical)>16777216 OR p_sha IS NULL
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 doc:=p_canonical::jsonb;
 IF jsonb_typeof(doc) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 IF NOT (doc ?& ARRAY['model','endpoint_origin','sdk_version','facts_schema_sha256','system_prompt_sha256','provider_id','provider_response_id','reported_model_version','response_digest','final_sha256','facts','manual_review_required','usage','billing_usage_integrity','cost'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(doc) key WHERE key NOT IN ('model','endpoint_origin','sdk_version','facts_schema_sha256','system_prompt_sha256','provider_id','provider_response_id','reported_model_version','response_digest','final_sha256','facts','manual_review_required','usage','billing_usage_integrity','cost','archive_version','canonical_response','response_body_base64','wire_response_sha256','response_structure_integrity'))
 THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 FOREACH field IN ARRAY ARRAY['model','endpoint_origin','sdk_version','facts_schema_sha256','system_prompt_sha256','provider_id','response_digest','final_sha256','billing_usage_integrity'] LOOP
  IF jsonb_typeof(doc->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 END LOOP;
 SELECT * INTO final FROM app.asset_versions WHERE id=t.final_version_id AND user_id=u;
 IF NOT FOUND OR doc->>'final_sha256' IS DISTINCT FROM final.sha256
 OR doc->>'model' IS DISTINCT FROM 'gemini-3.8-flash' OR doc->>'provider_id' IS DISTINCT FROM p_provider_id
 OR doc->>'response_digest' IS DISTINCT FROM p_digest
 OR doc->>'endpoint_origin' IS DISTINCT FROM COALESCE(split_part(t.input_snapshot->'_execution'->'provider_endpoints'->>'detect','/v1beta/',1),'https://generativelanguage.googleapis.com')
 OR doc->>'facts_schema_sha256' !~ '^[0-9a-f]{64}$' OR doc->>'system_prompt_sha256' !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(doc->'facts') IS DISTINCT FROM 'object' OR jsonb_typeof(doc->'manual_review_required') IS DISTINCT FROM 'boolean'
 THEN RAISE EXCEPTION 'detection_archive_scope_mismatch'; END IF;
 IF doc->'facts'->>'image_width' IS DISTINCT FROM final.width::text OR doc->'facts'->>'image_height' IS DISTINCT FROM final.height::text
 THEN RAISE EXCEPTION 'detection_archive_dimensions_mismatch'; END IF;
 IF doc ? 'archive_version' THEN
  IF doc->>'archive_version' IS DISTINCT FROM '2' OR jsonb_typeof(doc->'archive_version') IS DISTINCT FROM 'number'
  OR NOT (doc ?& ARRAY['canonical_response','response_body_base64','wire_response_sha256','response_structure_integrity']) THEN RAISE EXCEPTION 'invalid_detection_archive_format'; END IF;
  FOREACH field IN ARRAY ARRAY['canonical_response','response_body_base64','wire_response_sha256','response_structure_integrity'] LOOP
   IF jsonb_typeof(doc->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_detection_archive_format'; END IF;
  END LOOP;
  IF octet_length(doc->>'canonical_response')>8388608 OR encode(sha256(convert_to(doc->>'canonical_response','UTF8')),'hex') IS DISTINCT FROM p_digest
  OR length(doc->>'response_body_base64') NOT BETWEEN 4 AND 1398104
  OR doc->>'response_body_base64' !~ '^[A-Za-z0-9+/]*={0,2}$'
  OR doc->>'wire_response_sha256' !~ '^[0-9a-f]{64}$' OR doc->>'response_structure_integrity' NOT IN ('valid','ambiguous')
  THEN RAISE EXCEPTION 'detection_archive_response_digest_mismatch'; END IF;
  sdk:=(doc->>'canonical_response')::jsonb;
  IF jsonb_typeof(sdk) IS DISTINCT FROM 'object' OR sdk->>'model_version' IS DISTINCT FROM doc->>'reported_model_version'
  OR sdk->>'response_id' IS DISTINCT FROM doc->>'provider_response_id' THEN RAISE EXCEPTION 'detection_archive_response_identity_mismatch'; END IF;
  body:=decode(doc->>'response_body_base64','base64'); wire_sha:=doc->>'wire_response_sha256';
  IF octet_length(body) NOT BETWEEN 1 AND 1048576
  OR translate(encode(body,'base64'),chr(10)||chr(13),'') IS DISTINCT FROM doc->>'response_body_base64' OR encode(sha256(body),'hex') IS DISTINCT FROM wire_sha
  THEN RAISE EXCEPTION 'detection_archive_wire_digest_mismatch'; END IF;
  version:=2;
 ELSIF doc ?| ARRAY['canonical_response','response_body_base64','wire_response_sha256','response_structure_integrity'] THEN
  RAISE EXCEPTION 'invalid_detection_archive_format';
 END IF;
 -- Legacy mock quotes without explicit endpoints always used the official root.
 PERFORM pg_advisory_xact_lock(hashtextextended('detection-archive:'||a.id::text,0));
 identity:=app.append_receipt(t.id,a.id,p_provider_id,p_digest,NULL);
 SELECT * INTO existing FROM ops.detection_archives WHERE attempt_id=a.id;
 IF FOUND THEN
  IF existing.document_sha256 IS DISTINCT FROM p_sha OR existing.receipt_id IS DISTINCT FROM identity THEN RAISE EXCEPTION 'detection_archive_conflict'; END IF;
  RETURN identity;
 END IF;
 INSERT INTO ops.detection_archives(attempt_id,user_id,task_id,receipt_id,provider_id,response_digest,final_sha256,document_sha256,canonical_document,document,representation_version,wire_body_sha256)
 VALUES(a.id,u,t.id,identity,p_provider_id,p_digest,final.sha256,p_sha,p_canonical,doc,version,wire_sha);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'detection_response_archived','task',t.id,'preserved original synchronous detection facts; no resubmission',p_sha,
 jsonb_build_object('attempt_id',a.id,'receipt_id',identity,'response_digest',p_digest,'final_sha256',final.sha256,'representation_version',version,'wire_body_sha256',wire_sha),identity);
 RETURN identity;
END $function$;
CREATE OR REPLACE FUNCTION app.record_protocol_observation(p_task uuid, p_owner text, p_fence bigint, p_attempt uuid, p_stage text, p_usage jsonb)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE t app.generation_tasks; a app.provider_attempts; previous jsonb; saved ops.detection_archives; field text;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id;
 IF NOT FOUND OR a.provider NOT IN ('bfl_mock','gemini_mock','bailian_mock','bailian_live','bailian_vision_live') THEN RAISE EXCEPTION 'protocol_attempt_required'; END IF;
 IF p_stage IS NULL OR p_stage NOT IN ('intent','response') OR p_usage IS NULL OR jsonb_typeof(p_usage)<>'object' OR octet_length(p_usage::text)>8192 THEN RAISE EXCEPTION 'invalid_observation'; END IF;
 IF p_stage='response' AND (a.provider_id IS NULL OR p_usage->>'response_digest' IS DISTINCT FROM a.response_digest) THEN RAISE EXCEPTION 'receipt_observation_mismatch'; END IF;
 IF p_stage='response' AND a.step='detect' THEN
  SELECT * INTO saved FROM ops.detection_archives WHERE attempt_id=a.id AND user_id=t.user_id AND task_id=t.id;
  IF NOT FOUND THEN RAISE EXCEPTION 'detection_archive_required'; END IF;
  IF saved.representation_version=3 THEN
   IF p_usage->>'transport' IS DISTINCT FROM 'bailian_live' OR p_usage->>'reported_model_version' IS DISTINCT FROM saved.document->>'reported_model' THEN RAISE EXCEPTION 'detection_observation_archive_mismatch'; END IF;
   FOREACH field IN ARRAY ARRAY['usage','quantities','billing_usage_integrity','final_sha256','archive_version','wire_response_sha256','response_structure_integrity'] LOOP
    IF p_usage->field IS DISTINCT FROM saved.document->field THEN RAISE EXCEPTION 'detection_observation_archive_mismatch'; END IF;
   END LOOP;
  ELSE
  FOREACH field IN ARRAY ARRAY['usage','billing_usage_integrity','reported_model_version','sdk_version','final_sha256'] LOOP
   IF COALESCE(p_usage->field,'null'::jsonb)::text IS DISTINCT FROM COALESCE(saved.document->field,'null'::jsonb)::text
   THEN RAISE EXCEPTION 'detection_observation_archive_mismatch'; END IF;
  END LOOP;
  END IF;
  IF saved.representation_version=2 THEN
   FOREACH field IN ARRAY ARRAY['archive_version','wire_response_sha256','response_structure_integrity'] LOOP
    IF (p_usage->field)::text IS DISTINCT FROM (saved.document->field)::text THEN RAISE EXCEPTION 'detection_observation_archive_mismatch'; END IF;
   END LOOP;
  END IF;
 END IF;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,usage,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,p_stage||'_observation',a.provider,a.model,NULL,NULL,'unknown',p_usage,CASE WHEN a.provider IN ('bailian_live','bailian_vision_live') THEN 'live-provider-wire' ELSE 'offline-protocol-mock' END,'no-real-price')
 ON CONFLICT(attempt_id,cost_record_kind) DO NOTHING;
 SELECT usage INTO previous FROM app.task_cost_records WHERE attempt_id=a.id AND cost_record_kind=p_stage||'_observation';
 IF previous IS DISTINCT FROM p_usage THEN RAISE EXCEPTION 'observation_conflict'; END IF;
END $function$;

CREATE FUNCTION app.record_live_estimate(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; existing app.task_cost_records; q jsonb; rates jsonb; d jsonb; k text; amount numeric:=0; identity uuid; state text:='unknown'; reason text:='usage_incomplete';
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id;
 IF NOT FOUND OR a.provider NOT IN ('bailian_live','bailian_vision_live') OR a.provider_id IS NULL OR a.response_digest IS NULL THEN RAISE EXCEPTION 'live_receipt_required'; END IF;
 SELECT * INTO existing FROM app.task_cost_records WHERE attempt_id=a.id AND user_id=t.user_id AND cost_record_kind='estimate';
 IF FOUND THEN RETURN jsonb_build_object('id',existing.id,'cost_status',existing.cost_status,'amount',existing.amount::text,'currency',existing.currency,'actual_spending_verified',false); END IF;
 rates:=t.input_snapshot->'_execution'->'live_reference_rates'->a.step;
 IF a.step='generate' THEN
  SELECT document INTO d FROM ops.qwen_result_archives WHERE attempt_id=a.id AND task_id=t.id AND user_id=t.user_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF d->>'billing_usage_integrity'='complete' THEN q:=d->'quantities'; END IF;
 ELSE
  SELECT document INTO d FROM ops.detection_archives WHERE attempt_id=a.id AND task_id=t.id AND user_id=t.user_id AND representation_version=3;
  IF NOT FOUND THEN RAISE EXCEPTION 'detection_archive_required'; END IF;
  IF d->>'billing_usage_integrity'='complete' AND d->>'reported_model'=a.model THEN q:=d->'quantities'; END IF;
 END IF;
 IF q IS NOT NULL AND jsonb_typeof(q)='object' THEN
  IF a.step='generate' THEN
   FOREACH k IN ARRAY ARRAY['qima_input_1k','qima_input_2k','qima_output_1k','qima_output_2k'] LOOP
    IF jsonb_typeof(q->k) IS DISTINCT FROM 'number' OR q->>k !~ '^[0-9]+$' OR (q->>k)::numeric>1000000000000 THEN q:=NULL;EXIT; END IF;
    amount:=amount+(q->>k)::numeric*(rates->>k)::numeric;
   END LOOP;
  ELSE
   FOREACH k IN ARRAY ARRAY['input_tokens','cached_input_tokens','output_tokens'] LOOP
    IF jsonb_typeof(q->k) IS DISTINCT FROM 'number' OR q->>k !~ '^[0-9]+$' OR (q->>k)::numeric>1000000000000 THEN q:=NULL;EXIT; END IF;
   END LOOP;
   IF q IS NOT NULL THEN amount:=((q->>'input_tokens')::numeric*(rates->>'input_token')::numeric+(q->>'cached_input_tokens')::numeric*(rates->>'input_token_cache')::numeric+(q->>'output_tokens')::numeric*(rates->>'output_token')::numeric)/1000000; END IF;
  END IF;
  IF q IS NOT NULL AND amount>=0 AND amount<1000000000000 THEN state:='estimated';amount:=ceil(amount*100000000)/100000000;reason:='reference_table_estimate_not_invoice'; END IF;
 END IF;
 IF state='unknown' THEN amount:=NULL; END IF;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,usage,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,'estimate',a.provider,a.model,CASE WHEN state='estimated' THEN 'CNY' ELSE NULL END,amount,state,jsonb_build_object('transport','bailian_live','response_digest',a.response_digest,'wire_response_sha256',d->'wire_response_sha256','quantities',q,'reference_rates',rates,'price_evidence_sha256',t.input_snapshot->'_execution'->'live_price_evidence_sha256'->a.step,'reason',reason,'actual_spending_verified',false),'audited-live-reference-not-invoice','bailian-live-reference-v1') RETURNING id INTO identity;
 RETURN jsonb_build_object('id',identity,'cost_status',state,'amount',amount::text,'currency',CASE WHEN state='estimated' THEN 'CNY' ELSE NULL END,'actual_spending_verified',false);
END $$;
CREATE FUNCTION ops.revoke_restored_live_policies() RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row record; total integer:=0;
BEGIN
 FOR row IN SELECT DISTINCT ON(target_id) * FROM ops.audit_events WHERE action='live_provider_policy' AND target_type='user' ORDER BY target_id,created_at DESC,id DESC LOOP
  IF row.after_state->'enabled'='true'::jsonb THEN
   INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id,created_at)
   VALUES(session_user,'live_provider_policy','user',row.target_id,'restore revokes live submissions until explicitly re-authorized',row.evidence_digest,row.after_state||jsonb_build_object('enabled',false,'restored_authority_revoked',true),gen_random_uuid(),clock_timestamp());total:=total+1;
  END IF;
 END LOOP;
 RETURN total;
END $$;
CREATE OR REPLACE FUNCTION app.record_supplier_estimate(p_task uuid, p_owner text, p_fence bigint, p_attempt uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
 IF a.provider IN ('bailian_live','bailian_vision_live') THEN RETURN app.record_live_estimate(p_task,p_owner,p_fence,p_attempt); END IF;
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
END $function$;
ALTER FUNCTION ops.activate_live_provider(uuid,text,jsonb,jsonb,boolean,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.activate_live_provider(uuid,text,jsonb,jsonb,boolean,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.activate_live_provider(uuid,text,jsonb,jsonb,boolean,uuid,text) TO ops_admin;
ALTER FUNCTION ops.live_policy(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.live_policy(uuid) FROM PUBLIC;
ALTER FUNCTION ops.assert_live_snapshot(jsonb,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.assert_live_snapshot(jsonb,uuid) FROM PUBLIC;
ALTER FUNCTION app.live_provider_configuration() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.live_provider_configuration() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.live_provider_configuration() TO app_api;
ALTER FUNCTION ops.task_provider_profile(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.task_provider_profile(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.task_provider_profile(uuid) TO app_worker;
ALTER FUNCTION ops.validate_delivery(uuid,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.validate_delivery(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.validate_delivery(uuid,uuid,text) TO app_worker;
ALTER FUNCTION app.append_live_detection_receipt(uuid,uuid,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.append_live_detection_receipt(uuid,uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.append_live_detection_receipt(uuid,uuid,text,text,text,text) TO app_worker,ops_admin;
ALTER FUNCTION app.record_live_estimate(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_live_estimate(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_live_estimate(uuid,text,bigint,uuid) TO app_worker;
ALTER FUNCTION ops.revoke_restored_live_policies() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.revoke_restored_live_policies() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.revoke_restored_live_policies() TO ops_admin;

CREATE OR REPLACE FUNCTION ops.validated_qwen_result(p_text text,p_sha text,p_origin text,p_id text,p_submit text,p_width integer,p_height integer) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE d jsonb; body jsonb; raw bytea; field text; lexical json; usage json;
 integrity text:='invalid'; meters jsonb; suffix text; valid boolean:=true; url text;
BEGIN
 IF p_text IS NULL OR octet_length(p_text)>2097152 OR p_sha IS NULL
 OR encode(sha256(convert_to(p_text,'UTF8')),'hex') IS DISTINCT FROM p_sha
 OR NOT(p_text IS JSON OBJECT WITH UNIQUE KEYS)
 THEN RAISE EXCEPTION 'invalid_qwen_result_archive'; END IF;
 d:=p_text::jsonb;
 IF (SELECT count(*) FROM jsonb_object_keys(d))<>14
 OR NOT(d ?& ARRAY['archive_version','model','endpoint_origin','provider_id','submission_response_sha256','response_body_base64','wire_response_sha256','downloaded_sha256','generated_sha256','width','height','input_image_count','billing_usage_integrity','quantities'])
 OR jsonb_typeof(d->'archive_version') IS DISTINCT FROM 'number' OR d->>'archive_version' IS DISTINCT FROM '1'
 THEN RAISE EXCEPTION 'invalid_qwen_result_archive'; END IF;
 FOREACH field IN ARRAY ARRAY['model','endpoint_origin','provider_id','submission_response_sha256','response_body_base64','wire_response_sha256','downloaded_sha256','generated_sha256','billing_usage_integrity'] LOOP
  IF jsonb_typeof(d->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_qwen_result_archive'; END IF;
 END LOOP;
 FOREACH field IN ARRAY ARRAY['submission_response_sha256','wire_response_sha256','downloaded_sha256','generated_sha256'] LOOP
  IF d->>field !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_qwen_result_archive'; END IF;
 END LOOP;
 IF p_origin IS NULL OR p_origin !~ '^https://ws-[a-z0-9]{1,64}[.]cn-beijing[.]maas[.]aliyuncs[.]com$'
 OR d->>'endpoint_origin' IS DISTINCT FROM p_origin OR d->>'provider_id' IS DISTINCT FROM p_id
 OR d->>'model' IS DISTINCT FROM 'qwen-image-3.0' OR d->>'submission_response_sha256' IS DISTINCT FROM p_submit
 OR jsonb_typeof(d->'width') IS DISTINCT FROM 'number' OR d->>'width' IS DISTINCT FROM p_width::text
 OR jsonb_typeof(d->'height') IS DISTINCT FROM 'number' OR d->>'height' IS DISTINCT FROM p_height::text
 OR jsonb_typeof(d->'input_image_count') IS DISTINCT FROM 'number' OR d->>'input_image_count' IS DISTINCT FROM '1'
 OR p_width IS NULL OR p_height IS NULL OR p_width NOT BETWEEN 256 AND 6256 OR p_height NOT BETWEEN 256 AND 6256
 OR p_width::bigint*p_height NOT BETWEEN 262144 AND 4000000 OR greatest(p_width,p_height)>8*least(p_width,p_height)
 OR length(d->>'response_body_base64') NOT BETWEEN 4 AND 1398104 OR d->>'response_body_base64' !~ '^[A-Za-z0-9+/]*={0,2}$'
 THEN RAISE EXCEPTION 'qwen_result_scope_mismatch'; END IF;
 raw:=decode(d->>'response_body_base64','base64');
 IF octet_length(raw) NOT BETWEEN 1 AND 1048576
 OR encode(sha256(raw),'hex') IS DISTINCT FROM d->>'wire_response_sha256'
 OR translate(encode(raw,'base64'),chr(10)||chr(13),'') IS DISTINCT FROM d->>'response_body_base64'
 OR NOT(convert_from(raw,'UTF8') IS JSON OBJECT WITH UNIQUE KEYS)
 THEN RAISE EXCEPTION 'qwen_result_wire_mismatch'; END IF;
 body:=convert_from(raw,'UTF8')::jsonb; lexical:=convert_from(raw,'UTF8')::json;
 IF jsonb_typeof(body->'output'->'task_id') IS DISTINCT FROM 'string' OR body->'output'->>'task_id' IS DISTINCT FROM p_id
 OR body->'output'->>'task_status' IS DISTINCT FROM 'SUCCEEDED' OR body->>'code' IS NOT NULL
 OR jsonb_typeof(body->'output'->'choices') IS DISTINCT FROM 'array' OR jsonb_array_length(body->'output'->'choices')<>1
 OR body->'output'->'choices'->0->>'finish_reason' IS DISTINCT FROM 'stop'
 OR body->'output'->'choices'->0->'message'->>'role' IS DISTINCT FROM 'assistant'
 OR jsonb_typeof(body->'output'->'choices'->0->'message'->'content') IS DISTINCT FROM 'array'
 OR jsonb_array_length(body->'output'->'choices'->0->'message'->'content')<>1
 OR jsonb_typeof(body->'output'->'choices'->0->'message'->'content'->0->'image') IS DISTINCT FROM 'string'
 THEN RAISE EXCEPTION 'qwen_result_scope_mismatch'; END IF;
 url:=body->'output'->'choices'->0->'message'->'content'->0->>'image';
 IF length(url)>8192 OR url ~ '[[:space:][:cntrl:]]' OR position(chr(92) IN url)>0
 OR url !~ '^https://(dashscope-result-[a-z0-9-]+[.]oss-cn-(beijing|shanghai|hangzhou|shenzhen)|dashscope-[a-z0-9]{1,32}[.]oss-accelerate)[.]aliyuncs[.]com([:]443)?/[^?#]*[.]png([?][^#]*)?$'
 THEN RAISE EXCEPTION 'qwen_result_url_mismatch'; END IF;
 usage:=lexical->'usage'; suffix:=CASE WHEN p_width::bigint*p_height<=2250000 THEN '1k' ELSE '2k' END;
 IF usage IS NULL OR json_typeof(usage)='null' THEN integrity:='missing';
 ELSIF json_typeof(usage)='object' THEN
  IF (SELECT count(*) FROM json_object_keys(usage))<>6
  OR NOT((usage::jsonb) ?& ARRAY['output_width','output_height','input_image_count','output_image_count','input_image_type','output_image_type']) THEN valid:=false; END IF;
  FOREACH field IN ARRAY ARRAY['output_width','output_height','input_image_count','output_image_count'] LOOP
   IF json_typeof(usage->field) IS DISTINCT FROM 'number' OR usage->>field !~ '^[0-9]{1,4}$' THEN valid:=false; END IF;
  END LOOP;
  IF usage->>'output_width' IS DISTINCT FROM p_width::text OR usage->>'output_height' IS DISTINCT FROM p_height::text
  OR usage->>'input_image_count' IS DISTINCT FROM '1' OR usage->>'output_image_count' IS DISTINCT FROM '1'
  OR usage->>'input_image_type' IS DISTINCT FROM 'qima_input_'||suffix
  OR usage->>'output_image_type' IS DISTINCT FROM 'qima_output_'||suffix THEN valid:=false; END IF;
  IF valid THEN
   integrity:='complete'; meters:=jsonb_build_object('qima_input_1k',CASE WHEN suffix='1k' THEN 1 ELSE 0 END,'qima_input_2k',CASE WHEN suffix='2k' THEN 1 ELSE 0 END,'qima_output_1k',CASE WHEN suffix='1k' THEN 1 ELSE 0 END,'qima_output_2k',CASE WHEN suffix='2k' THEN 1 ELSE 0 END);
  END IF;
 END IF;
 IF d->>'billing_usage_integrity' IS DISTINCT FROM integrity OR d->'quantities' IS DISTINCT FROM COALESCE(meters,'null'::jsonb)
 THEN RAISE EXCEPTION 'qwen_result_meter_mismatch'; END IF;
 RETURN d;
END $$;

CREATE OR REPLACE FUNCTION ops.deactivate_live_provider(p_user uuid,p_operation uuid,p_reason text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE policy jsonb; prior ops.audit_events;
BEGIN
 IF NOT pg_has_role(session_user,'ops_admin','member') THEN RAISE EXCEPTION 'operator_required'; END IF;
 IF p_user IS NULL OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'invalid_live_policy'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('live-policy:'||p_user::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'live_provider_policy' OR prior.target_id<>p_user OR prior.after_state->'enabled' IS DISTINCT FROM 'false'::jsonb OR prior.reason<>p_reason THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_operation;
 END IF;
 policy:=ops.live_policy(p_user);
 IF policy IS NULL THEN RETURN NULL; END IF;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id,created_at)
 VALUES(session_user,'live_provider_policy','user',p_user,p_reason,policy->>'runtime_sha256',policy||jsonb_build_object('enabled',false),p_operation,clock_timestamp());
 RETURN p_operation;
END $$;
ALTER FUNCTION ops.deactivate_live_provider(uuid,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.deactivate_live_provider(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.deactivate_live_provider(uuid,uuid,text) TO ops_admin;
