"""Real PG async image estimates and unit ceilings; all prices and HTTP synthetic."""

from uuid import UUID, uuid4

import psycopg
from bailian_pricing_fixture import BailianPricingFixture

from visual_platform.services.provider_pricing import supplier_task_costs
from visual_platform.services.runtime_quote import prepare_runtime_quote


class QwenScopedEstimateTests(BailianPricingFixture):
    def costs(self, task):
        return supplier_task_costs(self.operator_dsn, UUID(task))

    def test_pending_submit_has_no_premature_estimate_then_cny_and_usd_estimates_are_separate(self):
        task = self.qwen_task()
        self.pending = True
        self.assertEqual(self.run_qwen(task), "retryable")
        self.assertEqual(self.costs(task)["estimates"], [])
        self.pending = False
        self.assertEqual(self.run_qwen(task), "succeeded")
        costs = self.costs(task)
        self.assertFalse(costs["actual_spending_verified"])
        self.assertEqual(
            {(row["currency"], row["amount"]) for row in costs["estimates"]},
            {("CNY", "0.30000000"), ("USD", "0.00002720")},
        )
        self.assertTrue(all(row["ceiling_state"] == "within_ceiling" for row in costs["estimates"]))
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 1))
        with psycopg.connect(self.owner) as connection:
            rows = connection.execute(
                "SELECT a.step,e.observation_kind,e.calculation_version FROM ops.supplier_cost_estimates e JOIN app.provider_attempts a ON a.id=e.attempt_id WHERE a.task_id=%s ORDER BY a.step",
                (task,),
            ).fetchall()
            originals = connection.execute(
                "SELECT cost_status,amount,currency FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind IN ('intent_observation','response_observation','result_observation')",
                (task,),
            ).fetchall()
            charged = connection.execute(
                "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                (task,),
            ).fetchone()[0]
        self.assertEqual(
            rows,
            [
                ("detect", "response_observation", "exact-rational-24-decimal-v2"),
                ("generate", "result_observation", "exact-rational-24-decimal-v2"),
            ],
        )
        self.assertTrue(all(row == ("unknown", None, None) for row in originals))
        self.assertEqual(charged, 1)
        self.assertIsNone(self.snapshot()["currency"])

    def test_missing_terminal_usage_blocks_detection_without_treating_it_as_free(self):
        self.qwen_usage_complete = False
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "stopped")
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 0))
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual((result["status"], result["credits"]["status"]), ("uncertain", "frozen"))
        estimate = self.costs(task)["estimates"][0]
        self.assertEqual(
            (estimate["cost_status"], estimate["amount"], estimate["currency"]),
            ("unknown", None, None),
        )

    def test_known_over_cny_request_stops_before_any_supplier_or_cost_observation(self):
        self.qwen_output_rate = "2.00"
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "stopped")
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (0, 0))
        self.assertEqual(self.costs(task)["estimates"], [])
        self.assertEqual(self.snapshot()["holds"], [])
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (100, 0, 0)
        )
        with psycopg.connect(self.owner) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT generated_version_id FROM app.generation_tasks WHERE id=%s", (task,)
                ).fetchone()[0]
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )

    def test_wrong_price_merchant_cannot_prepare_quote_or_freeze_internal_credits(self):
        bindings = {
            "generate": self.publish_price("generate", merchant="f" * 64),
            "detect": self.publish_price("detect"),
        }
        source, _ = self.source_quote()
        before = self.client.get("/api/v1/credits/balance").json()
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "price_scope_mismatch"):
            prepare_runtime_quote(
                self.operator_dsn,
                self.user,
                source,
                self.qwen_runtime,
                self.qwen_runtime.digest(),
                uuid4(),
                "synthetic wrong merchant",
                price_versions=bindings,
            )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])
