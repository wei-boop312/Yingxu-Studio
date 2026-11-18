-- This explicit operator decision never resubmits generation or changes a ceiling.
-- Accept the original generation receipt first; reconcile the task only afterwards.
CREATE FUNCTION ops.review_blocked_provider_budget(p_id uuid,p_receipt uuid,p_scope_sha text,p_operation uuid,p_reason text,p_evidence text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b ops.provider_budgets; t app.generation_tasks; a app.provider_attempts; h ops.provider_budget_holds; r ops.provider_receipts; prior ops.audit_events; u uuid; target_task uuid;
BEGIN
 IF p_id IS NULL OR p_receipt IS NULL OR p_operation IS NULL OR p_scope_sha IS NULL OR p_scope_sha !~ '^[0-9a-f]{64}$'
 OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000
 OR p_evidence IS NULL OR p_evidence !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_budget_review'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
 SELECT * INTO prior FROM ops.audit_events WHERE request_id=p_operation;
 IF FOUND THEN
  IF prior.action IS DISTINCT FROM 'provider_budget_reviewed' OR prior.target_id IS DISTINCT FROM p_id
  OR prior.reason IS DISTINCT FROM p_reason OR prior.evidence_digest IS DISTINCT FROM p_evidence
  OR prior.after_state->>'receipt_id' IS DISTINCT FROM p_receipt::text
  OR prior.after_state->>'scope_sha256' IS DISTINCT FROM p_scope_sha THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN p_id;
 END IF;
 -- Discover ownership without trusting a caller-supplied user or task, then use tenant RLS.
 SELECT user_id INTO u FROM ops.provider_budgets WHERE id=p_id;
 IF u IS NULL THEN RAISE EXCEPTION 'provider_budget_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT t0.id INTO target_task FROM app.generation_tasks t0 JOIN ops.provider_budgets b0 ON b0.quote_id=t0.quote_id WHERE b0.id=p_id AND t0.user_id=u;
 IF target_task IS NULL THEN RAISE EXCEPTION 'budget_review_requires_uncertain_task'; END IF;
 -- All intent and reconciliation paths take the task lock before the budget lock.
 SELECT * INTO t FROM app.generation_tasks WHERE id=target_task AND user_id=u FOR UPDATE;
 SELECT * INTO b FROM ops.provider_budgets WHERE id=p_id FOR UPDATE;
 IF b.scope_sha256 IS DISTINCT FROM p_scope_sha OR b.input_sha256 IS DISTINCT FROM t.input_sha256
 OR t.input_snapshot->'_execution'->>'provider_profile' IS DISTINCT FROM 'offline_protocol_mock' THEN RAISE EXCEPTION 'provider_budget_scope_mismatch'; END IF;
 IF b.state<>'blocked' OR t.status<>'uncertain' OR t.credits_status<>'frozen' THEN RAISE EXCEPTION 'budget_review_requires_uncertain_task'; END IF;
 IF EXISTS(SELECT 1 FROM ops.audit_events WHERE action='restore_quarantine' AND target_id=t.id)
 THEN RAISE EXCEPTION 'restored_budget_cannot_reopen'; END IF;
 IF clock_timestamp()<b.authorized_at OR clock_timestamp()>=b.expires_at THEN RAISE EXCEPTION 'provider_budget_expired'; END IF;
 SELECT * INTO h FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step='generate';
 IF NOT FOUND OR h.outcome<>'uncertain' OR EXISTS(SELECT 1 FROM ops.provider_budget_holds WHERE authorization_id=b.id AND step<>'generate')
 THEN RAISE EXCEPTION 'budget_review_requires_generation_receipt'; END IF;
 SELECT * INTO a FROM app.provider_attempts WHERE id=h.attempt_id AND task_id=t.id AND user_id=u;
 IF NOT FOUND OR a.step<>'generate' OR a.provider<>'bfl_mock' OR a.model<>'flux-3-image' OR a.status<>'uncertain'
 THEN RAISE EXCEPTION 'budget_review_requires_generation_receipt'; END IF;
 SELECT * INTO r FROM ops.provider_receipts WHERE id=p_receipt AND task_id=t.id AND attempt_id=a.id AND user_id=u;
 IF NOT FOUND THEN RAISE EXCEPTION 'receipt_required'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ops.audit_events WHERE action='accept_receipt' AND target_id=t.id
 AND after_state->>'receipt_id'=r.id::text AND evidence_digest=p_evidence)
 THEN RAISE EXCEPTION 'audited_receipt_required'; END IF;
 IF (a.provider_id IS NOT NULL AND a.provider_id<>r.provider_id)
 OR (a.response_digest IS NOT NULL AND a.response_digest<>r.response_digest) THEN RAISE EXCEPTION 'receipt_conflict'; END IF;
 UPDATE ops.provider_budgets SET state='open' WHERE id=b.id;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,before_state,after_state,request_id)
 VALUES(session_user,'provider_budget_reviewed','provider_budget',b.id,p_reason,p_evidence,
 jsonb_build_object('state','blocked','task_id',t.id,'attempt_id',a.id),
 jsonb_build_object('state','open','task_id',t.id,'receipt_id',r.id,'scope_sha256',b.scope_sha256,'unchanged_ceiling',b.total_ceiling::text),p_operation);
 -- The uncertain hold remains blocked until the existing receipt-reconciliation path
 -- makes that SAME attempt known. This function cannot permit another generate intent.
 RETURN b.id;
END $$;
ALTER FUNCTION ops.review_blocked_provider_budget(uuid,uuid,text,uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.review_blocked_provider_budget(uuid,uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.review_blocked_provider_budget(uuid,uuid,text,uuid,text,text) TO ops_admin;
