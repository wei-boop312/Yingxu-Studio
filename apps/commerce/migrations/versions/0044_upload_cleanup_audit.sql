-- The original operation stays unique in the cleanup receipt, not across audit events.
CREATE OR REPLACE FUNCTION ops.claim_upload_cleanup(p_user uuid,p_intent uuid,p_operation uuid,p_reason text) RETURNS jsonb
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
 VALUES(session_user,'upload_temp_cleanup_claimed','upload',i.id,p_reason,jsonb_build_object('operation_id',p_operation,'attempt',result.attempts,'temp_key_sha256',encode(sha256(convert_to(i.temp_key,'UTF8')),'hex')),gen_random_uuid());
 RETURN jsonb_build_object('state','pending','intent_id',i.id,'temp_key',i.temp_key,'attempt',result.attempts,'operation_id',p_operation);
END $$;

CREATE OR REPLACE FUNCTION ops.finish_upload_cleanup(p_user uuid,p_intent uuid,p_operation uuid,p_attempt integer,p_removed integer) RETURNS void
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
 VALUES(session_user,'upload_temp_cleanup_finished','upload',p_intent,saved.reason,jsonb_build_object('operation_id',p_operation,'removed_versions',p_removed,'attempt',p_attempt),gen_random_uuid());
END $$;
