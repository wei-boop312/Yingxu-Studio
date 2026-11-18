-- Storage pointer stays inside the private operator process, never public API.
CREATE FUNCTION ops.detection_review_artifact(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; result jsonb;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT jsonb_build_object('version_id',v.id,'sha256',v.sha256,'width',v.width,'height',v.height,
 'bucket',v.bucket,'object_key',v.object_key,'storage_version_id',v.storage_version_id)
 INTO result FROM app.generation_tasks t
 JOIN app.asset_versions v ON v.id=t.final_version_id AND v.user_id=t.user_id AND v.product_id=t.product_id
 JOIN app.assets a ON a.id=v.asset_id AND a.user_id=v.user_id
 WHERE t.id=p_task AND t.user_id=u AND t.status='uncertain' AND t.credits_status='frozen'
 AND t.phase IN ('detecting','validating_rules','finalizing') AND v.canonical AND a.status='ready' AND a.kind='final'
 AND EXISTS(SELECT 1 FROM app.provider_attempts p WHERE p.task_id=t.id AND p.user_id=u AND p.step='generate' AND p.status='succeeded' AND p.provider_id IS NOT NULL)
 AND EXISTS(SELECT 1 FROM app.provider_attempts p WHERE p.task_id=t.id AND p.user_id=u AND p.step='detect' AND p.status IN ('intent_recorded','submitted','polling','uncertain','failed'));
 IF result IS NULL THEN RAISE EXCEPTION 'detection_review_artifact_unavailable'; END IF;
 RETURN result;
END $$;
ALTER FUNCTION ops.detection_review_artifact(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.detection_review_artifact(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.detection_review_artifact(uuid) TO ops_admin;
