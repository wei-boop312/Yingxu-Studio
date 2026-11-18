-- Preserve original body bytes separately from normalized SDK response digest.
ALTER TABLE ops.detection_archives DROP CONSTRAINT detection_archives_canonical_document_check;
ALTER TABLE ops.detection_archives ADD CONSTRAINT detection_archive_document_size CHECK(octet_length(canonical_document)<=16777216);
ALTER TABLE ops.detection_archives ADD COLUMN representation_version integer NOT NULL DEFAULT 1 CHECK(representation_version IN (1,2));
ALTER TABLE ops.detection_archives ADD COLUMN wire_body_sha256 text CHECK(wire_body_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE ops.detection_archives ADD CONSTRAINT detection_archive_representation CHECK(
 (representation_version=1 AND wire_body_sha256 IS NULL AND NOT(document ? 'archive_version'))
 OR (representation_version=2 AND wire_body_sha256 IS NOT NULL
 AND jsonb_typeof(document->'archive_version') IS NOT DISTINCT FROM 'number'
 AND document->>'archive_version' IS NOT DISTINCT FROM '2'
 AND wire_body_sha256 IS NOT DISTINCT FROM document->>'wire_response_sha256'));
CREATE OR REPLACE FUNCTION app.append_detection_receipt(p_task uuid,p_attempt uuid,p_provider_id text,p_digest text,p_canonical text,p_sha text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; final app.asset_versions; doc jsonb;
 existing ops.detection_archives; identity uuid; field text; sdk jsonb; body bytea; version integer:=1; wire_sha text;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u;
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
END $$;
ALTER FUNCTION app.append_detection_receipt(uuid,uuid,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.append_detection_receipt(uuid,uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.append_detection_receipt(uuid,uuid,text,text,text,text) TO app_worker;

CREATE OR REPLACE FUNCTION app.detection_archive(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; saved ops.detection_archives;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO saved FROM ops.detection_archives WHERE task_id=t.id AND user_id=t.user_id AND attempt_id=p_attempt;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF encode(sha256(convert_to(saved.canonical_document,'UTF8')),'hex') IS DISTINCT FROM saved.document_sha256
 OR saved.document IS DISTINCT FROM saved.canonical_document::jsonb THEN RAISE EXCEPTION 'detection_archive_integrity'; END IF;
 IF saved.representation_version=2 AND (saved.wire_body_sha256 IS DISTINCT FROM saved.document->>'wire_response_sha256'
 OR encode(sha256(decode(saved.document->>'response_body_base64','base64')),'hex') IS DISTINCT FROM saved.wire_body_sha256
 OR encode(sha256(convert_to(saved.document->>'canonical_response','UTF8')),'hex') IS DISTINCT FROM saved.response_digest)
 THEN RAISE EXCEPTION 'detection_archive_response_integrity'; END IF;
 RETURN saved.document;
END $$;
ALTER FUNCTION app.detection_archive(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.detection_archive(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.detection_archive(uuid,text,bigint,uuid) TO app_worker;

CREATE OR REPLACE FUNCTION app.record_protocol_observation(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid,p_stage text,p_usage jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; previous jsonb; saved ops.detection_archives; field text;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id;
 IF NOT FOUND OR a.provider NOT IN ('bfl_mock','gemini_mock') THEN RAISE EXCEPTION 'protocol_attempt_required'; END IF;
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
