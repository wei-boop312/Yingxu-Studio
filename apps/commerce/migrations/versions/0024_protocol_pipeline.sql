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
 VALUES(p_id,u,p_input_hash,p_snapshot_hash,p_snapshot,CASE WHEN p_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN 'trial-protocol-mock-v1' ELSE 'trial-fake-v1' END,CASE WHEN p_snapshot->'_execution'->>'provider_profile'='offline_protocol_mock' THEN 'flux-3-image-protocol-mock' ELSE 'fake-v1' END,p_rules,16,now()+interval '10 minutes');
 RETURN p_id;
END $$;

ALTER TABLE app.task_cost_records ALTER COLUMN currency DROP NOT NULL;
ALTER TABLE app.task_cost_records DROP CONSTRAINT task_cost_records_cost_record_kind_check;
ALTER TABLE app.task_cost_records ADD CONSTRAINT task_cost_records_cost_record_kind_check
 CHECK(cost_record_kind IN ('estimate','settlement','intent_observation','response_observation'));

CREATE FUNCTION app.record_protocol_observation(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid,p_stage text,p_usage jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; previous jsonb;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id;
 IF NOT FOUND OR a.provider NOT IN ('bfl_mock','gemini_mock') THEN RAISE EXCEPTION 'protocol_attempt_required'; END IF;
 IF p_stage IS NULL OR p_stage NOT IN ('intent','response') OR p_usage IS NULL OR jsonb_typeof(p_usage)<>'object' OR octet_length(p_usage::text)>8192 THEN RAISE EXCEPTION 'invalid_observation'; END IF;
 IF p_stage='response' AND (a.provider_id IS NULL OR p_usage->>'response_digest' IS DISTINCT FROM a.response_digest) THEN RAISE EXCEPTION 'receipt_observation_mismatch'; END IF;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,usage,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,p_stage||'_observation',a.provider,a.model,NULL,NULL,'unknown',p_usage,'offline-protocol-mock','no-real-price')
 ON CONFLICT(attempt_id,cost_record_kind) DO NOTHING;
 SELECT usage INTO previous FROM app.task_cost_records WHERE attempt_id=a.id AND cost_record_kind=p_stage||'_observation';
 IF previous IS DISTINCT FROM p_usage THEN RAISE EXCEPTION 'observation_conflict'; END IF;
END $$;
ALTER FUNCTION app.record_protocol_observation(uuid,text,bigint,uuid,text,jsonb) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_protocol_observation(uuid,text,bigint,uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_protocol_observation(uuid,text,bigint,uuid,text,jsonb) TO app_worker;

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
 RETURN a;
END $$;

CREATE OR REPLACE FUNCTION app.worker_context(p_task uuid,p_owner text,p_fence bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; source app.asset_versions;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO source FROM app.asset_versions WHERE id=t.source_asset_version_id AND user_id=t.user_id;
 RETURN to_jsonb(t)||jsonb_build_object('source',to_jsonb(source),'generated',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=t.generated_version_id),'final',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=t.final_version_id),'cost_observations',COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt_id',c.attempt_id,'kind',c.cost_record_kind)) FROM app.task_cost_records c WHERE c.task_id=t.id),'[]'::jsonb),'attempts',COALESCE((SELECT jsonb_agg(to_jsonb(a)) FROM app.provider_attempts a WHERE task_id=t.id),'[]'::jsonb));
END $$;
