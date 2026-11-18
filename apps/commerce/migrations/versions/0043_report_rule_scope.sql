CREATE FUNCTION app.rule_results_match_scope(p_rules jsonb,p_results jsonb,p_overall text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE r jsonb; result jsonb; i integer; field text; expected text; overall text:='compliant';
BEGIN
 IF jsonb_typeof(p_rules) IS DISTINCT FROM 'array' OR jsonb_typeof(p_results) IS DISTINCT FROM 'array'
 OR jsonb_array_length(p_rules)=0 OR jsonb_array_length(p_rules)<>jsonb_array_length(p_results) THEN RETURN false; END IF;
 FOR i IN 0..jsonb_array_length(p_rules)-1 LOOP
  r:=p_rules->i; result:=p_results->i;
  IF jsonb_typeof(r) IS DISTINCT FROM 'object' OR jsonb_typeof(result) IS DISTINCT FROM 'object'
  OR jsonb_typeof(result->'status') IS DISTINCT FROM 'string' OR result->>'status' NOT IN ('compliant','non_compliant','manual_review') THEN RETURN false; END IF;
  FOREACH field IN ARRAY ARRAY['platform','marketplace','category','image_type','rule_version','evidence_status'] LOOP
   expected:=CASE WHEN field='rule_version' THEN COALESCE(r->>'version','unverified')
    WHEN field='evidence_status' THEN COALESCE(r->>field,'unverified') ELSE COALESCE(r->>field,'') END;
   IF jsonb_typeof(result->field) IS DISTINCT FROM 'string' OR result->>field IS DISTINCT FROM expected THEN RETURN false; END IF;
  END LOOP;
  IF result->>'status'='non_compliant' THEN overall:='non_compliant';
  ELSIF result->>'status'='manual_review' AND overall<>'non_compliant' THEN overall:='manual_review'; END IF;
 END LOOP;
 RETURN p_overall IS NOT DISTINCT FROM overall;
END $$;
REVOKE ALL ON FUNCTION app.rule_results_match_scope(jsonb,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.rule_results_match_scope(jsonb,jsonb,text) TO app_executor;

CREATE OR REPLACE FUNCTION app.record_report(p_task uuid,p_owner text,p_fence bigint,p_id uuid,p_final_sha text,p_facts jsonb,p_results jsonb,p_overall text,p_sampling boolean,p_report_sha text,p_bucket text,p_key text,p_storage_version text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; v app.asset_versions; d ops.detection_manual_deliveries;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF t.compliance_id IS NOT NULL THEN RETURN t.compliance_id; END IF;
 IF t.phase NOT IN ('detecting','validating_rules') THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 SELECT * INTO v FROM app.asset_versions WHERE id=t.final_version_id;
 IF NOT FOUND OR v.sha256 IS DISTINCT FROM p_final_sha THEN RAISE EXCEPTION 'final_hash_mismatch'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='detect') THEN RAISE EXCEPTION 'detection_intent_required'; END IF;
 SELECT * INTO d FROM ops.detection_manual_deliveries WHERE task_id=t.id AND user_id=t.user_id;
 IF FOUND THEN
  IF t.phase<>'validating_rules' OR d.final_version_id IS DISTINCT FROM v.id OR d.final_sha256 IS DISTINCT FROM p_final_sha
  OR d.facts IS DISTINCT FROM p_facts OR p_overall='compliant' THEN RAISE EXCEPTION 'invalid_manual_detection_report'; END IF;
 ELSE
  IF t.phase<>'detecting' THEN RAISE EXCEPTION 'manual_detection_review_required'; END IF;
  IF NOT EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='detect' AND provider_id IS NOT NULL AND response_digest IS NOT NULL AND status IN ('submitted','polling','succeeded'))
  THEN RAISE EXCEPTION 'detection_receipt_required'; END IF;
 END IF;
 IF NOT app.rule_results_match_scope(t.input_snapshot->'_execution'->'rules',p_results,p_overall) THEN RAISE EXCEPTION 'report_rule_scope_mismatch'; END IF;
 IF p_overall='compliant' AND (NOT app.rule_result_can_pass(p_facts,t.input_snapshot->'_execution'->'rules',p_results,p_sampling)
 OR NOT app.rule_local_constraints_pass(t.input_snapshot->'_execution'->'rules',v.width,v.height,v.byte_size)
 OR p_facts->>'image_width' IS DISTINCT FROM v.width::text OR p_facts->>'image_height' IS DISTINCT FROM v.height::text) THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,t.user_id,t.product_id,t.id,v.id,v.sha256,p_facts,1,t.input_snapshot->'_execution'->'rules',p_results,p_overall,p_sampling,p_report_sha,p_bucket,p_key,p_storage_version);
 IF d.id IS NULL THEN
  UPDATE app.provider_attempts SET status='succeeded',finished_at=now() WHERE task_id=t.id AND step='detect';
 END IF;
 UPDATE app.generation_tasks SET compliance_id=p_id,phase='finalizing',revision=revision+1,updated_at=now() WHERE id=t.id;
 RETURN p_id;
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
 IF NOT app.rule_results_match_scope(ctx->'rules',p_results,p_overall) THEN RAISE EXCEPTION 'report_rule_scope_mismatch'; END IF;
 IF p_overall='compliant' AND (NOT app.rule_result_can_pass(c.facts,ctx->'rules',p_results,c.sampling_selected)
 OR NOT app.rule_local_constraints_pass(ctx->'rules',(ctx->'final'->>'width')::integer,(ctx->'final'->>'height')::integer,(ctx->'final'->>'byte_size')::bigint)
 OR c.facts->>'image_width' IS DISTINCT FROM ctx->'final'->>'width' OR c.facts->>'image_height' IS DISTINCT FROM ctx->'final'->>'height') THEN RAISE EXCEPTION 'unverified_cannot_pass'; END IF;
 INSERT INTO app.compliance_checks(id,user_id,product_id,task_id,final_version_id,final_sha256,facts,facts_schema_version,rule_snapshots,per_platform_results,auto_overall,sampling_selected,report_sha256,report_bucket,report_object_key,report_storage_version_id)
 VALUES(p_id,c.user_id,c.product_id,c.task_id,c.final_version_id,c.final_sha256,c.facts,c.facts_schema_version,ctx->'rules',p_results,p_overall,c.sampling_selected,p_report_sha,p_bucket,p_key,p_storage_version);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'recheck_rules','task',p_task,p_reason,p_report_sha,jsonb_build_object('source_check_id',p_source),jsonb_build_object('source_check_id',p_source,'check_id',p_id,'rule_ids',p_rules,'bucket',p_bucket,'key',p_key,'storage_version',p_storage_version),p_operation);
 RETURN p_id;
END $$;
