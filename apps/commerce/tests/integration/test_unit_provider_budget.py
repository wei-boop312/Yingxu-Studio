"""Real PG/Worker/S3 unit budgets; all prices and supplier transport are synthetic."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
from supplier_estimate_fixture import SupplierEstimateFixture

from visual_platform.services.provider_budget import register_provider_budget
from visual_platform.services.provider_pricing import supplier_task_costs


class UnitProviderBudgetTests(SupplierEstimateFixture):
    generate_unit = "BFL_CREDIT"
    detect_unit = "HUBWAY_BALANCE_UNIT"
    generate_cap = "0.10"
    detect_cap = "0.02"

    def bind(self, quote, snapshot, **kwargs):
        self.quote = quote
        steps = {
            "generate": {"currency": self.generate_unit, "ceiling": self.generate_cap},
            "detect": {"currency": self.detect_unit, "ceiling": self.detect_cap},
        }
        totals = {}
        for row in steps.values():
            totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + Decimal(
                row["ceiling"]
            )
        return super().bind(
            quote,
            snapshot,
            changed={
                "schema_version": 3,
                "step_budgets": steps,
                "currency_totals": [
                    {"currency": unit, "ceiling": str(value)} for unit, value in totals.items()
                ],
            },
        )

    def costs(self, task):
        return supplier_task_costs(self.operator_dsn, UUID(task))

    def test_distinct_units_deliver_once_with_no_cross_unit_total_or_invoice_claim(self):
        task, accounts = self.metered(
            currency="BFL_CREDIT", detect=True, detect_currency="HUBWAY_BALANCE_UNIT"
        )
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["schema_version"], 3)
        for key in (
            "currency",
            "total_ceiling",
            "reserved_ceiling_total",
            "known_estimate_subtotal",
        ):
            self.assertIsNone(snapshot[key])
        by_unit = {row["currency"]: row for row in snapshot["limits_by_currency"]}
        self.assertEqual(
            Decimal(by_unit["BFL_CREDIT"]["known_estimate_subtotal"]), Decimal("0.003")
        )
        self.assertEqual(
            Decimal(by_unit["HUBWAY_BALANCE_UNIT"]["known_estimate_subtotal"]),
            Decimal("0.00002720"),
        )
        self.assertTrue(snapshot["estimate_coverage_complete"])
        self.assertFalse(snapshot["actual_spending_verified"])
        self.assertEqual(snapshot["state"], "closed")
        self.assertTrue(
            all(row["ceiling_state"] == "within_ceiling" for row in self.costs(task)["estimates"])
        )
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "not_claimed")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_status='confirmed'",
                    (task,),
                ).fetchone()[0],
                0,
            )

    def test_same_unit_combines_only_that_units_reservations_and_estimates(self):
        self.generate_unit = self.detect_unit = "CNY"
        task, accounts = self.metered(currency="CNY", detect=True, detect_currency="CNY")
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        rows = self.snapshot()["limits_by_currency"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["currency"], "CNY")
        self.assertEqual(Decimal(rows[0]["reserved_ceiling_total"]), Decimal("0.12"))
        self.assertEqual(Decimal(rows[0]["known_estimate_subtotal"]), Decimal("0.00302720"))

    def test_generation_over_its_own_unit_cap_blocks_detection_and_keeps_freeze(self):
        self.generate_cap = "0.001"
        task, accounts = self.metered(
            currency="BFL_CREDIT", detect=True, detect_currency="HUBWAY_BALANCE_UNIT"
        )
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "stopped")
        self.assertEqual(self.costs(task)["estimates"][0]["ceiling_state"], "over_ceiling")
        self.assertEqual(self.snapshot()["state"], "blocked")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        state = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual((state["status"], state["credits"]["status"]), ("uncertain", "frozen"))

    def test_price_unit_mismatch_rejects_registration_before_intent_or_post(self):
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "provider_budget_price_unit_mismatch"
        ):
            self.metered(currency="USD", detect=True, detect_currency="HUBWAY_BALANCE_UNIT")
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.provider_budgets WHERE quote_id=%s", (self.quote,)
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.generation_tasks WHERE quote_id=%s", (self.quote,)
                ).fetchone()[0],
                0,
            )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))

    def test_both_frozen_prices_are_required_even_before_first_generation(self):
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "provider_budget_price_unit_mismatch"
        ):
            self.metered(currency="BFL_CREDIT")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))

    def test_sql_bypass_rejects_missing_duplicate_underfunded_and_masquerading_units(self):
        task, _ = self.metered(
            currency="BFL_CREDIT", detect=True, detect_currency="HUBWAY_BALANCE_UNIT"
        )
        original = self.scope.model_dump(mode="json")
        variants = []
        for totals in (
            [],
            original["currency_totals"][:1],
            original["currency_totals"][:1] * 2,
            [{"currency": "BFL_CREDIT", "ceiling": "0.000001"}, original["currency_totals"][1]],
        ):
            variants.append({**original, "currency_totals": totals})
        for ceiling in (True, 0.1, "1e2", "-1"):
            data = json.loads(json.dumps(original))
            data["step_budgets"]["generate"]["ceiling"] = ceiling
            variants.append(data)
        variants.append({**original, "currency": "USD"})
        for data in variants:
            with self.subTest(data=data), psycopg.connect(self.operator_dsn) as c:
                canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                with self.assertRaises(psycopg.errors.RaiseException):
                    c.execute(
                        "SELECT ops.register_provider_budget(%s,%s,%s,%s,%s,%s)",
                        (
                            self.scope.authorization_id,
                            self.quote,
                            canonical,
                            digest,
                            uuid4(),
                            "invalid unit fixture",
                        ),
                    )
        self.assertEqual(len(self.costs(task)["estimates"]), 0)

    def test_concurrent_identical_registration_replays_without_new_limits_or_audit(self):
        self.metered(currency="BFL_CREDIT", detect=True, detect_currency="HUBWAY_BALANCE_UNIT")

        def replay(_):
            return register_provider_budget(
                self.operator_dsn,
                self.quote,
                self.scope,
                self.digest,
                self.operation,
                "synthetic offline budget",
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(replay, range(4))), [self.budget] * 4)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.provider_step_limits WHERE authorization_id=%s",
                    (self.budget,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (self.operation,)
                ).fetchone()[0],
                1,
            )

    def test_private_immutable_unit_limits_and_no_legacy_scalar_budget_values(self):
        self.metered(currency="BFL_CREDIT", detect=True, detect_currency="HUBWAY_BALANCE_UNIT")
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT currency,generate_ceiling,detect_ceiling,total_ceiling FROM ops.provider_budgets WHERE id=%s",
                    (self.budget,),
                ).fetchone(),
                (None, None, None, None),
            )
            for role in ("app_api", "app_worker"):
                for table in ("ops.provider_step_limits", "ops.provider_total_limits"):
                    self.assertFalse(
                        c.execute(
                            "SELECT has_table_privilege(%s,%s,'SELECT')", (role, table)
                        ).fetchone()[0]
                    )
        for table in ("ops.provider_step_limits", "ops.provider_total_limits"):
            with psycopg.connect(self.owner) as c:
                with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                    c.execute("DELETE FROM " + table + " WHERE authorization_id=%s", (self.budget,))
