CREATE TABLE ops.upload_temp_cleanup (
 intent_id uuid PRIMARY KEY, user_id uuid NOT NULL, operation_id uuid NOT NULL UNIQUE,
 state text NOT NULL CHECK(state IN ('pending','done')), lease_until timestamptz,
 attempts integer NOT NULL CHECK(attempts>0), removed_versions integer CHECK(removed_versions>=0),
 actor text NOT NULL, reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 4000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 FOREIGN KEY(user_id,intent_id) REFERENCES app.upload_intents(user_id,id),
 CHECK((state='pending' AND lease_until IS NOT NULL AND finished_at IS NULL AND removed_versions IS NULL)
 OR (state='done' AND finished_at IS NOT NULL AND lease_until IS NULL AND removed_versions IS NOT NULL))
);
CREATE INDEX upload_cleanup_candidate ON app.upload_intents(user_id,expires_at,id);
ALTER TABLE ops.upload_temp_cleanup ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.upload_temp_cleanup FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.upload_temp_cleanup FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON ops.upload_temp_cleanup TO app_executor;
GRANT SELECT ON ops.upload_temp_cleanup TO ops_admin;
CREATE POLICY cleanup_tenant ON ops.upload_temp_cleanup TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY cleanup_read ON ops.upload_temp_cleanup FOR SELECT TO ops_admin USING(true);

CREATE FUNCTION ops.expired_uploads(p_user uuid,p_limit integer DEFAULT 100) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF p_user IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_cleanup_scope'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT COALESCE(jsonb_agg(row),'[]'::jsonb) INTO result FROM (
  SELECT i.id AS intent_id, i.status, i.expires_at,
   encode(sha256(convert_to(i.temp_key,'UTF8')),'hex') AS temp_key_sha256
  FROM app.upload_intents i LEFT JOIN ops.upload_temp_cleanup g ON g.intent_id=i.id
  WHERE i.user_id=p_user AND i.expires_at<clock_timestamp()-interval '24 hours'
   AND (i.lease_until IS NULL OR i.lease_until<=clock_timestamp())
   AND (g.intent_id IS NULL OR (g.state='pending' AND g.lease_until<=clock_timestamp()))
  ORDER BY i.expires_at,i.id LIMIT p_limit
 ) row;
 RETURN result;
END $$;

CREATE FUNCTION ops.claim_upload_cleanup(p_user uuid,p_intent uuid,p_operation uuid,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i app.upload_intents; prior ops.upload_temp_cleanup; result ops.upload_temp_cleanup;
BEGIN
 IF p_user IS NULL OR p_intent IS NULL OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'invalid_cleanup_scope'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO i FROM app.upload_intents WHERE id=p_intent AND user_id=p_user FOR UPDATE;
 IF NOT FOUND OR i.temp_key IS DISTINCT FROM 'temp/'||p_user::text||'/'||p_intent::text THEN RAISE EXCEPTION 'cleanup_upload_not_found'; END IF;
 SELECT * INTO prior FROM ops.upload_temp_cleanup WHERE intent_id=i.id;
 IF FOUND AND prior.state='done' THEN
  IF prior.operation_id IS DISTINCT FROM p_operation OR prior.reason IS DISTINCT FROM p_reason THEN RAISE EXCEPTION 'cleanup_operation_conflict'; END IF;
  RETURN jsonb_build_object('state','done','intent_id',i.id,'removed_versions',prior.removed_versions);
 END IF;
 IF i.expires_at>=clock_timestamp()-interval '24 hours' OR i.lease_until>clock_timestamp()
 OR (prior.intent_id IS NOT NULL AND prior.lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'cleanup_not_eligible'; END IF;
 IF prior.intent_id IS NOT NULL AND (prior.operation_id IS DISTINCT FROM p_operation OR prior.reason IS DISTINCT FROM p_reason) THEN RAISE EXCEPTION 'cleanup_operation_conflict'; END IF;
 IF EXISTS(SELECT 1 FROM app.asset_versions WHERE user_id=p_user AND object_key=i.temp_key) THEN RAISE EXCEPTION 'cleanup_referenced_object'; END IF;
 IF i.status<>'ready' THEN
  UPDATE app.upload_intents SET status='rejected',lease_owner=NULL,lease_until=NULL,error_code='expired_temp_cleanup' WHERE id=i.id;
  UPDATE app.assets SET status='rejected' WHERE id=i.asset_id;
 END IF;
 INSERT INTO ops.upload_temp_cleanup(intent_id,user_id,operation_id,state,lease_until,attempts,actor,reason)
 VALUES(i.id,p_user,p_operation,'pending',clock_timestamp()+interval '5 minutes',1,session_user,p_reason)
 ON CONFLICT(intent_id) DO UPDATE SET lease_until=clock_timestamp()+interval '5 minutes',attempts=ops.upload_temp_cleanup.attempts+1
 RETURNING * INTO result;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,after_state,request_id)
 VALUES(session_user,'upload_temp_cleanup_claimed','upload',i.id,p_reason,jsonb_build_object('attempt',result.attempts,'temp_key_sha256',encode(sha256(convert_to(i.temp_key,'UTF8')),'hex')),p_operation);
 RETURN jsonb_build_object('state','pending','intent_id',i.id,'temp_key',i.temp_key,'attempt',result.attempts,'operation_id',p_operation);
END $$;

CREATE FUNCTION ops.finish_upload_cleanup(p_user uuid,p_intent uuid,p_operation uuid,p_attempt integer,p_removed integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE saved ops.upload_temp_cleanup;
BEGIN
 PERFORM set_config('app.user_id',p_user::text,true);
 SELECT * INTO saved FROM ops.upload_temp_cleanup WHERE intent_id=p_intent AND user_id=p_user FOR UPDATE;
 IF NOT FOUND OR saved.operation_id IS DISTINCT FROM p_operation OR p_attempt IS NULL OR saved.attempts IS DISTINCT FROM p_attempt
 OR p_removed IS NULL OR p_removed NOT BETWEEN 0 AND 1000 THEN RAISE EXCEPTION 'cleanup_receipt_mismatch'; END IF;
 IF saved.state='done' THEN
  IF saved.removed_versions IS DISTINCT FROM p_removed THEN RAISE EXCEPTION 'cleanup_receipt_mismatch'; END IF;
  RETURN;
 END IF;
 IF saved.lease_until IS NULL OR saved.lease_until<=clock_timestamp() THEN RAISE EXCEPTION 'cleanup_lease_lost'; END IF;
 UPDATE ops.upload_temp_cleanup SET state='done',lease_until=NULL,removed_versions=p_removed,finished_at=clock_timestamp() WHERE intent_id=p_intent;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,after_state,request_id)
 VALUES(session_user,'upload_temp_cleanup_finished','upload',p_intent,saved.reason,jsonb_build_object('removed_versions',p_removed,'attempt',p_attempt),p_operation);
END $$;

ALTER FUNCTION ops.expired_uploads(uuid,integer) OWNER TO app_executor;
ALTER FUNCTION ops.claim_upload_cleanup(uuid,uuid,uuid,text) OWNER TO app_executor;
ALTER FUNCTION ops.finish_upload_cleanup(uuid,uuid,uuid,integer,integer) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.expired_uploads(uuid,integer),ops.claim_upload_cleanup(uuid,uuid,uuid,text),ops.finish_upload_cleanup(uuid,uuid,uuid,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.expired_uploads(uuid,integer),ops.claim_upload_cleanup(uuid,uuid,uuid,text),ops.finish_upload_cleanup(uuid,uuid,uuid,integer,integer) TO ops_admin;
