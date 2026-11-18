"""Real PG/Worker/S3 estimates; all supplier transport and prices are synthetic."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import psycopg
from psycopg import sql
from supplier_estimate_fixture import SupplierEstimateFixture

from visual_platform.services.provider_pricing import supplier_task_costs
from visual_platform.worker.runner import Worker


class SupplierEstimateTests(SupplierEstimateFixture):
    def test_wire_decimal_precision_survives_pg_observation_and_rounds_up(self):
        import hashlib
        import json

        import httpx

        original = self.bfl_route
        submitted = []

        def route(request):
            response = original(request)
            if request.method != "POST":
                return response
            value = response.json()
            value.pop("cost", None)
            raw = (json.dumps(value)[:-1] + ', "cost": 0.0600000000000000001 }').encode()
            submitted.append(raw)
            return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

        self.bfl_route = route
        task, accounts = self.metered()
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertEqual(self.costs(task)["estimates"][0]["amount"], "0.00300001")
        with psycopg.connect(self.owner) as connection:
            rows = connection.execute(
                "SELECT c.usage FROM app.task_cost_records c "
                "JOIN app.provider_attempts a ON a.id=c.attempt_id "
                "WHERE c.task_id=%s AND c.cost_record_kind='response_observation' "
                "AND a.step='generate'",
                (task,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0]["provider_cost"]["quantity"], "0.0600000000000000001")
        self.assertEqual(rows[0][0]["response_digest"], hashlib.sha256(submitted[0]).hexdigest())
        self.assert_unknown_original_observations(task)
        self.assertEqual(self.post_count("bfl"), 1)

    def costs(self, task):
        return supplier_task_costs(self.operator_dsn, UUID(task))

    def assert_unknown_original_observations(self, task):
        with psycopg.connect(self.owner) as c:
            observations = c.execute(
                "SELECT cost_status,amount,currency FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind IN ('intent_observation','response_observation')",
                (task,),
            ).fetchall()
        self.assertTrue(observations)
        self.assertTrue(all(row == ("unknown", None, None) for row in observations))

    def test_two_step_exact_estimates_preserve_unknown_observations_and_one_charge(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        costs = self.costs(task)
        self.assertFalse(costs["actual_spending_verified"])
        self.assertEqual(
            sorted(row["amount"] for row in costs["estimates"]), ["0.00002720", "0.00300000"]
        )
        self.assertTrue(
            all(
                row["cost_status"] == "estimated" and row["ceiling_state"] == "within_ceiling"
                for row in costs["estimates"]
            )
        )
        self.assert_unknown_original_observations(task)
        budget = self.snapshot()
        self.assertEqual(Decimal(budget["known_estimate_subtotal"]), Decimal("0.00302720"))
        self.assertTrue(budget["estimate_coverage_complete"])
        self.assertFalse(budget["blocked_for_cost_review"])
        self.assertFalse(budget["actual_spending_verified"])
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "not_claimed")
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
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_over_ceiling_stops_next_paid_step_preserves_freeze_and_cannot_reopen_budget(self):
        task, accounts = self.metered(generate_rate="5")
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "stopped")
        state = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual((state["status"], state["credits"]["status"]), ("uncertain", "frozen"))
        self.assertEqual(self.costs(task)["estimates"][0]["ceiling_state"], "over_ceiling")
        self.assertEqual(self.costs(task)["estimates"][0]["amount"], "0.30000000")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        self.assertEqual(self.snapshot()["state"], "blocked")
        self.assertTrue(self.snapshot()["blocked_for_cost_review"])
        with psycopg.connect(self.owner) as c:
            with self.assertRaisesRegex(
                psycopg.errors.RaiseException, "supplier_cost_review_cannot_reopen"
            ):
                c.execute(
                    "UPDATE ops.provider_budgets SET state='open' WHERE id=%s", (self.budget,)
                )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (84, 16, 0)
        )
        self.assert_unknown_original_observations(task)

    def test_nominal_balance_units_never_convert_to_usd_or_allow_new_paid_step(self):
        task, accounts = self.metered(currency="BFL_CREDIT")
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "stopped")
        estimate = self.costs(task)["estimates"][0]
        self.assertEqual(
            (estimate["currency"], estimate["ceiling_state"]), ("BFL_CREDIT", "unknown")
        )
        self.assertIsNone(self.snapshot()["known_estimate_subtotal"])
        self.assertFalse(self.snapshot()["estimate_coverage_complete"])
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))

    def test_missing_bfl_quantity_is_unknown_null_and_blocks_detection(self):
        route = self.bfl_route

        def missing(request):
            response = route(request)
            if request.method == "POST":
                import httpx

                data = response.json()
                del data["cost"]
                return httpx.Response(200, json=data)
            return response

        self.bfl_route = missing
        task, accounts = self.metered()
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "stopped")
        estimate = self.costs(task)["estimates"][0]
        self.assertEqual(
            (estimate["cost_status"], estimate["amount"], estimate["currency"]),
            ("unknown", None, None),
        )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        self.assert_unknown_original_observations(task)

    def test_incomplete_gemini_usage_stays_unknown_while_delivered_png_charges_once(self):
        task, accounts = self.metered(detect=True, complete=False)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        estimates = self.costs(task)["estimates"]
        self.assertEqual(len(estimates), 2)
        self.assertEqual(
            len([e for e in estimates if e["cost_status"] == "unknown" and e["amount"] is None]), 1
        )
        self.assertTrue(self.snapshot()["blocked_for_cost_review"])
        self.assertFalse(self.snapshot()["estimate_coverage_complete"])
        self.assertFalse(self.snapshot()["actual_spending_verified"])
        self.assertEqual(self.snapshot()["state"], "closed")
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["spent_total"], 16)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        self.assert_unknown_original_observations(task)

    def test_reported_model_mismatch_cannot_inherit_price_or_conclusive_facts(self):
        task, accounts = self.metered(detect=True, reported_model="gemini-4-argon")
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertTrue(
            any(
                e["reason"] == "reported_model_unverified" and e["amount"] is None
                for e in self.costs(task)["estimates"]
            )
        )
        report = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        self.assertFalse(report["facts"]["analysis_complete"])
        self.assertIn("gemini_reported_model_mismatch", report["facts"]["uncertainties"])
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_response_observation_survives_interruption_and_concurrent_estimate_is_unique(self):
        task, accounts = self.metered()

        class BeforeEstimate(Worker):
            async def db(self, query, *args):
                if "app.record_supplier_estimate" in query:
                    raise RuntimeError("controlled stop after response observation")
                return await super().db(query, *args)

        self.assertEqual(
            self.run_protocol(task, merchant_accounts=accounts, worker_class=BeforeEstimate),
            "retryable",
        )
        with psycopg.connect(self.worker_dsn) as c:
            claim = c.execute(
                "SELECT ops.claim_task(%s,%s,'offline_protocol_mock')", (task, "cost-concurrency")
            ).fetchone()[0]
        with psycopg.connect(self.owner) as c:
            attempt = c.execute(
                "SELECT id FROM app.provider_attempts WHERE task_id=%s AND step='generate'", (task,)
            ).fetchone()[0]

        def apply(_):
            with psycopg.connect(self.worker_dsn) as c:
                return c.execute(
                    "SELECT app.record_supplier_estimate(%s,%s,%s,%s)",
                    (task, "cost-concurrency", claim["fencing_token"], attempt),
                ).fetchone()[0]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(apply, range(4)))
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertTrue(all(r["amount"] == "0.00300000" for r in results))
        with psycopg.connect(self.worker_dsn) as c:
            c.execute(
                "SELECT app.yield_lease(%s,%s,%s)",
                (task, "cost-concurrency", claim["fencing_token"]),
            )
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertEqual(len(self.costs(task)["estimates"]), 1)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE target_id=%s AND action='supplier_cost_estimated'",
                    (task,),
                ).fetchone()[0],
                1,
            )

    def test_estimate_audit_failure_rolls_back_record_link_and_budget_then_resumes_receipt(self):
        task, accounts = self.metered()
        hook = "cost_abort_" + uuid4().hex
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='supplier_cost_estimated' THEN RAISE EXCEPTION 'controlled_estimate_audit_failure'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(hook))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.audit_events FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(hook), sql.Identifier(hook))
            )
        try:
            with self.assertRaisesRegex(asyncpg.RaiseError, "controlled_estimate_audit_failure"):
                self.run_protocol(task, merchant_accounts=accounts)
            self.assertEqual(self.costs(task)["estimates"], [])
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='estimate'",
                        (task,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='response_observation'",
                        (task,),
                    ).fetchone()[0],
                    1,
                )
            self.assertEqual(self.snapshot()["state"], "open")
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(
                    sql.SQL("DROP TRIGGER {} ON ops.audit_events").format(sql.Identifier(hook))
                )
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(hook)))
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_estimate_metadata_and_money_are_immutable_and_private(self):
        task, accounts = self.metered()
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        estimate = self.costs(task)["estimates"][0]
        for query in [
            "UPDATE app.task_cost_records SET amount=0 WHERE id=%s",
            "DELETE FROM ops.supplier_cost_estimates WHERE cost_record_id=%s",
        ]:
            with psycopg.connect(self.owner) as c:
                with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                    c.execute(query, (estimate["id"],))
        for dsn in [self.settings.api_database_url, self.worker_dsn]:
            with psycopg.connect(dsn) as c:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute("SELECT * FROM ops.supplier_cost_estimates")
        with psycopg.connect(self.settings.api_database_url) as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT ops.supplier_task_costs(%s)", (task,))
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                c.execute(
                    "INSERT INTO ops.supplier_cost_estimates(cost_record_id,user_id,task_id,attempt_id,response_observation_id,response_digest,price_id,price_document_sha256,ceiling_state) SELECT %s,user_id,task_id,%s,%s,response_digest,price_id,price_document_sha256,ceiling_state FROM ops.supplier_cost_estimates WHERE cost_record_id=%s",
                    (uuid4(), uuid4(), uuid4(), estimate["id"]),
                )

    def test_price_expired_before_intent_refunds_without_creating_hold_or_post(self):
        now = datetime.now(timezone.utc)
        until = now + timedelta(seconds=15)
        price, identity = self.publish(self.document(valid_until=until.isoformat()))
        execution = self.execution(price, identity)
        task = self.submit(self.bind, execution_updates=execution)
        time.sleep(max(0, (until - datetime.now(timezone.utc)).total_seconds()) + 0.5)
        self.assertEqual(
            self.run_protocol(task, merchant_accounts=execution["provider_accounts"]), "stopped"
        )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["available"], 100)
        self.assertEqual(self.snapshot()["holds"], [])

    def test_sub_quantum_positive_cost_rounds_up_without_binary_or_decimal_loss(self):
        task, accounts = self.metered(generate_rate="0.00000001")
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        self.assertEqual(self.costs(task)["estimates"][0]["amount"], "0.00000001")
        self.assertFalse(self.costs(task)["actual_spending_verified"])
