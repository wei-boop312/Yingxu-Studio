-- Preserve synchronous detection facts with their immutable receipt, not a local-only file.
ALTER TABLE ops.provider_receipts ADD CONSTRAINT detection_receipt_owned_digest
 UNIQUE(user_id,task_id,attempt_id,id,provider_id,response_digest);
CREATE TABLE ops.detection_archives (
 attempt_id uuid PRIMARY KEY, user_id uuid NOT NULL, task_id uuid NOT NULL, receipt_id uuid NOT NULL UNIQUE,
 provider_id text NOT NULL, response_digest text NOT NULL,
 final_sha256 text NOT NULL CHECK(final_sha256 ~ '^[0-9a-f]{64}$'),
 document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),
 canonical_document text NOT NULL CHECK(octet_length(canonical_document)<=2097152),
 document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_id,task_id,attempt_id,receipt_id,provider_id,response_digest)
 REFERENCES ops.provider_receipts(user_id,task_id,attempt_id,id,provider_id,response_digest)
);
ALTER TABLE ops.detection_archives ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.detection_archives FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.detection_archives FROM PUBLIC;
GRANT SELECT,INSERT ON ops.detection_archives TO app_executor;
GRANT SELECT ON ops.detection_archives TO ops_admin;
CREATE POLICY detection_archive_executor ON ops.detection_archives TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY detection_archive_operator ON ops.detection_archives FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_detection_archive BEFORE UPDATE OR DELETE ON ops.detection_archives FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

-- Like append_receipt, a late Worker may preserve the known result; applying it still needs a live fence.
CREATE FUNCTION app.append_detection_receipt(p_task uuid,p_attempt uuid,p_provider_id text,p_digest text,p_canonical text,p_sha text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; a app.provider_attempts; final app.asset_versions; doc jsonb;
 existing ops.detection_archives; identity uuid; field text;
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
 OR p_canonical IS NULL OR octet_length(p_canonical)>2097152 OR p_sha IS NULL
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 doc:=p_canonical::jsonb;
 IF jsonb_typeof(doc) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid_detection_archive'; END IF;
 IF NOT (doc ?& ARRAY['model','endpoint_origin','sdk_version','facts_schema_sha256','system_prompt_sha256','provider_id','provider_response_id','reported_model_version','response_digest','final_sha256','facts','manual_review_required','usage','billing_usage_integrity','cost'])
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(doc) key WHERE key NOT IN ('model','endpoint_origin','sdk_version','facts_schema_sha256','system_prompt_sha256','provider_id','provider_response_id','reported_model_version','response_digest','final_sha256','facts','manual_review_required','usage','billing_usage_integrity','cost'))
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
 -- Legacy mock quotes without explicit endpoints always used the official root.
 PERFORM pg_advisory_xact_lock(hashtextextended('detection-archive:'||a.id::text,0));
 identity:=app.append_receipt(t.id,a.id,p_provider_id,p_digest,NULL);
 SELECT * INTO existing FROM ops.detection_archives WHERE attempt_id=a.id;
 IF FOUND THEN
  IF existing.document_sha256 IS DISTINCT FROM p_sha OR existing.receipt_id IS DISTINCT FROM identity THEN RAISE EXCEPTION 'detection_archive_conflict'; END IF;
  RETURN identity;
 END IF;
 INSERT INTO ops.detection_archives(attempt_id,user_id,task_id,receipt_id,provider_id,response_digest,final_sha256,document_sha256,canonical_document,document)
 VALUES(a.id,u,t.id,identity,p_provider_id,p_digest,final.sha256,p_sha,p_canonical,doc);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'detection_response_archived','task',t.id,'preserved original synchronous detection facts; no resubmission',p_sha,
 jsonb_build_object('attempt_id',a.id,'receipt_id',identity,'response_digest',p_digest,'final_sha256',final.sha256),identity);
 RETURN identity;
END $$;
ALTER FUNCTION app.append_detection_receipt(uuid,uuid,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.append_detection_receipt(uuid,uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.append_detection_receipt(uuid,uuid,text,text,text,text) TO app_worker;

CREATE FUNCTION app.detection_archive(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; saved ops.detection_archives;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO saved FROM ops.detection_archives WHERE task_id=t.id AND user_id=t.user_id AND attempt_id=p_attempt;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF encode(sha256(convert_to(saved.canonical_document,'UTF8')),'hex') IS DISTINCT FROM saved.document_sha256
 OR saved.document IS DISTINCT FROM saved.canonical_document::jsonb THEN RAISE EXCEPTION 'detection_archive_integrity'; END IF;
 RETURN saved.document;
END $$;
ALTER FUNCTION app.detection_archive(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.detection_archive(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.detection_archive(uuid,text,bigint,uuid) TO app_worker;
