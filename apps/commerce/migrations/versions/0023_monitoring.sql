CREATE ROLE ops_monitor NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE app_monitor_reader NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA ops TO ops_monitor;
GRANT USAGE ON SCHEMA app,ops TO app_monitor_reader;
CREATE TABLE ops.service_heartbeats (
 component text NOT NULL CHECK(component IN ('publisher','worker')), instance_id uuid NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(), last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(component,instance_id)
);
ALTER TABLE ops.service_heartbeats ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.service_heartbeats FORCE ROW LEVEL SECURITY;
CREATE POLICY executor_heartbeats ON ops.service_heartbeats TO app_executor USING(true) WITH CHECK(true);
CREATE POLICY monitor_heartbeats ON ops.service_heartbeats FOR SELECT TO app_monitor_reader USING(true);
GRANT SELECT,INSERT,UPDATE,DELETE ON ops.service_heartbeats TO app_executor;
GRANT SELECT(component,last_seen_at) ON ops.service_heartbeats TO app_monitor_reader;
CREATE FUNCTION ops.touch_service(p_component text,p_instance uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_instance IS NULL OR NOT (
  (p_component='publisher' AND pg_has_role(session_user,'ops_dispatcher','MEMBER')) OR
  (p_component='worker' AND pg_has_role(session_user,'app_worker','MEMBER'))
 ) OR p_component IS NULL THEN RAISE EXCEPTION 'invalid_service_identity' USING ERRCODE='42501'; END IF;
 INSERT INTO ops.service_heartbeats(component,instance_id) VALUES(p_component,p_instance)
 ON CONFLICT(component,instance_id) DO UPDATE SET last_seen_at=clock_timestamp();
 DELETE FROM ops.service_heartbeats WHERE component=p_component AND last_seen_at<now()-interval '7 days';
END $$;
ALTER FUNCTION ops.touch_service(text,uuid) OWNER TO app_executor;
REVOKE ALL ON FUNCTION ops.touch_service(text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.touch_service(text,uuid) TO ops_dispatcher,app_worker;

GRANT SELECT(id,user_id,status,credits_status,reserved_credits,lease_until,created_at,updated_at,completed_at) ON app.generation_tasks TO app_monitor_reader;
GRANT SELECT(user_id,available,reserved,funded_total,spent_total) ON app.wallets TO app_monitor_reader;
GRANT SELECT(user_id,task_id,type,available_delta,reserved_delta,funded_delta,spent_delta) ON app.credit_transactions TO app_monitor_reader;
GRANT SELECT(published_at,created_at) ON ops.outbox TO app_monitor_reader;
GRANT SELECT(state) ON ops.task_dispatch TO app_monitor_reader;
CREATE POLICY monitor_tasks ON app.generation_tasks FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_wallets ON app.wallets FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_ledger ON app.credit_transactions FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_outbox ON ops.outbox FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_dispatch ON ops.task_dispatch FOR SELECT TO app_monitor_reader USING(true);
CREATE FUNCTION ops.monitor_snapshot() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH task_counts AS (SELECT status,count(*) amount FROM app.generation_tasks GROUP BY status),
 ledger AS (SELECT user_id,sum(available_delta) a,sum(reserved_delta) r,sum(funded_delta) f,sum(spent_delta) s FROM app.credit_transactions GROUP BY user_id),
 reserved AS (SELECT user_id,sum(reserved_credits) amount FROM app.generation_tasks WHERE credits_status='frozen' GROUP BY user_id),
 settled AS (SELECT task_id FROM app.credit_transactions WHERE type IN ('charge','refund') AND task_id IS NOT NULL GROUP BY task_id HAVING count(*)>1),
 latency AS (SELECT count(*) samples,percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at-created_at)) p95 FROM app.generation_tasks WHERE completed_at>=now()-interval '24 hours'),
 hb AS (SELECT component,GREATEST(0,extract(epoch FROM now()-max(last_seen_at))) age FROM ops.service_heartbeats GROUP BY component)
 SELECT jsonb_build_object(
 'observed_at',now(),
 'tasks',COALESCE((SELECT jsonb_object_agg(status,amount) FROM task_counts),'{}'::jsonb),
 'queued_oldest_seconds',(SELECT GREATEST(0,extract(epoch FROM now()-min(created_at))) FROM app.generation_tasks WHERE status='queued'),
 'unknown_oldest_seconds',(SELECT GREATEST(0,extract(epoch FROM now()-min(updated_at))) FROM app.generation_tasks WHERE status='uncertain'),
 'expired_leases',(SELECT count(*) FROM app.generation_tasks WHERE status='processing' AND (lease_until IS NULL OR lease_until<=now())),
 'outbox_pending',(SELECT count(*) FROM ops.outbox WHERE published_at IS NULL),
 'outbox_oldest_seconds',(SELECT GREATEST(0,extract(epoch FROM now()-min(created_at))) FROM ops.outbox WHERE published_at IS NULL),
 'manual_dispatch',(SELECT count(*) FROM ops.task_dispatch WHERE state IN ('manual','uncertain')),
 'wallet_equation_errors',(SELECT count(*) FROM app.wallets WHERE available<0 OR reserved<0 OR available+reserved<>funded_total-spent_total),
 'ledger_total_errors',(SELECT count(*) FROM app.wallets w LEFT JOIN ledger l USING(user_id) WHERE w.available<>COALESCE(l.a,0) OR w.reserved<>COALESCE(l.r,0) OR w.funded_total<>COALESCE(l.f,0) OR w.spent_total<>COALESCE(l.s,0)),
 'reservation_link_errors',(SELECT count(*) FROM app.wallets w LEFT JOIN reserved r USING(user_id) WHERE w.reserved<>COALESCE(r.amount,0)),
 'task_credit_state_errors',(SELECT count(*) FROM app.generation_tasks WHERE (status IN ('queued','processing','uncertain') AND credits_status<>'frozen') OR (status='succeeded' AND credits_status<>'charged') OR (status IN ('failed','cancelled') AND credits_status<>'refunded')),
 'duplicate_terminal_settlements',(SELECT count(*) FROM settled),
 'task_duration_24h',(SELECT jsonb_build_object('samples',samples,'p95_seconds',p95) FROM latency),
 'heartbeat_age_seconds',COALESCE((SELECT jsonb_object_agg(component,age) FROM hb),'{}'::jsonb)
 );
$$;
ALTER FUNCTION ops.monitor_snapshot() OWNER TO app_monitor_reader;
REVOKE ALL ON FUNCTION ops.monitor_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.monitor_snapshot() TO ops_monitor,ops_admin;
