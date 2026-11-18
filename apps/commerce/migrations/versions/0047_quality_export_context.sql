-- No broad table grants, inference, report writes, ledger changes or paid transport.
CREATE FUNCTION ops.quality_export_context(p_user uuid,p_task uuid,p_check uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; result jsonb;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL OR p_user IS DISTINCT FROM u THEN RAISE EXCEPTION 'quality_source_unavailable'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT jsonb_build_object('user_id',u,'task_id',t.id,'check_id',c.id,
 'final',jsonb_build_object('sha256',v.sha256,'width',v.width,'height',v.height,'byte_size',v.byte_size,'bucket',v.bucket,'object_key',v.object_key,'storage_version_id',v.storage_version_id),
 'report',jsonb_build_object('sha256',c.report_sha256,'bucket',c.report_bucket,'object_key',c.report_object_key,'storage_version_id',c.report_storage_version_id,'facts',c.facts,'per_platform',c.per_platform_results))
 INTO result FROM app.generation_tasks t
 JOIN app.compliance_checks c ON c.task_id=t.id AND c.user_id=t.user_id AND c.product_id=t.product_id AND c.id=p_check
 JOIN app.asset_versions v ON v.id=c.final_version_id AND v.user_id=t.user_id AND v.product_id=t.product_id AND v.id=t.final_version_id AND v.sha256=c.final_sha256
 JOIN app.assets a ON a.id=v.asset_id AND a.user_id=v.user_id
 WHERE t.id=p_task AND t.user_id=u AND t.status='succeeded' AND v.canonical AND a.kind='final' AND a.status='ready';
 IF result IS NULL THEN RAISE EXCEPTION 'quality_source_unavailable'; END IF;
 RETURN result;
END $$;
ALTER FUNCTION ops.quality_export_context(uuid,uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.quality_export_context(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.quality_export_context(uuid,uuid,uuid) TO ops_admin;
