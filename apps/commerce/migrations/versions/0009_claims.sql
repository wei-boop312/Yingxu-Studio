CREATE FUNCTION app.assert_lease(p_task uuid,p_owner text,p_fence bigint) RETURNS app.generation_tasks
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
CREATE FUNCTION ops.claim_task(p_task uuid,p_owner text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; t app.generation_tasks;
BEGIN
 IF p_owner IS NULL OR length(p_owner) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'invalid_owner'; END IF;
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
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
CREATE FUNCTION app.renew_lease(p_task uuid,p_owner text,p_fence bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 UPDATE app.generation_tasks SET lease_until=clock_timestamp()+interval '60 seconds' WHERE id=t.id;
 UPDATE ops.task_dispatch SET lease_until=clock_timestamp()+interval '60 seconds' WHERE task_id=t.id;
END $$;
CREATE FUNCTION app.worker_context(p_task uuid,p_owner text,p_fence bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; source app.asset_versions;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO source FROM app.asset_versions WHERE id=t.source_asset_version_id AND user_id=t.user_id;
 RETURN to_jsonb(t)||jsonb_build_object('source',to_jsonb(source),'attempts',COALESCE((SELECT jsonb_agg(to_jsonb(a)) FROM app.provider_attempts a WHERE task_id=t.id),'[]'::jsonb));
END $$;
CREATE FUNCTION app.record_intent(p_task uuid,p_owner text,p_fence bigint,p_step text,p_request_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a uuid;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 IF p_step NOT IN ('generate','detect') OR p_request_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_intent'; END IF;
 IF (p_step='generate' AND t.phase<>'submitting') OR (p_step='detect' AND (t.phase<>'detecting' OR t.final_version_id IS NULL)) THEN RAISE EXCEPTION 'invalid_phase'; END IF;
 IF EXISTS(SELECT 1 FROM app.provider_attempts WHERE task_id=t.id AND step=p_step) THEN RAISE EXCEPTION 'intent_exists' USING ERRCODE='55000'; END IF;
 INSERT INTO app.provider_attempts(user_id,task_id,step,provider,model,status,request_hash,local_request_id)
 VALUES(t.user_id,t.id,p_step,'fake',CASE WHEN p_step='generate' THEN t.input_snapshot->'_execution'->>'provider_model' ELSE 'fake-facts-v1' END,'intent_recorded',p_request_hash,gen_random_uuid()) RETURNING id INTO a;
 RETURN a;
END $$;
CREATE FUNCTION app.append_receipt(p_task uuid,p_attempt uuid,p_provider_id text,p_digest text,p_polling_url text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid; receipt uuid;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RAISE EXCEPTION 'task_not_found'; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 IF p_provider_id IS NULL OR length(p_provider_id)=0 OR p_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid_receipt'; END IF;
 INSERT INTO ops.provider_receipts(user_id,task_id,attempt_id,provider_id,response_digest,polling_url_private)
 VALUES(u,p_task,p_attempt,p_provider_id,p_digest,p_polling_url)
 ON CONFLICT(attempt_id,provider_id,response_digest) DO NOTHING RETURNING id INTO receipt;
 IF receipt IS NULL THEN SELECT id INTO receipt FROM ops.provider_receipts WHERE attempt_id=p_attempt AND provider_id=p_provider_id AND response_digest=p_digest; END IF;
 RETURN receipt;
END $$;
CREATE FUNCTION app.apply_receipt(p_task uuid,p_owner text,p_fence bigint,p_receipt uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; r ops.provider_receipts; a app.provider_attempts;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO r FROM ops.provider_receipts WHERE id=p_receipt AND user_id=t.user_id AND task_id=t.id;
 IF NOT FOUND THEN RAISE EXCEPTION 'receipt_not_found'; END IF;
 SELECT * INTO a FROM app.provider_attempts WHERE id=r.attempt_id AND task_id=t.id FOR UPDATE;
 IF a.provider_id IS NOT NULL AND a.provider_id<>r.provider_id THEN RAISE EXCEPTION 'receipt_conflict'; END IF;
 IF a.status IN ('succeeded','failed') THEN RETURN; END IF;
 UPDATE app.provider_attempts SET provider_id=r.provider_id,polling_url_private=r.polling_url_private,response_digest=r.response_digest,status='submitted',submitted_at=COALESCE(submitted_at,now()) WHERE id=a.id;
 UPDATE app.generation_tasks SET phase=CASE WHEN a.step='generate' THEN 'polling' ELSE 'detecting' END,revision=revision+1,updated_at=now() WHERE id=t.id;
END $$;
ALTER FUNCTION app.assert_lease(uuid,text,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.assert_lease(uuid,text,bigint) FROM PUBLIC;
ALTER FUNCTION ops.claim_task(uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.claim_task(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.claim_task(uuid,text) TO app_worker;
ALTER FUNCTION app.renew_lease(uuid,text,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.renew_lease(uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.renew_lease(uuid,text,bigint) TO app_worker;
ALTER FUNCTION app.worker_context(uuid,text,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.worker_context(uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.worker_context(uuid,text,bigint) TO app_worker;
ALTER FUNCTION app.record_intent(uuid,text,bigint,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_intent(uuid,text,bigint,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_intent(uuid,text,bigint,text,text) TO app_worker;
ALTER FUNCTION app.append_receipt(uuid,uuid,text,text,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.append_receipt(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.append_receipt(uuid,uuid,text,text,text) TO app_worker;
ALTER FUNCTION app.apply_receipt(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.apply_receipt(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.apply_receipt(uuid,text,bigint,uuid) TO app_worker;
