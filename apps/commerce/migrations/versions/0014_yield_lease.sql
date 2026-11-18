CREATE FUNCTION app.yield_lease(p_task uuid,p_owner text,p_fence bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid;
BEGIN
 SELECT user_id INTO u FROM ops.task_dispatch WHERE task_id=p_task;
 IF u IS NULL THEN RETURN; END IF;
 PERFORM set_config('app.user_id',u::text,true);
 UPDATE app.generation_tasks SET lease_owner=NULL,lease_until=clock_timestamp() WHERE id=p_task AND user_id=u AND status='processing' AND lease_owner=p_owner AND fencing_token=p_fence;
 IF FOUND THEN UPDATE ops.task_dispatch SET lease_until=clock_timestamp() WHERE task_id=p_task; END IF;
END $$;
ALTER FUNCTION app.yield_lease(uuid,text,bigint) OWNER TO app_executor;
REVOKE ALL ON FUNCTION app.yield_lease(uuid,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.yield_lease(uuid,text,bigint) TO app_worker;
