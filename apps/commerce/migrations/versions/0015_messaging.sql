ALTER TABLE ops.outbox ADD COLUMN publisher_token uuid;
ALTER TABLE ops.task_dispatch ADD COLUMN recovery_attempts integer NOT NULL DEFAULT 0 CHECK(recovery_attempts>=0);
ALTER TABLE ops.task_dispatch ADD COLUMN next_recovery_at timestamptz NOT NULL DEFAULT now()+interval '30 seconds';
CREATE FUNCTION ops.validate_delivery(p_outbox uuid,p_task uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 RETURN EXISTS(SELECT 1 FROM ops.outbox o JOIN ops.task_dispatch d ON d.task_id=o.task_id AND d.user_id=o.user_id WHERE o.id=p_outbox AND o.task_id=p_task AND d.state<>'manual');
END $$;
CREATE FUNCTION ops.enqueue_recoveries(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d ops.task_dispatch; n integer; count integer:=0;
BEGIN
 IF p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_limit'; END IF;
 FOR d IN SELECT * FROM ops.task_dispatch td WHERE td.state IN ('queued','processing') AND td.next_recovery_at<=now()
 AND (td.state='queued' OR td.lease_until<=now())
 AND EXISTS(SELECT 1 FROM ops.outbox o WHERE o.task_id=td.task_id AND o.event_type='task.queued' AND o.published_at IS NOT NULL)
 ORDER BY td.next_recovery_at FOR UPDATE SKIP LOCKED LIMIT p_limit LOOP
  PERFORM set_config('app.user_id',d.user_id::text,true);
  n:=d.recovery_attempts+1;
  INSERT INTO ops.outbox(user_id,task_id,event_type,dedupe_key)
  VALUES(d.user_id,d.task_id,CASE WHEN n>5 THEN 'task.manual' ELSE 'task.recovery' END,d.task_id::text||':recovery:'||n::text)
  ON CONFLICT(dedupe_key) DO NOTHING;
  UPDATE ops.task_dispatch SET recovery_attempts=n,next_recovery_at=now()+interval '30 seconds',state=CASE WHEN n>5 THEN 'manual' ELSE state END WHERE task_id=d.task_id;
  count:=count+1;
 END LOOP;
 RETURN count;
END $$;
ALTER FUNCTION ops.validate_delivery(uuid,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.validate_delivery(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.validate_delivery(uuid,uuid) TO app_worker;
ALTER FUNCTION ops.enqueue_recoveries(integer) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.enqueue_recoveries(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.enqueue_recoveries(integer) TO ops_dispatcher;
CREATE OR REPLACE FUNCTION app.cancel_task(p_task uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
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
 UPDATE ops.task_dispatch SET state='cancelled',lease_until=NULL WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','cancelled','revision',t.revision+1));
END $$;
CREATE OR REPLACE FUNCTION ops.claim_task(p_task uuid,p_owner text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_owner'; END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task AND state<>'manual';
 IF u IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF t.status IN ('succeeded','failed','cancelled','uncertain') OR (t.lease_until IS NOT NULL AND t.lease_until>clock_timestamp()) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND provider_id IS NULL AND status IN ('intent_recorded','uncertain')) THEN
  UPDATE app.generation_tasks SET status='uncertain',lease_owner=NULL,lease_until=NULL,revision=revision+1,updated_at=now(),error_code='submission_outcome_unknown' WHERE id=t.id;
  UPDATE ops.task_dispatch SET state='uncertain',lease_until=NULL WHERE task_id=t.id;
  INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status','uncertain','revision',t.revision+1));
  RETURN jsonb_build_object('id',t.id,'status','uncertain');
 END IF;
 UPDATE app.generation_tasks SET status='processing',phase=CASE WHEN status='queued' THEN 'submitting' ELSE phase END,
  lease_owner=p_owner,lease_until=clock_timestamp()+interval '60 seconds',fencing_token=fencing_token+1,revision=revision+1,updated_at=now()
 WHERE id=t.id RETURNING * INTO t;
 UPDATE ops.task_dispatch SET state='processing',lease_until=t.lease_until,fencing_token=t.fencing_token WHERE task_id=t.id;
 INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(u,t.id,'task.updated',jsonb_build_object('status',t.status,'phase',t.phase,'revision',t.revision));
 RETURN jsonb_build_object('id',t.id,'status',t.status,'phase',t.phase,'fencing_token',t.fencing_token);
END $$;
