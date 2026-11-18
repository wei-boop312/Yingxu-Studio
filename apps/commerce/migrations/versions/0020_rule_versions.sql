ALTER TABLE app.platform_rules ADD COLUMN evidence_sha256 text CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 ADD COLUMN evidence_bucket text, ADD COLUMN evidence_object_key text, ADD COLUMN evidence_storage_version_id text;
CREATE TRIGGER immutable_policies BEFORE UPDATE OR DELETE ON app.platform_rules FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
GRANT INSERT ON app.platform_rules TO app_executor;
CREATE POLICY executor_publish_rule ON app.platform_rules FOR INSERT TO app_executor WITH CHECK(published_at IS NOT NULL);

CREATE FUNCTION app.valid_rule_config(p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE k text; v jsonb;
BEGIN
 IF jsonb_typeof(p) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
 FOR k,v IN SELECT * FROM jsonb_each(p) LOOP
  IF k IN ('min_width','min_height','max_bytes') THEN
   IF v <> 'null'::jsonb AND (jsonb_typeof(v)<>'number' OR v::text !~ '^[1-9][0-9]*$') THEN RETURN false; END IF;
  ELSIF k IN ('min_product_long_edge_ratio','minimum_confidence') THEN
   IF v='null'::jsonb AND k='min_product_long_edge_ratio' THEN CONTINUE; END IF;
   IF jsonb_typeof(v)<>'number' OR (v::text)::numeric<0 OR (v::text)::numeric>1 THEN RETURN false; END IF;
  ELSIF k IN ('require_white_background','forbid_added_text','forbid_added_watermarks','allow_on_product_marks','require_identity_review','complete_rule_set') THEN
   IF jsonb_typeof(v)<>'boolean' THEN RETURN false; END IF;
  ELSE RETURN false;
  END IF;
 END LOOP;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION app.valid_rule_config(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.valid_rule_config(jsonb) TO app_executor;
CREATE FUNCTION ops.publish_rule(p_operation uuid,p_document jsonb,p_bucket text,p_key text,p_storage_version text,p_reason text) RETURNS uuid
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
ALTER FUNCTION ops.publish_rule(uuid,jsonb,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.publish_rule(uuid,jsonb,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.publish_rule(uuid,jsonb,text,text,text,text) TO ops_admin;

CREATE FUNCTION ops.recheck_context(p_task uuid,p_source uuid,p_rules uuid[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; c app.compliance_checks; v app.asset_versions; snapshots jsonb;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task;
 SELECT * INTO c FROM app.compliance_checks WHERE id=p_source AND task_id=p_task;
 IF NOT FOUND OR t.status<>'succeeded' OR t.final_version_id IS DISTINCT FROM c.final_version_id THEN RAISE EXCEPTION 'recheck_source_unavailable'; END IF;
 SELECT * INTO v FROM app.asset_versions WHERE id=c.final_version_id;
 IF v.sha256 IS DISTINCT FROM c.final_sha256 THEN RAISE EXCEPTION 'final_hash_mismatch'; END IF;
 IF p_rules IS NULL OR cardinality(p_rules) NOT BETWEEN 1 AND 10
 OR cardinality(p_rules)<>(SELECT count(DISTINCT x) FROM unnest(p_rules) x)
 OR cardinality(p_rules)<>jsonb_array_length(c.rule_snapshots) THEN RAISE EXCEPTION 'rule_scope_mismatch'; END IF;
 SELECT jsonb_agg(jsonb_build_object('rule_id',r.id,'platform',r.platform,'marketplace',r.marketplace,'category',r.category,'image_type',r.image_type,'version',r.version,'evidence_status',r.evidence_status,'source_urls',r.source_urls,'retrieved_at',r.retrieved_at,'evidence_sha256',r.evidence_sha256,'config',r.config,'config_sha256',r.config_sha256) ORDER BY s.ordinality)
 INTO snapshots FROM jsonb_array_elements(c.rule_snapshots) WITH ORDINALITY s(item,ordinality)
 JOIN app.platform_rules r ON r.id=ANY(p_rules) AND r.published_at IS NOT NULL AND r.platform=s.item->>'platform' AND r.marketplace=s.item->>'marketplace' AND r.category=s.item->>'category' AND r.image_type=s.item->>'image_type';
 IF snapshots IS NULL OR jsonb_array_length(snapshots)<>cardinality(p_rules)
 OR (SELECT count(DISTINCT item->>'rule_id') FROM jsonb_array_elements(snapshots) item)<>cardinality(p_rules) THEN RAISE EXCEPTION 'rule_scope_mismatch'; END IF;
 RETURN jsonb_build_object('user_id',u,'source_check',to_jsonb(c),'final',jsonb_build_object('id',v.id,'sha256',v.sha256,'width',v.width,'height',v.height,'byte_size',v.byte_size,'bucket',v.bucket,'object_key',v.object_key,'storage_version_id',v.storage_version_id),'rules',snapshots);
END $$;
ALTER FUNCTION ops.recheck_context(uuid,uuid,uuid[]) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.recheck_context(uuid,uuid,uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.recheck_context(uuid,uuid,uuid[]) TO ops_admin;

CREATE FUNCTION ops.record_recheck(p_task uuid,p_source uuid,p_rules uuid[],p_operation uuid,p_id uuid,p_results jsonb,p_overall text,p_report_sha text,p_bucket text,p_key text,p_storage_version text,p_reason text) RETURNS uuid
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
  OR prior.after_state->>'check_id' IS DISTINCT FROM p_id::text THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_id;
 END IF;
 ctx:=ops.recheck_context(p_task,p_source,p_rules);
 SELECT * INTO c FROM app.compliance_checks WHERE id=p_source;
 IF jsonb_array_length(p_results)<>cardinality(p_rules) THEN RAISE EXCEPTION 'invalid_recheck'; END IF;
 IF p_overall='compliant' AND (NOT COALESCE((c.facts->>'analysis_complete')::boolean,false) OR c.sampling_selected
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(ctx->'rules') r WHERE r->>'evidence_status' IS DISTINCT FROM 'verified' OR r->'config'->>'complete_rule_set' IS DISTINCT FROM 'true'
 OR COALESCE((r->'config'->>'require_identity_review')::boolean,true))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_results) r WHERE r->>'status' IS DISTINCT FROM 'compliant')) THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,c.user_id,c.product_id,c.task_id,c.final_version_id,c.final_sha256,c.facts,c.facts_schema_version,ctx->'rules',p_results,p_overall,c.sampling_selected,p_report_sha,p_bucket,p_key,p_storage_version);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'recheck_rules','task',p_task,p_reason,p_report_sha,jsonb_build_object('source_check_id',p_source),jsonb_build_object('source_check_id',p_source,'check_id',p_id,'rule_ids',p_rules),p_operation);
 RETURN p_id;
END $$;
ALTER FUNCTION ops.record_recheck(uuid,uuid,uuid[],uuid,uuid,jsonb,text,text,text,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.record_recheck(uuid,uuid,uuid[],uuid,uuid,jsonb,text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.record_recheck(uuid,uuid,uuid[],uuid,uuid,jsonb,text,text,text,text,text,text) TO ops_admin;
