CREATE TABLE ops.provider_runtime_revocations (
 runtime_sha256 text PRIMARY KEY REFERENCES ops.provider_runtime_versions(document_sha256),
 operation_id uuid NOT NULL UNIQUE, actor text NOT NULL, reason text NOT NULL,
 revoked_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE ops.provider_runtime_revocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_runtime_revocations FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_revocation_executor ON ops.provider_runtime_revocations TO app_executor USING(true) WITH CHECK(true);
GRANT SELECT,INSERT ON ops.provider_runtime_revocations TO app_executor;
CREATE TRIGGER immutable_runtime_revocation BEFORE UPDATE OR DELETE ON ops.provider_runtime_revocations FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION ops.revoke_provider_runtime(p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE identity uuid; prior ops.audit_events;
BEGIN
 IF p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$' OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'invalid_runtime_revocation'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'provider_runtime_revoke' OR prior.evidence_digest IS DISTINCT FROM p_sha OR prior.reason IS DISTINCT FROM p_reason THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN prior.target_id;
 END IF;
 -- Exclusive against the shared lock held until a quote/task/intent commit.
 PERFORM pg_advisory_xact_lock(hashtextextended('provider-runtime:'||p_sha,0));
 SELECT id INTO identity FROM ops.provider_runtime_versions WHERE document_sha256=p_sha;
 IF NOT FOUND THEN RAISE EXCEPTION 'runtime_configuration_not_registered'; END IF;
 INSERT INTO ops.provider_runtime_revocations(runtime_sha256,operation_id,actor,reason)
 VALUES(p_sha,p_operation,session_user,p_reason) ON CONFLICT(runtime_sha256) DO NOTHING;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'provider_runtime_revoke','provider_runtime',identity,p_reason,p_sha,jsonb_build_object('revoked',true,'previously_committed_intents_may_have_outcomes',true),p_operation);
 RETURN identity;
END $$;
ALTER FUNCTION ops.revoke_provider_runtime(text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.revoke_provider_runtime(text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.revoke_provider_runtime(text,uuid,text) TO ops_admin;

CREATE OR REPLACE FUNCTION ops.provider_runtime_context(p_sha text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',r.id,'document',r.document,'document_sha256',r.document_sha256,'execution_enabled',false,'merchant_identity_verified',false,
 'revoked',EXISTS(SELECT 1 FROM ops.provider_runtime_revocations v WHERE v.runtime_sha256=r.document_sha256))
 FROM ops.provider_runtime_versions r WHERE r.document_sha256=p_sha
$$;

CREATE OR REPLACE FUNCTION ops.assert_runtime_configuration(p_snapshot jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE execution jsonb; stored ops.provider_runtime_versions; binding jsonb; field text;
BEGIN
 execution:=p_snapshot->'_execution';
 IF execution IS NULL OR NOT COALESCE(execution ? 'provider_runtime_sha256',false) THEN RETURN; END IF;
 IF jsonb_typeof(execution->'provider_runtime_sha256') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('provider-runtime:'||(execution->>'provider_runtime_sha256'),0));
 SELECT * INTO stored FROM ops.provider_runtime_versions WHERE document_sha256=execution->>'provider_runtime_sha256';
 IF NOT FOUND THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 IF EXISTS(SELECT 1 FROM ops.provider_runtime_revocations WHERE runtime_sha256=stored.document_sha256) THEN RAISE EXCEPTION 'provider_runtime_revoked'; END IF;
 binding:=ops.validated_runtime_binding(stored.canonical_document,stored.document_sha256);
 IF execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock' THEN RAISE EXCEPTION 'runtime_profile_not_enabled'; END IF;
 FOREACH field IN ARRAY ARRAY['provider_runtime_sha256','provider_models','provider_endpoints','provider_accounts'] LOOP
  IF execution->field IS DISTINCT FROM binding->field THEN RAISE EXCEPTION 'registered_runtime_binding_mismatch'; END IF;
 END LOOP;
END $$;
CREATE TRIGGER enforce_task_runtime BEFORE INSERT ON app.generation_tasks FOR EACH ROW EXECUTE FUNCTION app.enforce_quote_runtime();
