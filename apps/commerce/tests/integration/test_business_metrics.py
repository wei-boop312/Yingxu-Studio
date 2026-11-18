import json
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from test_offline_pipeline import PipelineFixture

from visual_platform.providers.fake import DurableFakeProvider


class BusinessMetricsTests(PipelineFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.monitor_role = "business_metric_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.monitor_role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT ops_monitor TO {}").format(sql.Identifier(cls.monitor_role)))
        u = urlsplit(cls.owner)
        cls.monitor_dsn = urlunsplit(
            (
                u.scheme,
                quote(cls.monitor_role)
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
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.monitor_role)))
        super().tearDownClass()

    def snapshot(self):
        with psycopg.connect(self.monitor_dsn) as c:
            return c.execute("SELECT ops.business_metrics_snapshot()").fetchone()[0]

    def activity(self, snapshot, step):
        return next(
            (
                row
                for row in snapshot["provider_activity"]
                if row["provider"] == "fake" and row["step"] == step
            ),
            {"submission_intents": 0, "receipt_count": 0},
        )

    def test_real_offline_pipeline_populates_aggregates_without_user_or_request_contents(self):
        before = self.snapshot()
        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        after = self.snapshot()
        for step in ("generate", "detect"):
            self.assertEqual(
                self.activity(after, step)["submission_intents"],
                self.activity(before, step)["submission_intents"] + 1,
            )
            self.assertEqual(
                self.activity(after, step)["receipt_count"],
                self.activity(before, step)["receipt_count"] + 1,
            )
        self.assertEqual(after["compliance"]["samples"], before["compliance"]["samples"] + 1)
        self.assertEqual(
            after["compliance"]["manual_review"], before["compliance"]["manual_review"] + 1
        )
        self.assertGreater(len(after["phase_duration"]), 0)
        encoded = json.dumps(after)
        for private in (
            str(self.user),
            self.email,
            task,
            "input_snapshot",
            "facts",
            "polling_url_private",
        ):
            self.assertNotIn(private, encoded)
        self.assertEqual(
            set(after["missing_measurements"]), {"api_duration_and_5xx", "archive_failure_count"}
        )

    def test_settlement_does_not_double_count_later_estimate_or_mix_its_currency(self):
        task = self.submit()
        self.run_worker(task)
        before = self.snapshot()["provider_cost"]
        with psycopg.connect(self.owner) as c:
            attempt = c.execute(
                "SELECT id FROM app.provider_attempts WHERE task_id=%s AND step='generate'", (task,)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,price_source,price_version) VALUES(%s,%s,%s,'estimate','fake','fake-v1','EUR',99.99,'estimated','synthetic-test','test')",
                (self.user, task, attempt),
            )
        self.assertEqual(self.snapshot()["provider_cost"], before)

    def test_unknown_cost_keeps_unknown_currency_and_null_amount(self):
        task = self.submit()
        self.assertEqual(
            self.run_worker(
                task, provider=DurableFakeProvider(Path(self.folder.name), mode="uncertain")
            ),
            "stopped",
        )
        with psycopg.connect(self.owner) as c:
            attempt = c.execute(
                "SELECT id FROM app.provider_attempts WHERE task_id=%s AND step='generate'", (task,)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO app.task_cost_records(user_id,task_id,attempt_id,cost_record_kind,provider,model,currency,amount,cost_status,price_source,price_version) VALUES(%s,%s,%s,'intent_observation','fake','fake-v1',NULL,NULL,'unknown','synthetic-unpriced-test','test')",
                (self.user, task, attempt),
            )
        row = next(
            row
            for row in self.snapshot()["provider_cost"]
            if row["provider"] == "fake"
            and row["cost_status"] == "unknown"
            and row["currency"] is None
        )
        self.assertGreater(row["attempts"], 0)
        self.assertIsNone(row["amount"])

    def test_duplicate_phase_events_are_coalesced_and_aggregate_role_cannot_read_rows(self):
        task = self.submit()
        before = next(
            (row for row in self.snapshot()["phase_duration"] if row["phase"] == "protecting"),
            {"samples": 0, "total_seconds": 0},
        )
        with psycopg.connect(self.owner) as c:
            for phase, seconds in (("protecting", 10), ("protecting", 8), ("finalizing", 6)):
                c.execute(
                    "INSERT INTO app.task_events(user_id,task_id,type,payload,created_at) VALUES(%s,%s,'task.updated',jsonb_build_object('status','processing','phase',%s::text,'revision',2),now()-(%s*interval '1 second'))",
                    (self.user, task, phase, seconds),
                )
        after = next(
            row for row in self.snapshot()["phase_duration"] if row["phase"] == "protecting"
        )
        self.assertEqual(after["samples"], before["samples"] + 1)
        self.assertAlmostEqual(after["total_seconds"] - before["total_seconds"], 4, places=6)
        for query in (
            "SELECT payload FROM app.task_events",
            "SELECT amount FROM app.task_cost_records",
            "SET ROLE app_monitor_reader",
        ):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(self.monitor_dsn) as c:
                    c.execute(query)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.owner) as c:
                c.execute("SET SESSION AUTHORIZATION app_api")
                c.execute("SELECT ops.business_metrics_snapshot()")
