CREATE FUNCTION ops.recheck_operation(p_task uuid,p_source uuid,p_rules uuid[],p_operation uuid,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; prior ops.audit_events; saved app.compliance_checks;
BEGIN
 IF p_task IS NULL OR p_source IS NULL OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_rules IS NULL OR cardinality(p_rules) NOT BETWEEN 1 AND 10
 OR cardinality(p_rules)<>(SELECT count(DISTINCT x) FROM unnest(p_rules) x) THEN RAISE EXCEPTION 'invalid_recheck_operation'; END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF prior.action IS DISTINCT FROM 'recheck_rules' OR prior.target_type IS DISTINCT FROM 'task' OR prior.target_id IS DISTINCT FROM p_task
 OR prior.reason IS DISTINCT FROM p_reason OR prior.after_state->>'source_check_id' IS DISTINCT FROM p_source::text
 OR prior.after_state->'rule_ids' IS DISTINCT FROM to_jsonb(p_rules) THEN RAISE EXCEPTION 'operation_conflict'; END IF;
 SELECT * INTO saved FROM app.compliance_checks WHERE id=(prior.after_state->>'check_id')::uuid AND user_id=u AND task_id=p_task;
 IF NOT FOUND OR saved.report_sha256 IS DISTINCT FROM prior.evidence_digest
 OR saved.report_bucket IS DISTINCT FROM prior.after_state->>'bucket'
 OR saved.report_object_key IS DISTINCT FROM prior.after_state->>'key'
 OR saved.report_storage_version_id IS DISTINCT FROM prior.after_state->>'storage_version'
 THEN RAISE EXCEPTION 'recheck_receipt_integrity'; END IF;
 RETURN jsonb_build_object('check_id',saved.id,'auto_overall',saved.auto_overall,'report_sha256',saved.report_sha256,
 'report_bucket',saved.report_bucket,'report_object_key',saved.report_object_key,'report_storage_version_id',saved.report_storage_version_id);
END $$;
ALTER FUNCTION ops.recheck_operation(uuid,uuid,uuid[],uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.recheck_operation(uuid,uuid,uuid[],uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.recheck_operation(uuid,uuid,uuid[],uuid,text) TO ops_admin;
