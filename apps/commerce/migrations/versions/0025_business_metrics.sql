CREATE INDEX metric_event_time ON app.task_events(created_at,task_id,id);
CREATE INDEX metric_attempt_time ON app.provider_attempts(intent_at);
CREATE INDEX metric_cost_time ON app.task_cost_records(observed_at,attempt_id);
CREATE INDEX metric_check_time ON app.compliance_checks(created_at);

GRANT SELECT(id,task_id,created_at,payload) ON app.task_events TO app_monitor_reader;
GRANT SELECT(provider,step,status,intent_at,submitted_at) ON app.provider_attempts TO app_monitor_reader;
GRANT SELECT(attempt_id,cost_record_kind,provider,currency,amount,cost_status,observed_at) ON app.task_cost_records TO app_monitor_reader;
GRANT SELECT(created_at,auto_overall) ON app.compliance_checks TO app_monitor_reader;
CREATE POLICY monitor_phase_events ON app.task_events FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_attempts ON app.provider_attempts FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_costs ON app.task_cost_records FOR SELECT TO app_monitor_reader USING(true);
CREATE POLICY monitor_checks ON app.compliance_checks FOR SELECT TO app_monitor_reader USING(true);

CREATE FUNCTION ops.business_metrics_snapshot() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH events AS (
  SELECT task_id,id,created_at,
   CASE WHEN payload->>'status'='processing' AND payload->>'phase' IN ('submitting','polling','archiving','protecting','detecting','validating_rules','finalizing') THEN payload->>'phase' END phase
  FROM app.task_events WHERE created_at>=now()-interval '24 hours'
 ), changes AS (
  SELECT *,lag(phase) OVER (PARTITION BY task_id ORDER BY id) previous FROM events
 ), marks AS (
  SELECT task_id,id,created_at,phase FROM changes WHERE phase IS DISTINCT FROM previous
 ), spans AS (
  SELECT phase,created_at,lead(created_at) OVER (PARTITION BY task_id ORDER BY id) ended_at FROM marks
 ), phases AS (
  SELECT phase,count(*) samples,sum(extract(epoch FROM ended_at-created_at)) total_seconds,
   percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM ended_at-created_at)) p95_seconds
  FROM spans WHERE phase IS NOT NULL AND ended_at IS NOT NULL AND ended_at>=created_at GROUP BY phase
 ), activity AS (
  SELECT CASE WHEN provider IN ('fake','bfl_mock','gemini_mock','bfl','gemini') THEN provider ELSE 'other' END provider,
   step,count(*) submission_intents,count(*) FILTER (WHERE submitted_at IS NOT NULL) receipt_count,
   count(*) FILTER (WHERE status='uncertain') uncertain_count
  FROM app.provider_attempts WHERE intent_at>=now()-interval '24 hours' GROUP BY 1,2
 ), recent_cost_attempts AS (
  SELECT DISTINCT attempt_id FROM app.task_cost_records WHERE observed_at>=now()-interval '24 hours'
 ), effective_cost AS (
  SELECT DISTINCT ON (c.attempt_id) c.attempt_id,c.provider,c.currency,c.amount,c.cost_status
  FROM app.task_cost_records c JOIN recent_cost_attempts r USING(attempt_id)
  ORDER BY c.attempt_id,CASE c.cost_record_kind WHEN 'settlement' THEN 1 WHEN 'response_observation' THEN 2 WHEN 'intent_observation' THEN 3 ELSE 4 END
 ), costs AS (
  SELECT CASE WHEN provider IN ('fake','bfl_mock','gemini_mock','bfl','gemini') THEN provider ELSE 'other' END provider,
   currency,cost_status,count(*) attempts,
   CASE WHEN cost_status='unknown' THEN NULL ELSE sum(amount)::text END amount
  FROM effective_cost GROUP BY 1,2,3
 ), checks AS (
  SELECT count(*) samples,count(*) FILTER (WHERE auto_overall='manual_review') manual,
   count(*) FILTER (WHERE auto_overall='non_compliant') non_compliant
  FROM app.compliance_checks WHERE created_at>=now()-interval '24 hours'
 )
 SELECT jsonb_build_object(
  'observed_at',now(),'window_seconds',86400,
  'phase_scope','closed persisted phase spans starting within 24h; consecutive identical phase events merged; not CPU time',
  'phase_duration',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY phase) FROM phases p),'[]'::jsonb),
  'provider_activity_scope','write-ahead intents and known receipts for intents started within 24h; not confirmed billable HTTP calls',
  'provider_activity',COALESCE((SELECT jsonb_agg(to_jsonb(a) ORDER BY provider,step) FROM activity a),'[]'::jsonb),
  'cost_scope','one authoritative observation per recently observed attempt; settlement precedes response, intent and estimate; currencies/statuses never combined',
  'provider_cost',COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY provider,currency,cost_status) FROM costs c),'[]'::jsonb),
  'compliance_scope','final compliance check records within 24h, including rule-only rechecks; not human review decisions',
  'compliance',(SELECT jsonb_build_object('samples',samples,'manual_review',manual,'non_compliant',non_compliant,'manual_review_rate',manual::numeric/NULLIF(samples,0)) FROM checks),
  'missing_measurements',jsonb_build_array('api_duration_and_5xx','archive_failure_count')
 );
$$;
ALTER FUNCTION ops.business_metrics_snapshot() OWNER TO app_monitor_reader;
REVOKE ALL ON FUNCTION ops.business_metrics_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.business_metrics_snapshot() TO ops_monitor,ops_admin;
