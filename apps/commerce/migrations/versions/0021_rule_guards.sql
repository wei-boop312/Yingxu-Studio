CREATE FUNCTION app.rule_result_can_pass(p_facts jsonb,p_rules jsonb,p_results jsonb,p_sampling boolean) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE r jsonb; result jsonb; i integer;
BEGIN
 IF p_sampling IS DISTINCT FROM false OR p_facts->>'analysis_complete' IS DISTINCT FROM 'true'
 OR jsonb_typeof(p_facts->'uncertainties') IS DISTINCT FROM 'array' OR jsonb_array_length(p_facts->'uncertainties')<>0
 OR jsonb_typeof(p_rules) IS DISTINCT FROM 'array' OR jsonb_typeof(p_results) IS DISTINCT FROM 'array'
 OR jsonb_array_length(p_rules)=0 OR jsonb_array_length(p_rules)<>jsonb_array_length(p_results) THEN RETURN false; END IF;
 FOR i IN 0..jsonb_array_length(p_rules)-1 LOOP
  r:=p_rules->i; result:=p_results->i;
  IF r->>'evidence_status' IS DISTINCT FROM 'verified' OR r->'config'->>'complete_rule_set' IS DISTINCT FROM 'true'
  OR r->'config'->>'require_identity_review' IS DISTINCT FROM 'false'
  OR result->>'status' IS DISTINCT FROM 'compliant'
  OR result->>'platform' IS DISTINCT FROM r->>'platform' OR result->>'marketplace' IS DISTINCT FROM r->>'marketplace'
  OR result->>'image_type' IS DISTINCT FROM r->>'image_type' OR result->>'rule_version' IS DISTINCT FROM r->>'version'
  OR jsonb_typeof(result->'reasons') IS DISTINCT FROM 'array' OR jsonb_array_length(result->'reasons')<>0 THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION app.rule_result_can_pass(jsonb,jsonb,jsonb,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.rule_result_can_pass(jsonb,jsonb,jsonb,boolean) TO app_executor;
CREATE OR REPLACE FUNCTION ops.publish_rule(p_operation uuid,p_document jsonb,p_bucket text,p_key text,p_storage_version text,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior ops.audit_events; identity uuid; digest text; url text; host text; verified boolean;
BEGIN
 IF p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR jsonb_typeof(p_document) IS DISTINCT FROM 'object'
 OR p_document->>'platform' NOT IN ('amazon','ebay','shopify')
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
   WHEN 'ebay' THEN host ~ '(^|\.)(ebay\.com|ebayinc\.com)$'
   ELSE host ~ '(^|\.)shopify\.com$' END) THEN RAISE EXCEPTION 'invalid_evidence'; END IF;
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
CREATE OR REPLACE FUNCTION ops.record_recheck(p_task uuid,p_source uuid,p_rules uuid[],p_operation uuid,p_id uuid,p_results jsonb,p_overall text,p_report_sha text,p_bucket text,p_key text,p_storage_version text,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ctx jsonb; c app.compliance_checks; prior ops.audit_events;
BEGIN
 IF p_operation IS NULL OR p_id IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_report_sha IS NULL OR p_report_sha !~ '^[0-9a-f]{64}$'
 OR p_overall NOT IN ('compliant','non_compliant','manual_review') OR p_overall IS NULL
 OR jsonb_typeof(p_results) IS DISTINCT FROM 'array' OR p_bucket IS NULL OR p_bucket='' OR p_key IS NULL OR p_key='' THEN RAISE EXCEPTION 'invalid_recheck'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'recheck_rules' OR prior.target_id<>p_task OR prior.reason<>p_reason
  OR prior.evidence_digest<>p_report_sha OR prior.after_state->>'source_check_id' IS DISTINCT FROM p_source::text
  OR prior.after_state->'rule_ids' IS DISTINCT FROM to_jsonb(p_rules)
  OR prior.after_state->>'check_id' IS DISTINCT FROM p_id::text
  OR prior.after_state->>'bucket' IS DISTINCT FROM p_bucket OR prior.after_state->>'key' IS DISTINCT FROM p_key OR prior.after_state->>'storage_version' IS DISTINCT FROM p_storage_version THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_id;
 END IF;
 ctx:=ops.recheck_context(p_task,p_source,p_rules);
 SELECT * INTO c FROM app.compliance_checks WHERE id=p_source;
 IF jsonb_array_length(p_results)<>cardinality(p_rules) THEN RAISE EXCEPTION 'invalid_recheck'; END IF;
 IF p_overall='compliant' AND NOT app.rule_result_can_pass(c.facts,ctx->'rules',p_results,c.sampling_selected) THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,c.user_id,c.product_id,c.task_id,c.final_version_id,c.final_sha256,c.facts,c.facts_schema_version,ctx->'rules',p_results,p_overall,c.sampling_selected,p_report_sha,p_bucket,p_key,p_storage_version);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'recheck_rules','task',p_task,p_reason,p_report_sha,jsonb_build_object('source_check_id',p_source),jsonb_build_object('source_check_id',p_source,'check_id',p_id,'rule_ids',p_rules,'bucket',p_bucket,'key',p_key,'storage_version',p_storage_version),p_operation);
 RETURN p_id;
END $$;
CREATE OR REPLACE FUNCTION app.record_report(p_task uuid,p_owner text,p_fence bigint,p_id uuid,p_final_sha text,p_facts jsonb,p_results jsonb,p_overall text,p_sampling boolean,p_report_sha text,p_bucket text,p_key text,p_storage_version text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; v app.asset_versions;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF t.compliance_id IS NOT NULL THEN RETURN t.compliance_id; END IF;
 IF t.phase<>'detecting' THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 SELECT * INTO v FROM app.asset_versions WHERE id=t.final_version_id;
 IF NOT FOUND OR v.sha256 IS DISTINCT FROM p_final_sha THEN RAISE EXCEPTION 'final_hash_mismatch'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='detect') THEN RAISE EXCEPTION 'detection_intent_required'; END IF;
 IF p_overall='compliant' AND NOT app.rule_result_can_pass(p_facts,t.input_snapshot->'_execution'->'rules',p_results,p_sampling) THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,t.user_id,t.product_id,t.id,v.id,v.sha256,p_facts,1,t.input_snapshot->'_execution'->'rules',p_results,p_overall,p_sampling,p_report_sha,p_bucket,p_key,p_storage_version);
 UPDATE app.provider_attempts SET status='succeeded',finished_at=now() WHERE task_id=t.id AND step='detect';
 UPDATE app.generation_tasks SET compliance_id=p_id,phase='finalizing',revision=revision+1,updated_at=now() WHERE id=t.id;
 RETURN p_id;
END $$;
