"""Actual PG/budgets/Worker/S3 Qwen rehearsal; suppliers are explicit HTTP mocks."""

import base64
import copy
import hashlib
import json
from uuid import uuid4

import psycopg
from bailian_fixture import BailianFixture


class BailianRehearsalTests(BailianFixture):
    def test_sql_supplier_dimensions_reject_before_quote_or_internal_freeze(self):
        _, _, review = self.qwen_review()
        before = self.client.get("/api/v1/credits/balance").json()
        for width, height in ((256, 256), (2048, 2048), (4096, 256), (True, 512), (512.0, 512)):
            snapshot = copy.deepcopy(review["input_snapshot"])
            snapshot["generation"]["output"].update(width=width, height=height)
            with (
                self.subTest(width=width, height=height),
                psycopg.connect(self.owner) as connection,
            ):
                connection.execute("SET LOCAL ROLE app_api")
                connection.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "bailian_output_dimensions_out_of_range"
                ):
                    connection.execute(
                        "SELECT app.create_quote(%s,%s,%s,%s::jsonb,%s::jsonb)",
                        (
                            uuid4(),
                            "a" * 64,
                            hashlib.sha256(json.dumps(snapshot).encode()).hexdigest(),
                            json.dumps(snapshot),
                            json.dumps(snapshot["_execution"]["rules"]),
                        ),
                    )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])

    def test_full_qwen_worker_uses_distinct_attempt_model_units_and_one_internal_charge(self):
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        self.assertEqual(
            (self.post_count("bailian"), self.post_count("gemini"), self.post_count("bfl")),
            (1, 1, 0),
        )
        reference = self.qwen_payload["input"]["messages"][0]["content"][0]["image"]
        self.assertEqual(base64.b64decode(reference.split(",", 1)[1]), self.source)
        self.assertEqual(self.qwen_payload["parameters"]["size"], "512*512")
        self.assertEqual(self.run_qwen(task), "not_claimed")
        with psycopg.connect(self.owner) as connection:
            attempts = connection.execute(
                "SELECT step,provider,model FROM app.provider_attempts WHERE task_id=%s ORDER BY step",
                (task,),
            ).fetchall()
            costs = connection.execute(
                "SELECT cost_status,amount,currency FROM app.task_cost_records WHERE task_id=%s",
                (task,),
            ).fetchall()
            charged = connection.execute(
                "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                (task,),
            ).fetchone()[0]
        self.assertEqual(
            attempts,
            [
                ("detect", "gemini_mock", "gemini-3.8-flash"),
                ("generate", "bailian_mock", "qwen-image-3.0"),
            ],
        )
        self.assertTrue(costs)
        self.assertTrue(all(row == ("unknown", None, None) for row in costs))
        self.assertEqual(charged, 1)
        holds = self.snapshot()["holds"]
        self.assertEqual(
            {(row["step"], row["currency"]) for row in holds},
            {("generate", "CNY"), ("detect", "USD")},
        )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["reserved"], balance["spent_total"]), (0, 16))

    def test_missing_qwen_budget_refunds_internal_freeze_before_any_post(self):
        task = self.qwen_task(with_budget=False)
        self.assertEqual(self.run_qwen(task), "stopped")
        self.assertEqual(self.calls, [])
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (100, 0, 0)
        )

    def test_unknown_qwen_submit_preserves_freeze_and_is_not_reposted(self):
        task = self.qwen_task()
        self.timeout_step = "generate"
        self.assertEqual(self.run_qwen(task), "stopped")
        self.assertEqual(self.run_qwen(task), "not_claimed")
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 0))
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual((result["status"], result["credits"]["status"]), ("uncertain", "frozen"))

    def test_known_pending_qwen_task_resumes_with_get_without_another_post(self):
        task = self.qwen_task()
        self.pending = True
        self.assertEqual(self.run_qwen(task), "retryable")
        self.pending = False
        self.assertEqual(self.run_qwen(task), "succeeded")
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 1))

    def test_sql_budget_rejects_legacy_model_and_cross_unit_scope_before_new_funds(self):
        quote, _, review = self.qwen_review()
        self.bind_qwen(quote, review["input_snapshot"])
        for change in ("model", "legacy", "unit"):
            data = copy.deepcopy(self.qwen_scope_data)
            data["authorization_id"] = str(uuid4())
            if change == "model":
                data["generation_model"] = "flux-3-image"
            elif change == "legacy":
                data["bfl_model"] = "flux-3-image"
            else:
                data["step_budgets"]["generate"]["currency"] = "BFL_CREDIT"
                data["currency_totals"][0]["currency"] = "BFL_CREDIT"
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
            with self.subTest(change=change), psycopg.connect(self.operator_dsn) as connection:
                with self.assertRaises(psycopg.errors.RaiseException):
                    connection.execute(
                        "SELECT ops.register_provider_budget(%s,%s,%s,%s,%s,%s)",
                        (
                            data["authorization_id"],
                            quote,
                            canonical,
                            hashlib.sha256(canonical.encode()).hexdigest(),
                            uuid4(),
                            "synthetic negative",
                        ),
                    )
        self.assertEqual(self.calls, [])
