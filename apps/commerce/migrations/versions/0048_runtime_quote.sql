CREATE FUNCTION ops.runtime_quote_source(p_user uuid,p_source uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q app.quotes;
BEGIN
 IF p_user IS NULL THEN RAISE EXCEPTION 'quote_not_found'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO q FROM app.quotes WHERE id=p_source AND user_id=p_user;
 IF NOT FOUND OR COALESCE(q.input_snapshot->'_execution'->>'provider_profile','offline')<>'offline' THEN RAISE EXCEPTION 'quote_not_found'; END IF;
 RETURN jsonb_build_object('quote_id',q.id,'input_snapshot',q.input_snapshot);
END $$;
ALTER FUNCTION ops.runtime_quote_source(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.runtime_quote_source(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.runtime_quote_source(uuid,uuid) TO ops_admin;

CREATE FUNCTION ops.prepare_runtime_quote(p_user uuid,p_source uuid,p_id uuid,p_text text,p_sha text,p_operation uuid,p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q app.quotes; prior ops.audit_events; d jsonb; execution jsonb; entry jsonb; account jsonb;
 mutable text[]:=ARRAY['provider_profile','provider_model','price_version','provider_runtime_sha256','provider_models','provider_accounts','provider_endpoints'];
BEGIN
 IF p_user IS NULL OR p_source IS NULL OR p_id IS NULL OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_text IS NULL OR octet_length(p_text)>1048576 OR p_sha IS NULL OR p_sha !~ '^[0-9a-f]{64}$'
 OR encode(sha256(convert_to(p_text,'UTF8')),'hex') IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 d:=p_text::jsonb; execution:=d->'_execution';
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR jsonb_typeof(execution) IS DISTINCT FROM 'object'
 OR NOT(execution ?& mutable) OR execution->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock'
 OR execution->>'provider_model' IS DISTINCT FROM 'flux-3-image-protocol-mock'
 OR execution->>'price_version' IS DISTINCT FROM 'trial-protocol-mock-v1'
 OR jsonb_typeof(execution->'provider_runtime_sha256') IS DISTINCT FROM 'string'
 OR execution->>'provider_runtime_sha256' !~ '^[0-9a-f]{64}$'
 OR execution->'provider_models' IS DISTINCT FROM '{"generate":"flux-3-image","detect":"gemini-3.8-flash"}'::jsonb
 OR jsonb_typeof(execution->'provider_endpoints') IS DISTINCT FROM 'object'
 OR NOT(execution->'provider_endpoints' ?& ARRAY['generate','detect'])
 OR (SELECT count(*) FROM jsonb_object_keys(execution->'provider_endpoints'))<>2
 OR execution->'provider_endpoints'->>'generate' IS DISTINCT FROM 'https://api.bfl.ai/v1/flux-3-image'
 OR jsonb_typeof(execution->'provider_endpoints'->'detect') IS DISTINCT FROM 'string'
 OR execution->'provider_endpoints'->>'detect' NOT IN ('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent','https://hubway.cc/v1beta/models/gemini-3.8-flash:generateContent','https://z.hubway.cc/v1beta/models/gemini-3.8-flash:generateContent')
 OR jsonb_typeof(execution->'provider_accounts') IS DISTINCT FROM 'object'
 OR NOT(execution->'provider_accounts' ?& ARRAY['generate','detect'])
 OR (SELECT count(*) FROM jsonb_object_keys(execution->'provider_accounts'))<>2 THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 FOR entry IN SELECT value FROM jsonb_each(execution->'provider_accounts') LOOP
  account:=entry;
  IF jsonb_typeof(account) IS DISTINCT FROM 'object' OR NOT(account ?& ARRAY['merchant_fingerprint','billing_group'])
  OR (SELECT count(*) FROM jsonb_object_keys(account))<>2
  OR jsonb_typeof(account->'merchant_fingerprint') IS DISTINCT FROM 'string' OR account->>'merchant_fingerprint' !~ '^[0-9a-f]{64}$'
  OR jsonb_typeof(account->'billing_group') IS DISTINCT FROM 'string' OR length(account->>'billing_group') NOT BETWEEN 1 AND 160
  OR trim(account->>'billing_group')='' OR account->>'billing_group' ~ '[[:cntrl:]]' THEN RAISE EXCEPTION 'invalid_runtime_quote'; END IF;
 END LOOP;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action<>'runtime_quote_prepare' OR prior.target_id IS DISTINCT FROM p_id OR prior.reason IS DISTINCT FROM p_reason
  OR prior.evidence_digest IS DISTINCT FROM execution->>'provider_runtime_sha256'
  OR prior.before_state->>'source_quote_id' IS DISTINCT FROM p_source::text
  OR prior.after_state->>'user_id' IS DISTINCT FROM p_user::text
  OR prior.after_state->>'input_sha256' IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_id;
 END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO q FROM app.quotes WHERE id=p_source AND user_id=p_user FOR SHARE;
 IF NOT FOUND OR COALESCE(q.input_snapshot->'_execution'->>'provider_profile','offline')<>'offline'
 OR q.expires_at<=clock_timestamp() OR q.consumed_task_id IS NOT NULL THEN RAISE EXCEPTION 'runtime_quote_source_unavailable'; END IF;
 IF (d-'_execution') IS DISTINCT FROM (q.input_snapshot-'_execution')
 OR (execution-mutable) IS DISTINCT FROM ((q.input_snapshot->'_execution')-mutable)
 THEN RAISE EXCEPTION 'runtime_quote_business_mismatch'; END IF;
 PERFORM app.create_quote(p_id,q.input_hash,p_sha,d,q.rules_snapshot);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'runtime_quote_prepare','quote',p_id,p_reason,execution->>'provider_runtime_sha256',jsonb_build_object('source_quote_id',p_source),
 jsonb_build_object('user_id',p_user,'input_sha256',p_sha,'profile','offline_protocol_mock','paid_calls',0),p_operation);
 RETURN p_id;
END $$;
ALTER FUNCTION ops.prepare_runtime_quote(uuid,uuid,uuid,text,text,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.prepare_runtime_quote(uuid,uuid,uuid,text,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.prepare_runtime_quote(uuid,uuid,uuid,text,text,uuid,text) TO ops_admin;
