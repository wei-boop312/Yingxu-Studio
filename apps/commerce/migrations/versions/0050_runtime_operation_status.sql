CREATE FUNCTION ops.runtime_operation_status(p_sha text,p_registration uuid,p_preparation uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE registration_count integer; preparation ops.audit_events; preparation_count integer:=0; quote_count integer:=0;
BEGIN
 IF p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_registration IS NULL OR p_preparation IS NULL THEN RAISE EXCEPTION 'invalid_runtime_operation_scope'; END IF;
 SELECT count(*) INTO registration_count FROM ops.audit_events WHERE request_id=p_registration AND action='provider_runtime_register' AND evidence_digest=p_sha;
 SELECT * INTO preparation FROM ops.audit_events WHERE request_id=p_preparation AND action='runtime_quote_prepare' AND evidence_digest=p_sha;
 IF FOUND THEN
  preparation_count:=1;
  PERFORM set_config('app.user_id',preparation.after_state->>'user_id',true);
  SELECT count(*) INTO quote_count FROM app.quotes WHERE id=preparation.target_id
  AND user_id=(preparation.after_state->>'user_id')::uuid AND input_sha256=preparation.after_state->>'input_sha256'
  AND input_snapshot->'_execution'->>'provider_runtime_sha256'=p_sha;
 END IF;
 RETURN jsonb_build_object('runtime_registered',EXISTS(SELECT 1 FROM ops.provider_runtime_versions WHERE document_sha256=p_sha),
 'registration_audit_count',registration_count,'preparation_audit_count',preparation_count,'prepared_quote_count',quote_count);
END $$;
ALTER FUNCTION ops.runtime_operation_status(text,uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.runtime_operation_status(text,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.runtime_operation_status(text,uuid,uuid) TO ops_admin;
