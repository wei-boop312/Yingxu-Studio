-- Narrow integrity evidence for recovery review; no raw response or polling URL is exposed.
CREATE FUNCTION ops.generation_archive_integrity(p_user uuid,p_task uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF p_user IS NULL OR p_task IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT jsonb_build_object('attempt_id',g.attempt_id,'receipt_id',g.receipt_id,
 'document_sha256',g.document_sha256,'wire_response_sha256',g.response_digest,'request_sha256',g.request_sha256,
 'intent_bound',g.request_sha256=a.request_hash,
 'wire_hash_valid',encode(sha256(decode(g.document->>'response_body_base64','base64')),'hex')=g.response_digest,
 'canonical_hash_valid',encode(sha256(convert_to(g.canonical_document,'UTF8')),'hex')=g.document_sha256)
 INTO result FROM ops.generation_archives g JOIN app.provider_attempts a
 ON a.id=g.attempt_id AND a.task_id=g.task_id AND a.user_id=g.user_id
 WHERE g.user_id=p_user AND g.task_id=p_task;
 RETURN result;
END $$;
ALTER FUNCTION ops.generation_archive_integrity(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.generation_archive_integrity(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.generation_archive_integrity(uuid,uuid) TO ops_admin;
