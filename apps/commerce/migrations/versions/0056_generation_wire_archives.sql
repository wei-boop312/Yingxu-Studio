CREATE TABLE ops.generation_archives (
 attempt_id uuid PRIMARY KEY, user_id uuid NOT NULL, task_id uuid NOT NULL, receipt_id uuid NOT NULL UNIQUE,
 provider_id text NOT NULL, response_digest text NOT NULL CHECK(response_digest ~ '^[0-9a-f]{64}$'),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),
 canonical_document text NOT NULL CHECK(octet_length(canonical_document)<=2097152),
 document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(document_sha256=encode(sha256(convert_to(canonical_document,'UTF8')),'hex')),
 CHECK(document=canonical_document::jsonb),
 FOREIGN KEY(user_id,task_id,attempt_id,receipt_id,provider_id,response_digest)
 REFERENCES ops.provider_receipts(user_id,task_id,attempt_id,id,provider_id,response_digest)
);
ALTER TABLE ops.generation_archives ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.generation_archives FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.generation_archives FROM PUBLIC;
GRANT SELECT,INSERT ON ops.generation_archives TO app_executor;
CREATE POLICY generation_archive_executor ON ops.generation_archives TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE TRIGGER immutable_generation_archive BEFORE UPDATE OR DELETE ON ops.generation_archives FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION app.append_generation_receipt(p_task uuid,p_attempt uuid,p_provider_id text,p_digest text,p_canonical text,p_sha text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; d jsonb; raw bytea; body jsonb;
 field text; endpoint text; identity uuid; existing ops.generation_archives;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=u;
 IF NOT FOUND OR a.step<>'generate' OR a.provider NOT IN ('bfl_mock','bailian_mock')
 OR t.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
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
END $$;
ALTER FUNCTION app.append_generation_receipt(uuid,uuid,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.append_generation_receipt(uuid,uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.append_generation_receipt(uuid,uuid,text,text,text,text) TO app_worker;

CREATE FUNCTION app.generation_archive(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; saved ops.generation_archives; a app.provider_attempts;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO saved FROM ops.generation_archives WHERE task_id=t.id AND user_id=t.user_id AND attempt_id=p_attempt;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id;
 IF NOT FOUND OR a.request_hash IS DISTINCT FROM saved.request_sha256 OR a.provider_id IS DISTINCT FROM saved.provider_id
 OR a.response_digest IS DISTINCT FROM saved.response_digest
 OR encode(sha256(convert_to(saved.canonical_document,'UTF8')),'hex') IS DISTINCT FROM saved.document_sha256
 OR saved.document IS DISTINCT FROM saved.canonical_document::jsonb
 OR saved.document->>'response_digest' IS DISTINCT FROM saved.response_digest
 OR encode(sha256(decode(saved.document->>'response_body_base64','base64')),'hex') IS DISTINCT FROM saved.response_digest
 THEN RAISE EXCEPTION 'generation_archive_integrity'; END IF;
 RETURN saved.document;
END $$;
ALTER FUNCTION app.generation_archive(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.generation_archive(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.generation_archive(uuid,text,bigint,uuid) TO app_worker;
