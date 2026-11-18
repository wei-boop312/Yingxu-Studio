CREATE TABLE ops.task_event_retention_runs (
 operation_id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.users(id),
 retention_days integer NOT NULL CHECK(retention_days BETWEEN 1 AND 3650), batch_limit integer NOT NULL CHECK(batch_limit BETWEEN 1 AND 10000),
 cutoff timestamptz NOT NULL, deleted_count integer NOT NULL CHECK(deleted_count>=0 AND deleted_count<=batch_limit),
 first_deleted_id bigint, last_deleted_id bigint,
 actor text NOT NULL, reason text NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 4000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((deleted_count=0 AND first_deleted_id IS NULL AND last_deleted_id IS NULL)
 OR (deleted_count>0 AND first_deleted_id IS NOT NULL AND last_deleted_id IS NOT NULL AND first_deleted_id<=last_deleted_id))
);
ALTER TABLE ops.task_event_retention_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.task_event_retention_runs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON ops.task_event_retention_runs FROM PUBLIC;
GRANT SELECT,INSERT ON ops.task_event_retention_runs TO app_executor;
GRANT SELECT ON ops.task_event_retention_runs TO ops_admin;
CREATE POLICY event_retention_tenant ON ops.task_event_retention_runs TO app_executor
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);
CREATE POLICY event_retention_operator ON ops.task_event_retention_runs FOR SELECT TO ops_admin USING(true);
CREATE TRIGGER immutable_event_retention BEFORE UPDATE OR DELETE ON ops.task_event_retention_runs FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
GRANT DELETE ON app.task_events TO app_executor;
CREATE TABLE app.task_event_retention_cursors (
 user_id uuid NOT NULL, task_id uuid NOT NULL, pruned_through bigint NOT NULL CHECK(pruned_through>0),
 PRIMARY KEY(user_id,task_id), FOREIGN KEY(user_id,task_id) REFERENCES app.generation_tasks(user_id,id)
);
ALTER TABLE app.task_event_retention_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.task_event_retention_cursors FORCE ROW LEVEL SECURITY;
REVOKE ALL ON app.task_event_retention_cursors FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON app.task_event_retention_cursors TO app_executor;
GRANT SELECT ON app.task_event_retention_cursors TO app_api;
CREATE POLICY event_cursor_tenant ON app.task_event_retention_cursors
 USING(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid) WITH CHECK(user_id=NULLIF(current_setting('app.user_id',true),'')::uuid);

CREATE FUNCTION ops.preview_task_event_retention(p_user uuid,p_days integer DEFAULT 30,p_limit integer DEFAULT 1000) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE cutoff timestamptz; n integer;
BEGIN
 IF p_user IS NULL OR p_days IS NULL OR p_days NOT BETWEEN 1 AND 3650 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 10000 THEN RAISE EXCEPTION 'invalid_event_retention_scope'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 cutoff:=clock_timestamp()-make_interval(days=>p_days);
 SELECT count(*) INTO n FROM (
  SELECT e.id FROM app.task_events e WHERE e.user_id=p_user AND e.created_at<cutoff
  ORDER BY e.created_at,e.id LIMIT p_limit
 ) candidates;
 RETURN jsonb_build_object('retention_days',p_days,'cutoff',cutoff,'batch_limit',p_limit,'eligible_in_batch',n,'business_task_state_preserved',true);
END $$;

CREATE FUNCTION ops.prune_task_events(p_user uuid,p_days integer,p_limit integer,p_operation uuid,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior ops.task_event_retention_runs; saved ops.task_event_retention_runs; cutoff_at timestamptz; n integer; first_id bigint; last_id bigint;
BEGIN
 IF p_user IS NULL OR p_days IS NULL OR p_days NOT BETWEEN 1 AND 3650 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 10000
 OR p_operation IS NULL OR p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 4000 THEN RAISE EXCEPTION 'invalid_event_retention_scope'; END IF;
 PERFORM set_config('app.user_id',p_user::text,true);
 PERFORM pg_advisory_xact_lock(hashtextextended('event-retention:'||p_user::text,0));
 SELECT * INTO prior FROM ops.task_event_retention_runs WHERE operation_id=p_operation;
 IF FOUND THEN
  IF prior.user_id IS DISTINCT FROM p_user OR prior.retention_days IS DISTINCT FROM p_days OR prior.batch_limit IS DISTINCT FROM p_limit OR prior.reason IS DISTINCT FROM p_reason THEN RAISE EXCEPTION 'event_retention_operation_conflict'; END IF;
  RETURN to_jsonb(prior)-'actor'-'reason'-'user_id';
 END IF;
 cutoff_at:=clock_timestamp()-make_interval(days=>p_days);
 WITH candidates AS MATERIALIZED (
  SELECT e.id FROM app.task_events e WHERE e.user_id=p_user AND e.created_at<cutoff_at
  ORDER BY e.created_at,e.id LIMIT p_limit
 ), removed AS (
  DELETE FROM app.task_events e USING candidates c WHERE e.id=c.id AND e.user_id=p_user RETURNING e.id,e.task_id
 ), watermarks AS (
  INSERT INTO app.task_event_retention_cursors(user_id,task_id,pruned_through)
  SELECT p_user,task_id,max(id) FROM removed GROUP BY task_id
  ON CONFLICT(user_id,task_id) DO UPDATE SET pruned_through=GREATEST(app.task_event_retention_cursors.pruned_through,EXCLUDED.pruned_through)
  RETURNING task_id
 ) SELECT count(*),min(id),max(id) INTO n,first_id,last_id FROM removed;
 INSERT INTO ops.task_event_retention_runs(operation_id,user_id,retention_days,batch_limit,cutoff,deleted_count,first_deleted_id,last_deleted_id,actor,reason)
 VALUES(p_operation,p_user,p_days,p_limit,cutoff_at,n,first_id,last_id,session_user,p_reason) RETURNING * INTO saved;
 INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,after_state,request_id)
 VALUES(session_user,'task_events_pruned','user',p_user,p_reason,jsonb_build_object('retention_days',p_days,'cutoff',cutoff_at,'deleted_count',n,'business_task_state_preserved',true),p_operation);
 RETURN to_jsonb(saved)-'actor'-'reason'-'user_id';
END $$;
ALTER FUNCTION ops.preview_task_event_retention(uuid,integer,integer) OWNER TO app_executor;
ALTER FUNCTION ops.prune_task_events(uuid,integer,integer,uuid,text) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.preview_task_event_retention(uuid,integer,integer),ops.prune_task_events(uuid,integer,integer,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.preview_task_event_retention(uuid,integer,integer),ops.prune_task_events(uuid,integer,integer,uuid,text) TO ops_admin;
