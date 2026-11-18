CREATE UNIQUE INDEX audit_operation_once ON ops.audit_events(request_id);
GRANT SELECT(id,status) ON app.users TO app_provisioner;
CREATE POLICY private_account_status ON app.users FOR SELECT TO app_provisioner USING(true);
CREATE FUNCTION app.active_user(p_id uuid) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM app.users WHERE id=p_id AND status='active');
$$;
ALTER FUNCTION app.active_user(uuid) OWNER TO app_provisioner;
REVOKE ALL ON FUNCTION app.active_user(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.active_user(uuid) TO app_executor;
CREATE FUNCTION app.review_check(p_task uuid,p_check uuid,p_operation uuid,p_reviewer uuid,p_decision text,p_reason text,p_evidence jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; previous app.compliance_reviews; identity uuid; automatic text;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 IF NOT app.active_user(p_reviewer) OR p_decision NOT IN ('approved','rejected','needs_review') OR p_reason IS NULL OR length(trim(p_reason))=0 OR length(p_reason)>4000 OR jsonb_typeof(p_evidence) IS DISTINCT FROM 'array' OR jsonb_array_length(p_evidence)>100 THEN RAISE EXCEPTION 'invalid_review'; END IF;
 SELECT auto_overall INTO automatic FROM app.compliance_checks WHERE id=p_check AND task_id=p_task AND user_id=u;
 IF NOT FOUND THEN RAISE EXCEPTION 'check_not_found'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO previous FROM app.compliance_reviews WHERE operation_id=p_operation;
 IF FOUND THEN
  IF previous.check_id<>p_check OR previous.reviewer_id<>p_reviewer OR previous.decision<>p_decision OR previous.reason<>p_reason OR previous.evidence_refs IS DISTINCT FROM p_evidence THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN previous.id;
 END IF;
 INSERT INTO app.compliance_reviews(user_id,check_id,reviewer_id,operation_id,decision,reason,evidence_refs)
 VALUES(u,p_check,p_reviewer,p_operation,p_decision,p_reason,p_evidence) RETURNING id INTO identity;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'manual_review','compliance',p_check,p_reason,NULL,jsonb_build_object('auto_overall',automatic),jsonb_build_object('review_id',identity,'reviewer_id',p_reviewer,'decision',p_decision,'evidence_refs',p_evidence),p_operation);
 RETURN identity;
END $$;
CREATE FUNCTION ops.reconcile_task(p_task uuid,p_operation uuid,p_action text,p_receipt uuid,p_reason text,p_digest text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; w app.wallets; receipt ops.provider_receipts; attempt app.provider_attempts; audit ops.audit_events;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO w FROM app.wallets WHERE user_id=u FOR UPDATE;
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF p_action NOT IN ('resume','fail') OR p_reason IS NULL OR length(trim(p_reason))=0 OR length(p_reason)>4000 OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_reconciliation'; END IF;
 SELECT * INTO audit FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF audit.action IS DISTINCT FROM 'reconcile_'||p_action OR audit.target_id<>p_task OR audit.reason<>p_reason OR audit.evidence_digest<>p_digest OR audit.after_state->>'receipt_id' IS DISTINCT FROM p_receipt::text THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN;
 END IF;
 IF t.status<>'uncertain' THEN RAISE EXCEPTION 'task_not_uncertain'; END IF;
 IF p_action='resume' THEN
  SELECT * INTO receipt FROM ops.provider_receipts WHERE id=p_receipt AND task_id=t.id AND user_id=u;
  IF NOT FOUND THEN RAISE EXCEPTION 'receipt_required'; END IF;
  SELECT * INTO attempt FROM app.provider_attempts WHERE id=receipt.attempt_id AND task_id=t.id FOR UPDATE;
  IF attempt.status IN ('succeeded','failed') THEN RAISE EXCEPTION 'receipt_not_unresolved'; END IF;
  IF attempt.provider_id IS NOT NULL AND attempt.provider_id<>receipt.provider_id THEN RAISE EXCEPTION 'receipt_conflict'; END IF;
  UPDATE app.provider_attempts SET provider_id=receipt.provider_id,polling_url_private=receipt.polling_url_private,response_digest=receipt.response_digest,status='submitted',submitted_at=COALESCE(submitted_at,now()) WHERE id=attempt.id;
  UPDATE app.generation_tasks SET status='processing',phase=CASE WHEN attempt.step='generate' THEN 'polling' ELSE 'detecting' END,lease_owner=NULL,lease_until=now(),fencing_token=fencing_token+1,revision=revision+1,updated_at=now(),error_code=NULL WHERE id=t.id;
  UPDATE ops.task_dispatch SET state='processing',lease_until=now(),next_recovery_at=now(),recovery_attempts=0,fencing_token=t.fencing_token+1 WHERE task_id=t.id;
  INSERT INTO ops.outbox(user_id,task_id,event_type,dedupe_key) VALUES(u,t.id,'task.recovery',t.id::text||':reconcile:'||p_operation::text);
 ELSE
  IF p_receipt IS NOT NULL THEN RAISE EXCEPTION 'fail_does_not_accept_receipt'; END IF;
  UPDATE app.wallets SET available=available+t.reserved_credits,reserved=reserved-t.reserved_credits,revision=revision+1,updated_at=now() WHERE user_id=u RETURNING * INTO w;
  INSERT INTO app.credit_transactions(user_id,task_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
  VALUES(u,t.id,p_operation,'refund',t.reserved_credits,t.reserved_credits,-t.reserved_credits,0,0,w.available,w.reserved,session_user,p_reason);
  UPDATE app.provider_attempts SET status='failed',error_code='manual_delivery_closed',finished_at=now() WHERE task_id=t.id AND status NOT IN ('succeeded','failed');
  UPDATE app.generation_tasks SET status='failed',credits_status='refunded',completed_at=now(),lease_owner=NULL,lease_until=NULL,fencing_token=fencing_token+1,revision=revision+1,updated_at=now(),error_code='manual_delivery_closed' WHERE id=t.id;
  UPDATE ops.task_dispatch SET state='failed',lease_until=NULL WHERE task_id=t.id;
 END IF;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'reconcile_'||p_action,'task',t.id,p_reason,p_digest,jsonb_build_object('status',t.status,'credits_status',t.credits_status),jsonb_build_object('status',CASE WHEN p_action='resume' THEN 'processing' ELSE 'failed' END,'receipt_id',p_receipt),p_operation);
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status',CASE WHEN p_action='resume' THEN 'processing' ELSE 'failed' END,'revision',t.revision+1));
END $$;
ALTER FUNCTION app.review_check(uuid,uuid,uuid,uuid,text,text,jsonb) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.review_check(uuid,uuid,uuid,uuid,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.review_check(uuid,uuid,uuid,uuid,text,text,jsonb) TO ops_admin;
ALTER FUNCTION ops.reconcile_task(uuid,uuid,text,uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.reconcile_task(uuid,uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.reconcile_task(uuid,uuid,text,uuid,text,text) TO ops_admin;
