CREATE OR REPLACE FUNCTION app.worker_context(p_task uuid,p_owner text,p_fence bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; source app.asset_versions;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO source FROM app.asset_versions WHERE id=t.source_asset_version_id AND user_id=t.user_id;
 RETURN to_jsonb(t)||jsonb_build_object('source',to_jsonb(source),'generated',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=t.generated_version_id),'final',(SELECT to_jsonb(v) FROM app.asset_versions v WHERE v.id=t.final_version_id),'attempts',COALESCE((SELECT jsonb_agg(to_jsonb(a)) FROM app.provider_attempts a WHERE task_id=t.id),'[]'::jsonb));
END $$;
CREATE FUNCTION app.record_fake_cost(p_task uuid,p_owner text,p_fence bigint,p_attempt uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t app.generation_tasks; a app.provider_attempts;
BEGIN
 t:=app.assert_lease(p_task,p_owner,p_fence);
 SELECT * INTO a FROM app.provider_attempts WHERE id=p_attempt AND task_id=t.id AND provider='fake';
 IF NOT FOUND THEN RAISE EXCEPTION 'fake_attempt_required'; END IF;
 INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,price_source,price_version)
 VALUES(t.user_id,t.id,a.id,'settlement','fake',a.model,'USD',0,'confirmed','offline-no-paid-calls','fake-v1') ON CONFLICT(attempt_id,cost_record_kind) DO NOTHING;
END $$;
ALTER FUNCTION app.record_fake_cost(uuid,text,bigint,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.record_fake_cost(uuid,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_fake_cost(uuid,text,bigint,uuid) TO app_worker;
CREATE FUNCTION app.phase_event() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.phase IS DISTINCT FROM OLD.phase AND NEW.status='processing' AND OLD.status='processing' THEN
  INSERT INTO app.task_events(user_id,task_id,type,payload) VALUES(NEW.user_id,NEW.id,'task.updated',jsonb_build_object('status',NEW.status,'phase',NEW.phase,'revision',NEW.revision));
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION app.phase_event() FROM PUBLIC;
CREATE TRIGGER task_phase_events AFTER UPDATE OF phase ON app.generation_tasks FOR EACH ROW EXECUTE FUNCTION app.phase_event();
