-- Correct the new-rule publishing scope; existing immutable rows are preserved.
CREATE OR REPLACE FUNCTION ops.publish_rule(p_operation uuid,p_document jsonb,p_bucket text,p_key text,p_storage_version text,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior ops.audit_events; identity uuid; digest text; url text; host text; verified boolean;
BEGIN
 IF p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR jsonb_typeof(p_document) IS DISTINCT FROM 'object'
 OR p_document->>'platform' NOT IN ('amazon','temu','ozon')
 OR p_document->>'evidence_status' NOT IN ('verified','unverified')
 OR NOT app.valid_rule_config(p_document->'config')
 OR jsonb_typeof(p_document->'source_urls') IS DISTINCT FROM 'array'
 THEN RAISE EXCEPTION 'invalid_rule'; END IF;
 FOREACH host IN ARRAY ARRAY['platform','marketplace','category','image_type','version','evidence_status'] LOOP
  IF jsonb_typeof(p_document->host) IS DISTINCT FROM 'string' OR length(trim(p_document->>host)) NOT BETWEEN 1 AND 255 THEN RAISE EXCEPTION 'invalid_rule'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_document->'source_urls') value WHERE jsonb_typeof(value) IS DISTINCT FROM 'string') THEN RAISE EXCEPTION 'invalid_evidence'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_document) k WHERE k NOT IN ('platform','marketplace','category','image_type','version','evidence_status','source_urls','retrieved_at','evidence_sha256','config')) THEN RAISE EXCEPTION 'invalid_rule'; END IF;
 IF jsonb_array_length(p_document->'source_urls')>20 THEN RAISE EXCEPTION 'invalid_rule'; END IF;
 verified:=p_document->>'evidence_status'='verified';
 FOR url IN SELECT jsonb_array_elements_text(p_document->'source_urls') LOOP
  IF length(url)>2048 OR url !~ '^https://[^/@?#:]+([/:?#]|$)' THEN RAISE EXCEPTION 'invalid_evidence'; END IF;
  host:=lower(substring(url FROM '^https://([^/:?#]+)'));
  IF verified AND NOT (CASE p_document->>'platform'
   WHEN 'amazon' THEN host ~ '(^|\.)amazon\.com$'
   WHEN 'temu' THEN host ~ '(^|\.)(temu\.com|kuajingmaihuo\.com)$'
   ELSE host ~ '(^|\.)ozon\.ru$' END) THEN RAISE EXCEPTION 'invalid_evidence'; END IF;
 END LOOP;
 IF verified AND (jsonb_array_length(p_document->'source_urls')=0 OR p_document->>'retrieved_at' IS NULL
 OR (p_document->>'retrieved_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
 OR p_document->>'evidence_sha256' IS NULL OR p_document->>'evidence_sha256' !~ '^[0-9a-f]{64}$'
 OR p_bucket IS NULL OR length(p_bucket)=0 OR p_key IS NULL OR length(p_key)=0) THEN RAISE EXCEPTION 'invalid_evidence'; END IF;
 digest:=encode(sha256(convert_to(p_document::text,'UTF8')),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'publish_rule' OR prior.reason<>p_reason OR prior.evidence_digest<>digest
  OR prior.after_state->>'bucket' IS DISTINCT FROM p_bucket OR prior.after_state->>'key' IS DISTINCT FROM p_key
  OR prior.after_state->>'storage_version' IS DISTINCT FROM p_storage_version THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN prior.target_id;
 END IF;
 INSERT INTO app.platform_rules(platform,marketplace,category,image_type,version,evidence_status,source_urls,retrieved_at,config,config_sha256,published_at,evidence_sha256,evidence_bucket,evidence_object_key,evidence_storage_version_id)
 VALUES(p_document->>'platform',p_document->>'marketplace',p_document->>'category',p_document->>'image_type',p_document->>'version',p_document->>'evidence_status',p_document->'source_urls',(p_document->>'retrieved_at')::timestamptz,p_document->'config',encode(sha256(convert_to((p_document->'config')::text,'UTF8')),'hex'),clock_timestamp(),p_document->>'evidence_sha256',p_bucket,p_key,p_storage_version) RETURNING id INTO identity;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'publish_rule','platform_rule',identity,p_reason,digest,jsonb_build_object('version',p_document->>'version','evidence_status',p_document->>'evidence_status','bucket',p_bucket,'key',p_key,'storage_version',p_storage_version),p_operation);
 RETURN identity;
END $$;
ALTER FUNCTION ops.publish_rule(uuid,jsonb,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.publish_rule(uuid,jsonb,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.publish_rule(uuid,jsonb,text,text,text,text) TO ops_admin;
