-- Recovery evidence is scoped and contains no original body or signed download URL.
CREATE FUNCTION ops.qwen_result_integrity(p_user uuid,p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF p_user IS NULL OR p_task IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT jsonb_build_object('id',q.id,'attempt_id',q.attempt_id,'receipt_id',q.receipt_id,
 'document_sha256',q.document_sha256,'wire_response_sha256',q.document->'wire_response_sha256',
 'generated_sha256',q.document->'generated_sha256','downloaded_sha256',q.document->'downloaded_sha256',
 'billing_usage_integrity',q.document->'billing_usage_integrity','quantities',q.document->'quantities',
 'submission_bound',a.provider_id=q.provider_id AND a.response_digest=q.submission_response_sha256,
 'generated_image_bound',v.sha256=q.document->>'generated_sha256' AND v.width::text=q.document->>'width' AND v.height::text=q.document->>'height',
 'wire_hash_valid',encode(sha256(decode(q.document->>'response_body_base64','base64')),'hex')=q.document->>'wire_response_sha256',
 'canonical_hash_valid',encode(sha256(convert_to(q.canonical_document,'UTF8')),'hex')=q.document_sha256)
 INTO result FROM ops.qwen_result_archives q JOIN app.provider_attempts a
 ON a.id=q.attempt_id AND a.task_id=q.task_id AND a.user_id=q.user_id
 JOIN app.generation_tasks t ON t.id=q.task_id AND t.user_id=q.user_id
 JOIN app.asset_versions v ON v.id=t.generated_version_id AND v.user_id=t.user_id AND v.product_id=t.product_id
 WHERE q.user_id=p_user AND q.task_id=p_task;
 RETURN result;
END $$;
ALTER FUNCTION ops.qwen_result_integrity(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.qwen_result_integrity(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.qwen_result_integrity(uuid,uuid) TO ops_admin;
