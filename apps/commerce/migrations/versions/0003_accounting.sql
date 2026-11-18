CREATE ROLE app_executor NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA app,ops TO app_executor;
GRANT SELECT ON app.users,app.products,app.appearance_profiles,app.assets,app.asset_versions TO app_executor;
GRANT SELECT,INSERT,UPDATE ON app.wallets,app.quotes,app.generation_tasks TO app_executor;
GRANT SELECT,INSERT ON app.credit_transactions,app.task_events,ops.outbox,ops.task_dispatch,ops.audit_events TO app_executor;
GRANT USAGE ON SEQUENCE app.task_events_id_seq TO app_executor;
CREATE POLICY executor_outbox ON ops.outbox TO app_executor USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY executor_dispatch ON ops.task_dispatch TO app_executor USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY executor_audit ON ops.audit_events TO app_executor WITH CHECK(true);
CREATE FUNCTION app.grant_credits(p_user uuid,p_operation uuid,p_amount bigint,p_reason text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w app.wallets; prior app.credit_transactions;
BEGIN
 IF p_amount<=0 OR p_reason IS NULL OR length(p_reason)=0 THEN RAISE EXCEPTION 'invalid_grant'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 INSERT INTO app.wallets(user_id) VALUES(p_user) ON CONFLICT DO NOTHING;
 SELECT * INTO w FROM app.wallets WHERE user_id=p_user FOR UPDATE;
 SELECT * INTO prior FROM app.credit_transactions WHERE operation_id=p_operation;
 IF FOUND THEN
  IF prior.user_id<>p_user OR prior.type<>'grant' OR prior.amount<>p_amount THEN RAISE EXCEPTION 'operation_conflict'; END IF;
  RETURN;
 END IF;
 UPDATE app.wallets SET available=available+p_amount,funded_total=funded_total+p_amount,revision=revision+1,updated_at=now() WHERE user_id=p_user RETURNING * INTO w;
 INSERT INTO app.credit_transactions(user_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
 VALUES(p_user,p_operation,'grant',p_amount,p_amount,0,p_amount,0,w.available,w.reserved,session_user,p_reason);
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,request_id,after_state)
 VALUES(session_user,'grant','user',p_user,p_reason,p_operation,jsonb_build_object('amount',p_amount));
END $$;
ALTER FUNCTION app.grant_credits(uuid,uuid,bigint,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.grant_credits(uuid,uuid,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.grant_credits(uuid,uuid,bigint,text) TO ops_admin;
GRANT USAGE ON SCHEMA app TO ops_admin;
CREATE FUNCTION app.submit_task(p_id uuid,p_quote uuid,p_key text,p_hash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; q app.quotes; t app.generation_tasks; w app.wallets; product uuid; source uuid; appearance uuid;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 IF u IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.users WHERE id=u AND status='active') THEN RAISE EXCEPTION 'inactive_user'; END IF;
 IF p_hash !~ '^[0-9a-f]{64}$' OR length(p_key) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_request'; END IF;
 SELECT * INTO w FROM app.wallets WHERE user_id=u FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'insufficient_credits'; END IF;
 SELECT * INTO t FROM app.generation_tasks WHERE user_id=u AND operation='generate' AND idempotency_key=p_key;
 IF FOUND THEN
  IF t.request_hash<>p_hash OR t.quote_id<>p_quote THEN RAISE EXCEPTION 'idempotency_conflict'; END IF;
  RETURN t.id;
 END IF;
 SELECT * INTO q FROM app.quotes WHERE user_id=u AND id=p_quote FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'quote_not_found'; END IF;
 IF q.consumed_task_id IS NOT NULL THEN RAISE EXCEPTION 'quote_consumed'; END IF;
 IF q.expires_at<=now() THEN RAISE EXCEPTION 'quote_expired'; END IF;
 IF w.available<q.credits THEN RAISE EXCEPTION 'insufficient_credits'; END IF;
 product:=(q.input_snapshot->>'product_id')::uuid;
 source:=(q.input_snapshot->>'source_asset_version_id')::uuid;
 appearance:=(q.input_snapshot->>'appearance_profile_id')::uuid;
 PERFORM 1 FROM app.products WHERE user_id=u AND id=product AND archived_at IS NULL FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'product_unavailable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM app.asset_versions v JOIN app.assets a ON a.id=v.asset_id AND a.user_id=v.user_id
   WHERE v.id=source AND v.user_id=u AND v.product_id=product AND v.canonical AND a.status='ready') THEN RAISE EXCEPTION 'source_unavailable'; END IF;
 IF appearance IS NOT NULL AND NOT EXISTS(SELECT 1 FROM app.appearance_profiles WHERE id=appearance AND user_id=u AND product_id=product AND archived_at IS NULL) THEN RAISE EXCEPTION 'appearance_unavailable'; END IF;
 INSERT INTO app.generation_tasks(id,user_id,product_id,appearance_profile_id,source_asset_version_id,idempotency_key,request_hash,quote_id,reserved_credits,input_snapshot,input_sha256)
 VALUES(p_id,u,product,appearance,source,p_key,p_hash,q.id,q.credits,q.input_snapshot,q.input_sha256);
 UPDATE app.wallets SET available=available-q.credits,reserved=reserved+q.credits,revision=revision+1,updated_at=now() WHERE user_id=u RETURNING * INTO w;
 INSERT INTO app.credit_transactions(user_id,task_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
 VALUES(u,p_id,gen_random_uuid(),'freeze',q.credits,-q.credits,q.credits,0,0,w.available,w.reserved,'system','task_created');
 UPDATE app.quotes SET consumed_task_id=p_id WHERE id=q.id;
 INSERT INTO ops.outbox(user_id,task_id,event_type,dedupe_key) VALUES(u,p_id,'task.queued',p_id::text||':queued');
 INSERT INTO ops.task_dispatch(user_id,task_id) VALUES(u,p_id);
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,p_id,'task.updated',jsonb_build_object('status','queued','revision',1));
 RETURN p_id;
END $$;
ALTER FUNCTION app.submit_task(uuid,uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.submit_task(uuid,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.submit_task(uuid,uuid,text,text) TO app_api;

GRANT SELECT ON app.provider_attempts TO app_executor;
CREATE FUNCTION app.cancel_task(p_task uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; w app.wallets; t app.generation_tasks;
BEGIN
 u:=NULLIF(current_setting('app.user_id',true),'')::uuid;
 IF u IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
 SELECT * INTO w FROM app.wallets WHERE user_id=u FOR UPDATE;
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'task_not_found'; END IF;
 IF t.status='cancelled' THEN RETURN; END IF;
 IF t.status<>'queued' OR EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step='generate') THEN RAISE EXCEPTION 'already_started'; END IF;
 UPDATE app.wallets SET available=available+t.reserved_credits,reserved=reserved-t.reserved_credits,revision=revision+1,updated_at=now() WHERE user_id=u RETURNING * INTO w;
 INSERT INTO app.credit_transactions(user_id,task_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
 VALUES(u,t.id,gen_random_uuid(),'refund',t.reserved_credits,t.reserved_credits,-t.reserved_credits,0,0,w.available,w.reserved,'system','cancelled');
 UPDATE app.generation_tasks SET status='cancelled',credits_status='refunded',completed_at=now(),updated_at=now(),revision=revision+1 WHERE id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','cancelled','revision',t.revision+1));
END $$;
ALTER FUNCTION app.cancel_task(uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.cancel_task(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.cancel_task(uuid) TO app_api;
