from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
from test_task_api import TaskApiFixture


class WorkerDbTests(TaskApiFixture):
    def setUp(self):
        super().setUp()
        self.task = self.submit(self.quote()).json()["id"]

    def worker(self, query, args=()):
        with psycopg.connect(self.owner) as c:
            c.execute("SET SESSION AUTHORIZATION app_worker")
            return c.execute(query, args).fetchone()[0]

    def claim(self, owner="worker-1"):
        return self.worker("SELECT ops.claim_task(%s,%s)", (self.task, owner))

    def expire(self):
        with psycopg.connect(self.owner) as c:
            c.execute(
                "UPDATE app.generation_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",
                (self.task,),
            )

    def intent(self, fence, step="generate", owner="worker-1"):
        return self.worker(
            "SELECT app.record_intent(%s,%s,%s,%s,%s)", (self.task, owner, fence, step, "a" * 64)
        )

    def receipt(self, attempt):
        return self.worker(
            "SELECT app.append_receipt(%s,%s,%s,%s,NULL)",
            (self.task, attempt, str(uuid4()), "b" * 64),
        )

    def balance(self):
        with psycopg.connect(self.owner) as c:
            return c.execute(
                "SELECT available,reserved,spent_total FROM app.wallets WHERE user_id=%s",
                (self.user,),
            ).fetchone()

    def test_concurrent_claim_fencing_and_null_token_rejection(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda n: self.claim("w" + str(n)), range(8)))
        self.assertEqual(sum(x is not None for x in claims), 1)
        old = next(x for x in claims if x)
        self.expire()
        new = self.claim("worker-1")
        self.assertGreater(new["fencing_token"], old["fencing_token"])
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.worker(
                "SELECT app.renew_lease(%s,%s,%s)", (self.task, "worker-1", old["fencing_token"])
            )
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.worker("SELECT app.renew_lease(%s,%s,NULL)", (self.task, "worker-1"))
        self.worker(
            "SELECT app.renew_lease(%s,%s,%s)", (self.task, "worker-1", new["fencing_token"])
        )

    def test_unknown_never_reclaimed_and_late_receipt_append_only(self):
        claim = self.claim()
        fence = claim["fencing_token"]
        attempt = self.intent(fence)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.intent(fence)
        with self.assertRaises(psycopg.errors.RaiseException):
            self.worker(
                "SELECT app.fail_task(%s,%s,%s,%s,false)", (self.task, "worker-1", fence, "timeout")
            )
        self.expire()
        result = self.claim("new-worker")
        self.assertEqual(result["status"], "uncertain")
        receipt = self.receipt(attempt)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.worker(
                "SELECT app.apply_receipt(%s,%s,%s,%s)", (self.task, "worker-1", fence, receipt)
            )
        self.assertIsNone(self.claim("third-worker"))
        self.assertEqual(self.balance(), (0, 16, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.provider_receipts WHERE task_id=%s", (self.task,)
                ).fetchone()[0],
                1,
            )

    def prepare_delivery(self, *, with_detection_receipt=True):
        fence = self.claim()["fencing_token"]
        generate = self.intent(fence)
        receipt = self.receipt(generate)
        self.worker(
            "SELECT app.apply_receipt(%s,%s,%s,%s)", (self.task, "worker-1", fence, receipt)
        )
        for kind in ("generated", "final"):
            self.worker(
                "SELECT app.record_task_asset(%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,100)",
                (
                    self.task,
                    "worker-1",
                    fence,
                    kind,
                    uuid4(),
                    uuid4(),
                    "test",
                    str(uuid4()),
                    "c" * 64,
                    self.body["generation"]["output"]["width"],
                    self.body["generation"]["output"]["height"],
                ),
            )
        detect = self.intent(fence, "detect")
        if with_detection_receipt:
            detect_receipt = self.receipt(detect)
            self.worker(
                "SELECT app.apply_receipt(%s,%s,%s,%s)",
                (self.task, "worker-1", fence, detect_receipt),
            )
        context = self.worker("SELECT app.worker_context(%s,%s,%s)", (self.task, "worker-1", fence))
        results = [
            {
                "platform": r["platform"],
                "marketplace": r.get("marketplace", ""),
                "category": r.get("category", ""),
                "image_type": r.get("image_type", ""),
                "rule_version": r.get("version", "unverified"),
                "evidence_status": r.get("evidence_status", "unverified"),
                "status": "manual_review",
            }
            for r in context["input_snapshot"]["_execution"]["rules"]
        ]
        self.worker(
            "SELECT app.record_report(%s,%s,%s,%s,%s,%s,%s,%s,false,%s,%s,%s,NULL)",
            (
                self.task,
                "worker-1",
                fence,
                uuid4(),
                "c" * 64,
                Jsonb({"analysis_complete": False}),
                Jsonb(results),
                "manual_review",
                "d" * 64,
                "test",
                str(uuid4()),
            ),
        )
        return fence

    def test_manual_report_cannot_bypass_missing_detection_receipt_without_review(self):
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "detection_receipt_required"):
            self.prepare_delivery(with_detection_receipt=False)
        self.assertEqual(self.balance(), (0, 16, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.compliance_checks WHERE task_id=%s", (self.task,)
                ).fetchone()[0],
                0,
            )

    def test_delivery_settles_once_manual_review_allowed(self):
        fence = self.prepare_delivery()
        for _ in range(2):
            self.worker("SELECT app.settle_task(%s,%s,%s)", (self.task, "worker-1", fence))
        self.assertEqual(self.balance(), (0, 0, 16))
        result = self.client.get("/api/v1/tasks/" + self.task)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["result"]["sha256"], "c" * 64)
        report = self.client.get("/api/v1/tasks/" + self.task + "/compliance")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()["auto_overall"], "manual_review")
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (self.task,),
                ).fetchone()[0],
                1,
            )

    def test_charge_refund_race_has_single_terminal_ledger(self):
        fence = self.prepare_delivery()

        def finish(charge):
            try:
                if charge:
                    self.worker("SELECT app.settle_task(%s,%s,%s)", (self.task, "worker-1", fence))
                else:
                    self.worker(
                        "SELECT app.fail_task(%s,%s,%s,%s,false)",
                        (self.task, "worker-1", fence, "local_delivery_failure"),
                    )
                return True
            except psycopg.errors.ObjectNotInPrerequisiteState:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(finish, [True, False]))
        self.assertEqual(sum(outcomes), 1)
        a, r, s = self.balance()
        self.assertEqual(a + r, 16 - s)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type IN ('charge','refund')",
                    (self.task,),
                ).fetchone()[0],
                1,
            )

    def test_no_report_no_settlement_and_definite_failure_refunds(self):
        fence = self.claim()["fencing_token"]
        with self.assertRaises(psycopg.errors.RaiseException):
            self.worker("SELECT app.settle_task(%s,%s,%s)", (self.task, "worker-1", fence))
        self.assertEqual(self.balance(), (0, 16, 0))
        for _ in range(2):
            self.worker(
                "SELECT app.fail_task(%s,%s,%s,%s,false)",
                (self.task, "worker-1", fence, "input_unavailable"),
            )
        self.assertEqual(self.balance(), (16, 0, 0))
