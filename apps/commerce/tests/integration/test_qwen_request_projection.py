"""Real PG request admission; all supplier HTTP and rates remain synthetic."""

from uuid import uuid4

import psycopg
from bailian_fixture import BailianFixture
from bailian_pricing_fixture import BailianPricingFixture

from visual_platform.services.provider_budget import qwen_generation_projection


class QwenRequestProjectionTests(BailianPricingFixture):
    def test_projection_is_scoped_exact_and_not_a_supplier_observation(self):
        self.qwen_rate_per = 7
        quote, _, _ = self.qwen_review()
        projection = qwen_generation_projection(self.operator_dsn, self.user, quote)
        self.assertEqual(
            (projection["projection_status"], projection["amount"], projection["currency"]),
            ("projected", "0.04285715", "CNY"),
        )
        self.assertEqual(
            projection["quantities"],
            {"qima_input_1k": 1, "qima_input_2k": 0, "qima_output_1k": 1, "qima_output_2k": 0},
        )
        self.assertFalse(projection["actual_spending_verified"])
        self.assertFalse(projection["supplier_usage_observed"])
        self.assertFalse(projection["execution_enabled"])
        self.assertIsNone(qwen_generation_projection(self.operator_dsn, uuid4(), quote))
        with psycopg.connect(self.worker_dsn) as connection:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "SELECT ops.qwen_generation_projection(%s,%s)", (self.user, quote)
                )
        self.assertEqual(self.calls, [])
        with psycopg.connect(self.owner) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM app.generation_tasks WHERE quote_id=%s", (quote,)
                ).fetchone()[0],
                0,
            )

    def test_exact_cny_ceiling_admits_one_generation_and_one_detection(self):
        self.qwen_output_rate = "0.90"
        task = self.qwen_task()
        self.assertEqual(self.run_qwen(task), "succeeded")
        self.assertEqual((self.post_count("bailian"), self.post_count("gemini")), (1, 1))

    def test_projection_uses_frozen_output_area_at_the_tier_boundary(self):
        for edge, expected in ((1500, "0.30000000"), (1501, "0.90000000")):
            self.runtime_output_edge = edge
            quote, _, _ = self.qwen_review()
            result = qwen_generation_projection(self.operator_dsn, self.user, quote)
            self.assertEqual(result["amount"], expected)
            self.assertEqual(result["quantities"]["qima_output_1k"], int(edge == 1500))
            self.assertEqual(result["quantities"]["qima_output_2k"], int(edge == 1501))
        self.assertEqual(self.calls, [])


class UnpricedQwenProjectionTests(BailianFixture):
    def test_unpriced_mock_rehearsal_stays_unknown_without_inventing_a_zero(self):
        quote, _, _ = self.qwen_review()
        result = qwen_generation_projection(self.operator_dsn, self.user, quote)
        self.assertEqual(result["projection_status"], "unknown")
        self.assertEqual(result["reason"], "unpriced_rehearsal")
        self.assertIsNone(result["amount"])
        self.assertIsNone(result["currency"])
        self.assertFalse(result["supplier_usage_observed"])
