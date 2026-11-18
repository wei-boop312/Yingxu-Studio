CREATE POLICY executor_audit_replay ON ops.audit_events FOR SELECT TO app_executor USING(true);
