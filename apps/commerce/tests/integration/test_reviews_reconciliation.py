import hashlib
import json
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from test_offline_pipeline import PipelineFixture

from visual_platform.providers.contract import ProviderUncertain
from visual_platform.providers.fake import DurableFakeProvider


class ReviewReconciliationTests(PipelineFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_role = "admin_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.admin_role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT ops_admin TO {}").format(sql.Identifier(cls.admin_role)))
        u = urlsplit(cls.owner)
        cls.admin_dsn = urlunsplit(
            (
                u.scheme,
                quote(cls.admin_role)
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
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.admin_role)))
        super().tearDownClass()

    def admin(self, query, args):
        with psycopg.connect(self.admin_dsn) as c:
            return c.execute(query, args).fetchone()[0]

    def test_concurrent_review_append_only_keeps_auto_report(self):
        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        original = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        operation = uuid4()
        args = (
            task,
            original["id"],
            operation,
            self.user,
            "approved",
            "checked the product identity",
            Jsonb(["local:inspection-proof"]),
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(
                pool.map(
                    lambda _: self.admin("SELECT app.review_check(%s,%s,%s,%s,%s,%s,%s)", args),
                    range(8),
                )
            )
        self.assertEqual(len(set(ids)), 1)
        result = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        self.assertEqual(result["auto_overall"], "manual_review")
        self.assertEqual(result["effective_overall"], "compliant")
        self.assertEqual(result["effective_source"], "human_review")
        self.assertEqual(original["report_sha256"], result["report_sha256"])
        self.assertEqual(
            len(self.client.get("/api/v1/tasks/" + task + "/compliance/reviews").json()["items"]), 1
        )
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION app_api")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT app.review_check(%s,%s,%s,%s,%s,%s,%s)", args)
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute(
                    "UPDATE app.compliance_reviews SET reason=%s WHERE id=%s", ("tamper", ids[0])
                )

    def test_late_known_receipt_resumes_without_new_generate(self):
        class LostResponse(DurableFakeProvider):
            def submit_generate(self, *args):
                super().submit_generate(*args)
                raise ProviderUncertain("response lost after supplier success")

        task = self.submit()
        self.assertEqual(
            self.run_worker(task, provider=LostResponse(Path(self.folder.name))), "stopped"
        )
        details = self.admin("SELECT ops.task_details(%s)", (task,))
        attempt = details["attempts"][0]["id"]
        path = next(Path(self.folder.name).glob("generate-*.json"))
        raw = path.read_bytes()
        record = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        receipt = self.admin(
            "SELECT ops.accept_receipt(%s,%s,%s,%s,NULL,%s,%s,%s)",
            (
                task,
                attempt,
                record["provider_id"],
                digest,
                uuid4(),
                "verified persisted fake supplier receipt",
                digest,
            ),
        )
        operation = uuid4()
        args = (
            task,
            operation,
            "resume",
            receipt,
            "receipt validated; resume polling only",
            digest,
        )
        for _ in range(2):
            self.admin("SELECT ops.reconcile_task(%s,%s,%s,%s,%s,%s)", args)
        self.assertEqual(
            self.run_worker(task, provider=DurableFakeProvider(Path(self.folder.name))), "succeeded"
        )
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["spent_total"], 16)

    def test_manual_close_unknown_refunds_once_and_cli_inspects(self):
        task = self.submit()
        self.run_worker(
            task, provider=DurableFakeProvider(Path(self.folder.name), mode="uncertain")
        )
        env = {**os.environ, "ADMIN_DATABASE_URL": self.admin_dsn}
        cli = subprocess.run(
            [sys.executable, "scripts/admin.py", "inspect", "--task", task],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["task"]["status"], "uncertain")
        operation = uuid4()
        args = (
            task,
            operation,
            "fail",
            None,
            "unable to deliver; internal credits released",
            "a" * 64,
        )
        for _ in range(2):
            self.admin("SELECT ops.reconcile_task(%s,%s,%s,%s,%s,%s)", args)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "failed")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (100, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='refund'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE target_id=%s AND action='reconcile_fail'",
                    (task,),
                ).fetchone()[0],
                1,
            )
