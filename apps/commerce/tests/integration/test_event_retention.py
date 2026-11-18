"""Actual PG retention, SSE reset and idempotence; suppliers remain offline Fake."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from provider_budget_fixture import ProviderBudgetFixture
from psycopg import sql
from test_offline_pipeline import PipelineFixture

from visual_platform.services.event_retention import event_retention


class EventRetentionTests(ProviderBudgetFixture):
    def old_task(self):
        task = PipelineFixture.submit(self)
        self.assertEqual(self.run_worker(task), "succeeded")
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.task_events SET created_at=clock_timestamp()-interval '31 days' WHERE task_id=%s",
                (task,),
            )
            ids = [
                x[0]
                for x in c.execute(
                    "SELECT id FROM app.task_events WHERE task_id=%s ORDER BY id", (task,)
                ).fetchall()
            ]
        self.assertGreater(len(ids), 2)
        return task, ids

    def business_snapshot(self, task):
        with psycopg.connect(self.owner) as c:
            return (
                c.execute(
                    "SELECT to_jsonb(t) FROM app.generation_tasks t WHERE id=%s", (task,)
                ).fetchone(),
                c.execute(
                    "SELECT to_jsonb(t) FROM app.credit_transactions t WHERE task_id=%s ORDER BY id",
                    (task,),
                ).fetchall(),
                c.execute(
                    "SELECT to_jsonb(t) FROM app.task_cost_records t WHERE task_id=%s ORDER BY id",
                    (task,),
                ).fetchall(),
                self.client.get("/api/v1/credits/balance").json(),
            )

    def test_default_thirty_days_preserves_business_state_and_resets_actual_old_cursor(self):
        task, ids = self.old_task()
        before = self.business_snapshot(task)
        plan = event_retention(self.operator_dsn, self.user)
        self.assertEqual(plan["eligible_in_batch"], len(ids))
        self.assertEqual(plan["retention_days"], 30)
        operation = uuid4()
        result = event_retention(
            self.operator_dsn, self.user, operation=operation, reason="synthetic retention"
        )
        self.assertEqual(result["deleted_count"], len(ids))
        stream = self.client.get(
            f"/api/v1/tasks/{task}/events", headers={"last-event-id": str(ids[0])}
        )
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: stream.reset", stream.text)
        self.assertNotIn("event: task.updated", stream.text)
        self.assertEqual(self.business_snapshot(task), before)
        replay = event_retention(
            self.operator_dsn, self.user, operation=operation, reason="synthetic retention"
        )
        self.assertEqual(result, replay)
        self.assertEqual(event_retention(self.operator_dsn, self.user)["eligible_in_batch"], 0)

    def test_bounded_parallel_same_operation_deletes_one_batch_only(self):
        task, ids = self.old_task()
        operation = uuid4()

        def run(_):
            return event_retention(
                self.operator_dsn,
                self.user,
                limit=1,
                operation=operation,
                reason="parallel bounded batch",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(results[0]["deleted_count"], 1)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_events WHERE task_id=%s", (task,)
                ).fetchone()[0],
                len(ids) - 1,
            )
        with self.assertRaises(psycopg.errors.RaiseException):
            event_retention(
                self.operator_dsn,
                self.user,
                limit=2,
                operation=operation,
                reason="parallel bounded batch",
            )

    def test_sparse_pruning_uses_watermark_not_just_minimum_surviving_event(self):
        task, ids = self.old_task()
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.task_events SET created_at=clock_timestamp() WHERE task_id=%s AND id<>%s",
                (task, ids[-2]),
            )
        result = event_retention(
            self.operator_dsn, self.user, operation=uuid4(), reason="sparse old event"
        )
        self.assertEqual(result["deleted_count"], 1)
        stream = self.client.get(
            f"/api/v1/tasks/{task}/events", headers={"last-event-id": str(ids[-2])}
        )
        self.assertIn("event: stream.reset", stream.text)
        self.assertIn(f"id: {ids[-1]}\n", stream.text)
        with psycopg.connect(self.settings.api_database_url) as c:
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(uuid4()),))
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_event_retention_cursors WHERE task_id=%s",
                    (task,),
                ).fetchone()[0],
                0,
            )

    def test_recent_events_permissions_and_sql_bounds_remain_protected(self):
        task, ids = self.old_task()
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.task_events SET created_at=clock_timestamp() WHERE task_id=%s", (task,)
            )
        result = event_retention(
            self.operator_dsn, self.user, days=1, operation=uuid4(), reason="recent unaffected"
        )
        self.assertEqual(result["deleted_count"], 0)
        with (
            psycopg.connect(self.settings.api_database_url) as c,
            self.assertRaises(psycopg.errors.InsufficientPrivilege),
        ):
            c.execute("SELECT ops.prune_task_events(%s,30,1000,%s,'deny')", (self.user, uuid4()))
        with (
            psycopg.connect(self.settings.api_database_url) as c,
            self.assertRaises(psycopg.errors.InsufficientPrivilege),
        ):
            c.execute("DELETE FROM app.task_events WHERE task_id=%s", (task,))
        for days, limit in ((0, 1), (3651, 1), (30, 0), (30, 10001)):
            with (
                psycopg.connect(self.operator_dsn) as c,
                self.assertRaises(psycopg.errors.RaiseException),
            ):
                c.execute(
                    "SELECT ops.prune_task_events(%s,%s,%s,%s,'invalid')",
                    (self.user, days, limit, uuid4()),
                )

    def test_audit_failure_rolls_back_deletion_cursor_and_receipt(self):
        task, ids = self.old_task()
        operation = uuid4()
        hook = "retention_abort_" + uuid4().hex
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='task_events_pruned' THEN RAISE EXCEPTION 'controlled_event_audit_failure'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(hook))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.audit_events FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(hook), sql.Identifier(hook))
            )
        try:
            with self.assertRaisesRegex(
                psycopg.errors.RaiseException, "controlled_event_audit_failure"
            ):
                event_retention(
                    self.operator_dsn, self.user, operation=operation, reason="audit rollback"
                )
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.task_events WHERE task_id=%s", (task,)
                    ).fetchone()[0],
                    len(ids),
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.task_event_retention_cursors WHERE task_id=%s",
                        (task,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.task_event_retention_runs WHERE operation_id=%s",
                        (operation,),
                    ).fetchone()[0],
                    0,
                )
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(
                    sql.SQL("DROP TRIGGER {} ON ops.audit_events").format(sql.Identifier(hook))
                )
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(hook)))

    def test_active_stream_acknowledges_sparse_high_watermark_once_per_connection(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from uuid import UUID

        from visual_platform.api.tasks import events
        from visual_platform.db.pool import open_pool

        task = PipelineFixture.submit(self)
        with psycopg.connect(self.owner) as c:
            for age in ("0 days", "31 days"):
                c.execute(
                    'INSERT INTO app.task_events(user_id,task_id,type,payload,created_at) VALUES(%s,%s,\'task.updated\',\'{"status":"queued","revision":1}\',clock_timestamp()-%s::interval)',
                    (self.user, task, age),
                )
            ids = [
                row[0]
                for row in c.execute(
                    "SELECT id FROM app.task_events WHERE task_id=%s ORDER BY id", (task,)
                ).fetchall()
            ]
        event_retention(
            self.operator_dsn,
            self.user,
            operation=uuid4(),
            reason="high sparse watermark active stream",
        )

        async def collect(cursor):
            pool = await open_pool(self.settings.api_database_url, "app_api")
            iterations = 0

            async def disconnected():
                nonlocal iterations
                iterations += 1
                return iterations > 2

            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(api_pool=pool)),
                state=SimpleNamespace(),
                is_disconnected=disconnected,
            )
            try:
                with patch(
                    "visual_platform.api.tasks.current_user",
                    new=AsyncMock(return_value={"id": self.user}),
                ):
                    response = await events(
                        UUID(task),
                        request,
                        last_event_id=cursor,
                        cursor=None,
                        user={"id": self.user},
                    )
                    return "".join([chunk async for chunk in response.body_iterator])
            finally:
                await pool.close()

        try:
            # Real PG and generator; authentication heartbeat/disconnect are controlled,
            # while separate endpoint tests cover actual HTTP authentication.
            output = asyncio.run(collect(str(ids[-1])))
            self.assertEqual(output.count("event: stream.reset"), 1)
            self.assertEqual(output.count("event: task.updated"), len(ids) - 1)
            initial = asyncio.run(collect("0"))
            self.assertEqual(initial.count("event: stream.reset"), 0)
            self.assertEqual(initial.count("event: task.updated"), len(ids) - 1)
        finally:
            self.assertEqual(
                self.client.post(
                    f"/api/v1/tasks/{task}/cancel", headers=self.write_headers()
                ).status_code,
                200,
            )
