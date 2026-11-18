-- Internal dispatch is not tenant data. Its executor is NOLOGIN and only reachable through restricted functions.
CREATE POLICY executor_dispatch_update ON ops.task_dispatch FOR UPDATE TO app_executor USING(true) WITH CHECK(true);
