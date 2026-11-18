CREATE OR REPLACE FUNCTION app.assert_lease(p_task uuid,p_owner text,p_fence bigint) RETURNS app.generation_tasks
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status<>'processing' OR t.lease_owner IS DISTINCT FROM p_owner OR t.fencing_token IS DISTINCT FROM p_fence OR t.lease_until IS NULL OR t.lease_until<=clock_timestamp()
 THEN RAISE EXCEPTION 'stale_lease' USING ERRCODE='55000'; END IF;
 RETURN t;
END $$;
CREATE FUNCTION app.record_attempt_failure(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid,p_code text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 UPDATE app.provider_attempts SET status='failed',error_code=p_code,finished_at=now() WHERE id=p_attempt AND task_id=t.id AND status<>'succeeded';
 IF NOT FOUND THEN RAISE EXCEPTION 'attempt_not_found'; END IF;
END $$;
ALTER FUNCTION app.record_attempt_failure(uuid,text,bigint,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_attempt_failure(uuid,text,bigint,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_attempt_failure(uuid,text,bigint,uuid,text) TO app_worker;
CREATE OR REPLACE FUNCTION app.fail_task(p_task uuid,p_owner text,p_fence bigint,p_code text,p_uncertain boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks; w app.wallets;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO w FROM app.wallets WHERE user_id=u FOR UPDATE;
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status='failed' AND NOT p_uncertain THEN RETURN; END IF;
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_uncertain THEN
  UPDATE app.generation_tasks SET status='uncertain',lease_owner=NULL,lease_until=NULL,error_code=p_code,revision=revision+1,updated_at=now() WHERE id=t.id;
  UPDATE app.provider_attempts SET status='uncertain' WHERE task_id=t.id AND status NOT IN ('succeeded','failed');
 ELSE
  IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND status IN ('intent_recorded','submitted','polling','uncertain')) THEN RAISE EXCEPTION 'unresolved_attempt_requires_reconciliation'; END IF;
  UPDATE app.wallets SET available=available+t.reserved_credits,reserved=reserved-t.reserved_credits,revision=revision+1,updated_at=now() WHERE user_id=u RETURNING * INTO w;
  INSERT INTO app.credit_transactions(user_id,task_id,operation_id,type,amount,available_delta,reserved_delta,funded_delta,spent_delta,available_after,reserved_after,actor_ref,reason)
  VALUES(u,t.id,gen_random_uuid(),'refund',t.reserved_credits,t.reserved_credits,-t.reserved_credits,0,0,w.available,w.reserved,'system',p_code);
  UPDATE app.generation_tasks SET status='failed',credits_status='refunded',completed_at=now(),lease_owner=NULL,lease_until=NULL,error_code=p_code,revision=revision+1,updated_at=now() WHERE id=t.id;
 END IF;
 UPDATE ops.task_dispatch SET state=CASE WHEN p_uncertain THEN 'uncertain' ELSE 'failed' END,lease_until=NULL WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status',CASE WHEN p_uncertain THEN 'uncertain' ELSE 'failed' END,'revision',t.revision+1));
END $$;
