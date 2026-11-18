import asyncio
import json
import os
import secrets
import subprocess
import sys
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from test_task_api import TaskApiFixture

from visual_platform.db.pool import open_pool
from visual_platform.services.heartbeat import service_heartbeat
from visual_platform.services.monitoring import alerts


class MonitoringTests(TaskApiFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.extra_roles = []
        cls.urls = {}
        with psycopg.connect(cls.owner) as c:
            for group in ("ops_monitor", "app_worker", "ops_dispatcher"):
                name = "metric_" + uuid4().hex
                password = secrets.token_urlsafe(32)
                cls.extra_roles.append(name)
                c.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                    ).format(sql.Identifier(name), sql.Literal(password))
                )
                c.execute(
                    sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(name))
                )
                u = urlsplit(cls.owner)
                cls.urls[group] = urlunsplit(
                    (
                        u.scheme,
                        quote(name)
                        + ":"
                        + quote(password)
                        + "@"
                        + u.hostname
                        + ":"
                        + str(u.port or 5432),
                        u.path,
                        u.query,
                        u.fragment,
                    )
                )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            for name in cls.extra_roles:
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))
        super().tearDownClass()

    def snapshot(self):
        with psycopg.connect(self.urls["ops_monitor"]) as c:
            return c.execute("SELECT ops.monitor_snapshot()").fetchone()[0]

    def test_only_aggregate_access_and_no_operator_or_business_writes(self):
        value = self.snapshot()
        encoded = json.dumps(value)
        self.assertNotIn(str(self.user), encoded)
        self.assertNotIn(self.email, encoded)
        for query in (
            "SELECT input_snapshot FROM app.generation_tasks",
            "SELECT password_hash FROM auth.credentials",
            "SELECT * FROM ops.service_heartbeats",
            "SELECT ops.touch_service('worker',gen_random_uuid())",
            "SET ROLE app_monitor_reader",
            "SELECT ops.task_details(NULL::uuid)",
        ):
            with self.subTest(query=query), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(self.urls["ops_monitor"]) as c:
                    c.execute(query)
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION app_api")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT ops.monitor_snapshot()")

    def test_unknown_backlog_age_and_no_duplicate_financial_action(self):
        before = self.snapshot()
        task = self.submit(self.quote()).json()["id"]
        with psycopg.connect(self.urls["app_worker"]) as c:
            claim = c.execute("SELECT ops.claim_task(%s,'monitor-test')", (task,)).fetchone()[0]
            fence = claim["fencing_token"]
            c.execute(
                "SELECT app.record_intent(%s,'monitor-test',%s,'generate',%s)",
                (task, fence, "a" * 64),
            )
            c.execute(
                "SELECT app.fail_task(%s,'monitor-test',%s,'submission_outcome_unknown',true)",
                (task, fence),
            )
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.generation_tasks SET updated_at=now()-interval '10 minutes' WHERE id=%s",
                (task,),
            )
        after = self.snapshot()
        self.assertEqual(
            after["tasks"].get("uncertain", 0), before["tasks"].get("uncertain", 0) + 1
        )
        self.assertGreaterEqual(after["unknown_oldest_seconds"], 600)
        self.assertIn("unknown_backlog", {a["code"] for a in alerts(after)})
        self.assertEqual(after["ledger_total_errors"], before["ledger_total_errors"])
        self.assertEqual(after["reservation_link_errors"], before["reservation_link_errors"])
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["reserved"], 16)
        self.snapshot()
        self.snapshot()
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["reserved"], 16)

    def test_financial_inconsistency_detected_and_rollback_clears_it(self):
        baseline = self.snapshot()["ledger_total_errors"]
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.wallets SET available=available-1,spent_total=spent_total+1 WHERE user_id=%s",
                (self.user,),
            )
            c.execute("SET LOCAL ROLE ops_monitor")
            value = c.execute("SELECT ops.monitor_snapshot()").fetchone()[0]
            self.assertEqual(value["ledger_total_errors"], baseline + 1)
            self.assertIn("ledger_total_errors", {a["code"] for a in alerts(value)})
            c.rollback()
        self.assertEqual(self.snapshot()["ledger_total_errors"], baseline)

    def test_heartbeat_identity_staleness_and_background_cancellation(self):
        with psycopg.connect(self.urls["ops_dispatcher"]) as c:
            c.execute("SELECT ops.touch_service('publisher',%s)", (uuid4(),))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.urls["app_worker"]) as c:
                c.execute("SELECT ops.touch_service('publisher',%s)", (uuid4(),))

        async def run():
            pool = await open_pool(self.urls["app_worker"], "app_worker")
            try:
                async with service_heartbeat(pool, "worker", interval=0.05):
                    for _ in range(30):
                        await asyncio.sleep(0.02)
                        if (self.snapshot()["heartbeat_age_seconds"].get("worker") or 999) < 5:
                            break
                    self.assertLess(self.snapshot()["heartbeat_age_seconds"]["worker"], 5)
            finally:
                await pool.close()

        asyncio.run(run())
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE ops.service_heartbeats SET last_seen_at=now()-interval '1 minute' WHERE component='worker'"
            )
            c.execute("SET LOCAL ROLE ops_monitor")
            value = c.execute("SELECT ops.monitor_snapshot()").fetchone()[0]
            self.assertIn("worker_heartbeat_missing", {a["code"] for a in alerts(value)})
            c.rollback()

    def test_cli_sanitizes_and_rejects_owner_identity(self):
        command = [sys.executable, "scripts/monitor.py", "--prometheus"]
        result = subprocess.run(
            command,
            env={**os.environ, "MONITOR_DATABASE_URL": self.urls["ops_monitor"]},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn(result.returncode, (0, 1, 2))
        self.assertIn("visual_tasks", result.stdout)
        self.assertNotIn(str(self.user), result.stdout)
        denied = subprocess.run(
            command,
            env={**os.environ, "MONITOR_DATABASE_URL": self.owner},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(denied.returncode, 3)
        self.assertIn("visual_monitor_scrape_error 1", denied.stdout)
        self.assertNotIn(self.owner, denied.stdout + denied.stderr)
