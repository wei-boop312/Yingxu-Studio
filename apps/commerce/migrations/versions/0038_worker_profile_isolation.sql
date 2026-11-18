-- Profile mismatch must not acquire a lease, quarantine, refund or consume an intent.
CREATE FUNCTION ops.claim_task(p_task uuid,p_owner text,p_profile text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks;
BEGIN
 IF p_profile IS NULL OR p_profile NOT IN ('offline','offline_protocol_mock') THEN RAISE EXCEPTION 'invalid_worker_profile'; END IF;
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_owner'; END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task AND state<>'manual';
 IF u IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 SELECT * INTO t FROM app.generation_tasks WHERE id=p_task AND user_id=u FOR UPDATE;
 IF COALESCE(t.input_snapshot->'_execution'->>'provider_profile','offline') IS DISTINCT FROM p_profile THEN RETURN NULL; END IF;
 IF t.status IN ('succeeded','failed','cancelled','uncertain') OR (t.lease_until IS NOT NULL AND t.lease_until>clock_timestamp()) THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts a WHERE a.task_id=t.id AND a.provider_id IS NULL AND a.status IN ('intent_recorded','uncertain')
 AND NOT(a.step='detect' AND t.phase IN ('validating_rules','finalizing')
 AND EXISTS(SELECT 1 FROM ops.detection_manual_deliveries d JOIN app.asset_versions v ON v.id=d.final_version_id AND v.user_id=d.user_id
 WHERE d.task_id=t.id AND d.user_id=t.user_id AND d.attempt_id=a.id AND d.final_version_id=t.final_version_id AND d.final_sha256=v.sha256))) THEN
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

ALTER FUNCTION ops.claim_task(uuid,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.claim_task(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.claim_task(uuid,text,text) TO app_worker;

-- Legacy two-argument Worker calls now mean the default offline Fake profile.
CREATE OR REPLACE FUNCTION ops.claim_task(p_task uuid,p_owner text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT ops.claim_task(p_task,p_owner,'offline')
$$;

-- Protocol rehearsal is an explicit private Worker invocation, never normal queue work.
CREATE FUNCTION ops.route_provider_outbox() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE profile text;
BEGIN
 IF NEW.event_type IN ('task.queued','task.recovery') THEN
  PERFORM set_config('app.user_id',NEW.user_id::text,true);
  SELECT t.input_snapshot->'_execution'->>'provider_profile' INTO profile FROM app.generation_tasks t WHERE t.id=NEW.task_id AND t.user_id=NEW.user_id;
  IF profile='offline_protocol_mock' THEN NEW.event_type:='task.manual'; END IF;
 END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION ops.route_provider_outbox() OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.route_provider_outbox() FROM PUBLIC;
CREATE TRIGGER route_provider_outbox BEFORE INSERT ON ops.outbox FOR EACH ROW EXECUTE FUNCTION ops.route_provider_outbox();

-- Reject historical/cross-queue protocol messages as well as manual dispatches.
CREATE OR REPLACE FUNCTION ops.validate_delivery(p_outbox uuid,p_task uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid;
BEGIN
 SELECT user_id INTO u FROM ops.outbox WHERE id=p_outbox AND task_id=p_task;
 IF u IS NULL THEN RETURN false; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 RETURN EXISTS(SELECT 1 FROM ops.outbox o JOIN ops.task_dispatch d ON d.task_id=o.task_id AND d.user_id=o.user_id
 JOIN app.generation_tasks t ON t.id=d.task_id AND t.user_id=d.user_id WHERE o.id=p_outbox AND o.task_id=p_task AND d.state<>'manual'
 AND o.event_type<>'task.manual' AND COALESCE(t.input_snapshot->'_execution'->>'provider_profile','offline')='offline');
END $$;
