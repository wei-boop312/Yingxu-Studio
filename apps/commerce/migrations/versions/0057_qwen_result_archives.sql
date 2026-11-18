ALTER TABLE app.task_cost_records DROP CONSTRAINT task_cost_records_cost_record_kind_check;
ALTER TABLE app.task_cost_records ADD CONSTRAINT task_cost_records_cost_record_kind_check
 CHECK(cost_record_kind IN ('estimate','settlement','intent_observation','response_observation','result_observation'));

CREATE TABLE ops.qwen_result_archives (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), attempt_id uuid NOT NULL UNIQUE,
 user_id uuid NOT NULL, task_id uuid NOT NULL, receipt_id uuid NOT NULL,
 provider_id text NOT NULL, submission_response_sha256 text NOT NULL,
 document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),
 canonical_document text NOT NULL CHECK(octet_length(canonical_document)<=2097152),
 document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(document_sha256=encode(sha256(convert_to(canonical_document,'UTF8')),'hex')),
 CHECK(document=canonical_document::jsonb),
 FOREIGN KEY(user_id,task_id,attempt_id,receipt_id,provider_id,submission_response_sha256)
 REFERENCES ops.provider_receipts(user_id,task_id,attempt_id,id,provider_id,response_digest)
);
ALTER TABLE ops.qwen_result_archives ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.qwen_result_archives FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.qwen_result_archives FROM PUBLIC;
GRANT SELECT,INSERT ON ops.qwen_result_archives TO app_executor;
CREATE POLICY qwen_result_executor ON ops.qwen_result_archives TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE TRIGGER immutable_qwen_result BEFORE UPDATE OR DELETE ON ops.qwen_result_archives FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION ops.validated_qwen_result(p_text text,p_sha text,p_origin text,p_id text,p_submit text,p_width integer,p_height integer) RETURNS jsonb
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
 OR url !~ '^https://dashscope-result-[a-z0-9-]+[.]oss-cn-(beijing|shanghai|hangzhou|shenzhen)[.]aliyuncs[.]com([:]443)?/[^?#]*[.]png([?][^#]*)?$'
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
ALTER FUNCTION ops.validated_qwen_result(text,text,text,text,text,integer,integer) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.validated_qwen_result(text,text,text,text,text,integer,integer) FROM PUBLIC;

CREATE FUNCTION app.append_qwen_result(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid,p_text text,p_sha text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts; r ops.provider_receipts; d jsonb; existing ops.qwen_result_archives;
 identity uuid; field text;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND user_id=t.user_id;
 IF NOT FOUND OR a.step<>'generate' OR a.provider<>'bailian_mock' OR a.model<>'qwen-image-3.0'
 OR a.provider_id IS NULL OR a.response_digest IS NULL OR t.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
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
 jsonb_build_object('transport','offline_mock','response_digest',a.response_digest,'submission_response_sha256',a.response_digest,'wire_response_sha256',d->'wire_response_sha256','generated_sha256',d->'generated_sha256','billing_usage_integrity',d->'billing_usage_integrity','quantities',d->'quantities','actual_spending_verified',false,'qwen_result_archive_id',identity),
 'original-qwen-query-not-invoice','no-real-price');
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'qwen_result_archived','task',t.id,'preserved original successful query and image meters; not an invoice',p_sha,jsonb_build_object('attempt_id',a.id,'wire_response_sha256',d->'wire_response_sha256','generated_sha256',d->'generated_sha256'),identity);
 RETURN identity;
END $$;
ALTER FUNCTION app.append_qwen_result(uuid,text,bigint,uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.append_qwen_result(uuid,text,bigint,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.append_qwen_result(uuid,text,bigint,uuid,text,text) TO app_worker;

CREATE FUNCTION app.enforce_qwen_generated_archive() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE saved ops.qwen_result_archives; image app.asset_versions;
BEGIN
 IF NEW.generated_version_id IS NULL OR NEW.generated_version_id IS NOT DISTINCT FROM OLD.generated_version_id
 OR NEW.input_snapshot->'_execution'->'provider_models'->>'generate' IS DISTINCT FROM 'qwen-image-3.0' THEN RETURN NEW; END IF;
 SELECT * INTO saved FROM ops.qwen_result_archives WHERE task_id=NEW.id AND user_id=NEW.user_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'qwen_result_archive_required'; END IF;
 SELECT * INTO image FROM app.asset_versions WHERE id=NEW.generated_version_id AND user_id=NEW.user_id AND product_id=NEW.product_id;
 IF NOT FOUND OR image.sha256 IS DISTINCT FROM saved.document->>'generated_sha256'
 OR image.width::text IS DISTINCT FROM saved.document->>'width' OR image.height::text IS DISTINCT FROM saved.document->>'height'
 THEN RAISE EXCEPTION 'qwen_generated_image_mismatch'; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION app.enforce_qwen_generated_archive() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.enforce_qwen_generated_archive() FROM PUBLIC;
CREATE TRIGGER enforce_qwen_generated_archive BEFORE UPDATE OF generated_version_id ON app.generation_tasks FOR EACH ROW EXECUTE FUNCTION app.enforce_qwen_generated_archive();
