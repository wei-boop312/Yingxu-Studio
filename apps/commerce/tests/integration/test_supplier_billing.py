"""Private billing preflight against actual PG receipts/archives; all supplier calls are mocked."""

import hashlib
import json
from uuid import UUID, uuid4

import psycopg
from supplier_estimate_fixture import SupplierEstimateFixture

from visual_platform.domain.supplier_billing import SupplierBillReview, review_supplier_bill
from visual_platform.services.provider_pricing import supplier_billing_context


class SupplierBillingTests(SupplierEstimateFixture):
    evidence = b"Synthetic offline rate evidence; not actual supplier prices or invoice."

    def attempts(self, task):
        with psycopg.connect(self.owner) as c:
            return dict(
                c.execute(
                    "SELECT step,id FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchall()
            )

    def context(self, task, attempt):
        return supplier_billing_context(self.operator_dsn, UUID(task), attempt)

    def bill(self, context, **changes):
        return SupplierBillReview.model_validate_json(
            json.dumps(
                {
                    **context["scope"],
                    "schema_version": 1,
                    "amount": "0.001",
                    "evidence_kind": "offline_fixture",
                    "evidence_sha256": hashlib.sha256(self.evidence).hexdigest(),
                    "statement_fingerprint": "d" * 64,
                    "line_fingerprint": "e" * 64,
                    **changes,
                }
            )
        )

    def test_both_known_steps_match_frozen_context_and_gemini_uses_remote_response_id(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        before = self.client.get("/api/v1/credits/balance").json()
        before_budget = self.snapshot()
        for step, attempt in self.attempts(task).items():
            context = self.context(task, attempt)
            self.assertTrue(context["receipt_applied"])
            self.assertTrue(context["price_scope_verified"])
            self.assertFalse(context["actual_spending_verified"])
            if step == "detect":
                self.assertEqual(context["scope"]["provider_request_id"], "detector-result")
                self.assertNotEqual(
                    context["scope"]["provider_request_id"], "gemini-" + str(attempt)
                )
            review = review_supplier_bill(
                self.bill(context), context, self.evidence, evidence_reviewed=True
            )
            self.assertEqual(review["result"], "OFFLINE_BILL_SCOPE_MATCHED")
            self.assertFalse(review["confirmation_enabled"])
        self.assertEqual(self.snapshot(), before_budget)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_status='confirmed'",
                    (task,),
                ).fetchone()[0],
                0,
            )

    def test_real_statement_wrong_merchant_or_unit_cannot_confirm_mock_spending(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        context = self.context(task, self.attempts(task)["detect"])
        for change, reason in (
            ({"evidence_kind": "reviewed_statement"}, "not_enabled"),
            ({"merchant_fingerprint": "f" * 64}, "scope_mismatch"),
            ({"currency": "CNY"}, "scope_mismatch"),
            ({"provider_request_id": "gemini-" + context["scope"]["attempt_id"]}, "scope_mismatch"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, reason):
                review_supplier_bill(
                    self.bill(context, **change), context, self.evidence, evidence_reviewed=True
                )

    def test_absent_remote_response_id_remains_unverifiable_not_local_alias(self):
        route = self.gemini_route

        def missing(request):
            import httpx

            response = route(request)
            data = response.json()
            data.pop("responseId")
            return httpx.Response(200, json=data)

        self.gemini_route = missing
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        context = self.context(task, self.attempts(task)["detect"])
        self.assertIsNone(context["scope"]["provider_request_id"])
        with self.assertRaisesRegex(ValueError, "scope_mismatch"):
            review_supplier_bill(
                self.bill(context, provider_request_id="unverifiable-fixture"),
                context,
                self.evidence,
                evidence_reviewed=True,
            )

    def test_nominal_price_versus_usd_legacy_hold_cannot_pass_bill_scope(self):
        task, accounts = self.metered(currency="BFL_CREDIT")
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "stopped")
        context = self.context(task, self.attempts(task)["generate"])
        self.assertFalse(context["price_scope_verified"])
        with self.assertRaisesRegex(ValueError, "requires_applied"):
            review_supplier_bill(self.bill(context), context, self.evidence, evidence_reviewed=True)

    def test_private_function_rejects_api_worker_owner_service_and_wrong_attempt(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        attempt = self.attempts(task)["generate"]
        with self.assertRaisesRegex(ValueError, "non-owner"):
            supplier_billing_context(self.owner, UUID(task), attempt)
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "known_protocol_receipt_required"
        ):
            self.context(task, uuid4())
        for role in ("app_api", "app_worker"):
            with psycopg.connect(self.owner) as c:
                from psycopg import sql

                c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute("SELECT ops.supplier_billing_context(%s,%s)", (task, attempt))
