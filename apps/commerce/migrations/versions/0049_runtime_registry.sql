CREATE TABLE ops.provider_runtime_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 canonical_document text NOT NULL CHECK(octet_length(canonical_document)<=16384),
 document jsonb NOT NULL CHECK(jsonb_typeof(document)='object'),
 document_sha256 text NOT NULL UNIQUE CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL, reason text NOT NULL,
 CHECK(document_sha256=encode(sha256(convert_to(canonical_document,'UTF8')),'hex')),
 CHECK(document=canonical_document::jsonb)
);
ALTER TABLE ops.provider_runtime_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.provider_runtime_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_executor ON ops.provider_runtime_versions TO app_executor USING(true) WITH CHECK(true);
GRANT SELECT,INSERT ON ops.provider_runtime_versions TO app_executor;
CREATE TRIGGER immutable_provider_runtime BEFORE UPDATE OR DELETE ON ops.provider_runtime_versions FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE FUNCTION ops.validated_runtime_binding(p_canonical text,p_sha text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE d jsonb; name text; field text; step jsonb;
BEGIN
 IF p_canonical IS NULL OR octet_length(p_canonical)>16384 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_canonical,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
 d:=p_canonical::jsonb;
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR NOT(d ?& ARRAY['schema_version','version','generate','detect'])
 OR (SELECT count(*) FROM jsonb_object_keys(d))<>4 OR d->'schema_version' IS DISTINCT FROM '1'::jsonb OR d->>'schema_version' IS DISTINCT FROM '1'
 OR jsonb_typeof(d->'version') IS DISTINCT FROM 'string' OR d->>'version' !~ '^[A-Za-z0-9_.-]{1,80}$' THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
 FOREACH name IN ARRAY ARRAY['generate','detect'] LOOP
  step:=d->name;
  IF jsonb_typeof(step) IS DISTINCT FROM 'object'
  OR NOT(step ?& ARRAY['provider','model','endpoint_origin','merchant_fingerprint','credential_fingerprint','billing_group'])
  OR (SELECT count(*) FROM jsonb_object_keys(step))<>6 THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  FOREACH field IN ARRAY ARRAY['provider','model','endpoint_origin','merchant_fingerprint','credential_fingerprint','billing_group'] LOOP
   IF jsonb_typeof(step->field) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  END LOOP;
  IF step->>'merchant_fingerprint' !~ '^[0-9a-f]{64}$' OR step->>'credential_fingerprint' !~ '^[0-9a-f]{64}$'
  OR length(step->>'billing_group') NOT BETWEEN 1 AND 160 OR trim(step->>'billing_group')='' OR step->>'billing_group' ~ '[[:cntrl:]]'
  THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  IF name='generate' THEN
   IF step->>'provider'<>'bfl' OR step->>'model'<>'flux-3-image' OR step->>'endpoint_origin'<>'https://api.bfl.ai' THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  ELSE
   IF step->>'model'<>'gemini-3.8-flash' OR step->>'provider' NOT IN ('google_gemini','hubway_gemini')
   OR (step->>'provider'='google_gemini' AND step->>'endpoint_origin'<>'https://generativelanguage.googleapis.com')
   OR (step->>'provider'='hubway_gemini' AND step->>'endpoint_origin' NOT IN ('https://hubway.cc','https://z.hubway.cc')) THEN RAISE EXCEPTION 'invalid_runtime_document'; END IF;
  END IF;
 END LOOP;
 RETURN jsonb_build_object('provider_runtime_sha256',p_sha,'provider_models',jsonb_build_object('generate','flux-3-image','detect','gemini-3.8-flash'),
 'provider_endpoints',jsonb_build_object('generate','https://api.bfl.ai/v1/flux-3-image','detect',(d->'detect'->>'endpoint_origin')||'/v1beta/models/gemini-3.8-flash:generateContent'),
 'provider_accounts',jsonb_build_object('generate',(d->'generate')-'provider'-'model'-'endpoint_origin'-'credential_fingerprint','detect',(d->'detect')-'provider'-'model'-'endpoint_origin'-'credential_fingerprint'));
END $$;
ALTER FUNCTION ops.validated_runtime_binding(text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.validated_runtime_binding(text,text) FROM PUBLIC;

CREATE FUNCTION ops.register_provider_runtime(p_canonical text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior ops.audit_events; identity uuid; existing text;
BEGIN
 IF p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'invalid_runtime_review'; END IF;
 PERFORM ops.validated_runtime_binding(p_canonical,p_sha);
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'provider_runtime_register' OR prior.evidence_digest IS DISTINCT FROM p_sha OR prior.reason IS DISTINCT FROM p_reason THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN prior.target_id;
 END IF;
 INSERT INTO ops.provider_runtime_versions(canonical_document,document,document_sha256,actor,reason)
 VALUES(p_canonical,p_canonical::jsonb,p_sha,session_user,p_reason) ON CONFLICT(document_sha256) DO NOTHING;
 SELECT id,canonical_document INTO identity,existing FROM ops.provider_runtime_versions WHERE document_sha256=p_sha;
 IF identity IS NULL OR existing IS DISTINCT FROM p_canonical THEN RAISE EXCEPTION 'runtime_document_conflict'; END IF;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,after_state,request_id)
 VALUES(session_user,'provider_runtime_register','provider_runtime',identity,p_reason,p_sha,jsonb_build_object('document_sha256',p_sha,'execution_enabled',false,'merchant_identity_verified',false),p_operation);
 RETURN identity;
END $$;
ALTER FUNCTION ops.register_provider_runtime(text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.register_provider_runtime(text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.register_provider_runtime(text,text,uuid,text) TO ops_admin;

CREATE FUNCTION ops.provider_runtime_context(p_sha text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',id,'document',document,'document_sha256',document_sha256,'execution_enabled',false,'merchant_identity_verified',false)
 FROM ops.provider_runtime_versions WHERE document_sha256=p_sha
$$;
ALTER FUNCTION ops.provider_runtime_context(text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.provider_runtime_context(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.provider_runtime_context(text) TO ops_admin;

CREATE FUNCTION ops.assert_runtime_configuration(p_snapshot jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE execution jsonb; stored ops.provider_runtime_versions; binding jsonb; field text;
BEGIN
 execution:=p_snapshot->'_execution';
 IF execution IS NULL OR NOT COALESCE(execution ? 'provider_runtime_sha256',false) THEN RETURN; END IF;
 IF jsonb_typeof(execution->'provider_runtime_sha256') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 SELECT * INTO stored FROM ops.provider_runtime_versions WHERE document_sha256=execution->>'provider_runtime_sha256';
 IF NOT FOUND THEN RAISE EXCEPTION 'unregistered_runtime_configuration'; END IF;
 binding:=ops.validated_runtime_binding(stored.canonical_document,stored.document_sha256);
 IF execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock' THEN RAISE EXCEPTION 'runtime_profile_not_enabled'; END IF;
 FOREACH field IN ARRAY ARRAY['provider_runtime_sha256','provider_models','provider_endpoints','provider_accounts'] LOOP
  IF execution->field IS DISTINCT FROM binding->field THEN RAISE EXCEPTION 'registered_runtime_binding_mismatch'; END IF;
 END LOOP;
END $$;
ALTER FUNCTION ops.assert_runtime_configuration(jsonb) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.assert_runtime_configuration(jsonb) FROM PUBLIC;

CREATE FUNCTION app.enforce_quote_runtime() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM ops.assert_runtime_configuration(NEW.input_snapshot);
 RETURN NEW;
END $$;
ALTER FUNCTION app.enforce_quote_runtime() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.enforce_quote_runtime() FROM PUBLIC;
CREATE TRIGGER enforce_quote_runtime BEFORE INSERT ON app.quotes FOR EACH ROW EXECUTE FUNCTION app.enforce_quote_runtime();

CREATE FUNCTION app.enforce_intent_runtime() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE snapshot jsonb;
BEGIN
 PERFORM set_config('app.user_id',NEW.user_id::text,true);
 SELECT input_snapshot INTO snapshot FROM app.generation_tasks WHERE id=NEW.task_id AND user_id=NEW.user_id;
 -- Preserve the existing composite-FK error for invalid legacy rows.
 IF FOUND THEN PERFORM ops.assert_runtime_configuration(snapshot); END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION app.enforce_intent_runtime() OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.enforce_intent_runtime() FROM PUBLIC;
CREATE TRIGGER enforce_intent_runtime BEFORE INSERT ON app.provider_attempts FOR EACH ROW EXECUTE FUNCTION app.enforce_intent_runtime();
