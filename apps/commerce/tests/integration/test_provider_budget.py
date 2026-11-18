"""Real non-owner PG/Worker/S3 budget reservations; supplier HTTP remains mocked."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
from provider_budget_fixture import ProviderBudgetFixture

from visual_platform.providers.authorization import SmokeAuthorization, canonical_authorization
from visual_platform.services.provider_budget import (
    provider_quote_review,
    register_provider_budget,
    review_blocked_provider_budget,
)
from visual_platform.services.quotes import hash_data


class ProviderBudgetTests(ProviderBudgetFixture):
    def test_reviewed_quote_hash_binds_explicit_hubway_mock_endpoint_through_delivery(self):
        from visual_platform.providers.pipeline import BASELINE_ENDPOINTS

        endpoints = {
            **BASELINE_ENDPOINTS,
            "detect": "https://hubway.cc/v1beta/models/gemini-3.8-flash:generateContent",
        }
        task = self.submit(self.bind, execution_updates={"provider_endpoints": endpoints})
        self.assertEqual(self.scope.quote_input_sha256, hash_data(self.last_snapshot))
        self.assertEqual(self.run_protocol(task, gemini_origin="https://hubway.cc"), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        self.assertEqual(self.snapshot()["state"], "closed")
        self.assertFalse(self.snapshot()["actual_spending_verified"])

    def uncertain_receipt(self, *, lifetime=3600):
        task = self.submit(lambda quote, snapshot: self.bind(quote, snapshot, lifetime=lifetime))
        self.timeout_step = "generate"
        self.assertEqual(self.run_protocol(task), "stopped")
        self.timeout_step = None
        with psycopg.connect(self.owner) as c:
            attempt = c.execute(
                "SELECT id FROM app.provider_attempts WHERE task_id=%s AND step='generate'",
                (task,),
            ).fetchone()[0]
        with psycopg.connect(self.operator_dsn) as c:
            receipt = c.execute(
                "SELECT ops.accept_receipt(%s,%s,'known-result',%s,%s,%s,%s,%s)",
                (
                    task,
                    attempt,
                    "a" * 64,
                    "https://api.eu2.bfl.ai/v1/get_result?id=known-result",
                    uuid4(),
                    "synthetic supplier receipt only",
                    "b" * 64,
                ),
            ).fetchone()[0]
        return task, receipt

    def review(self, original_receipt, operation, **changes):
        args = {
            "identity": UUID(self.budget),
            "receipt": original_receipt,
            "scope_sha": self.digest,
            "operation": operation,
            "reason": "synthetic original ceiling review",
            "evidence": "b" * 64,
            **changes,
        }
        return review_blocked_provider_budget(self.operator_dsn, **args)

    def test_reviewed_receipt_resumes_same_generation_without_new_generation_post(self):
        task, receipt = self.uncertain_receipt()
        operation = uuid4()
        self.assertEqual(self.review(receipt, operation), self.budget)
        self.assertEqual(self.snapshot()["state"], "open")
        self.assertEqual(self.snapshot()["holds"][0]["outcome"], "uncertain")
        # The separate task reconciliation is still required; review alone cannot run it.
        self.assertEqual(self.run_protocol(task), "not_claimed")
        with psycopg.connect(self.operator_dsn) as c:
            c.execute(
                "SELECT ops.reconcile_task(%s,%s,'resume',%s,%s,%s)",
                (task, uuid4(), receipt, "synthetic known result", "b" * 64),
            )
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        self.assertEqual(self.review(receipt, operation), self.budget)
        value = self.snapshot()
        self.assertEqual(value["state"], "closed")
        self.assertEqual(Decimal(value["reserved_ceiling_total"]), Decimal("0.12"))
        self.assertFalse(value["actual_spending_verified"])
        with psycopg.connect(self.owner) as c:
            audits = c.execute(
                "SELECT actor,after_state FROM ops.audit_events WHERE request_id=%s", (operation,)
            ).fetchall()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0][0], self.operator)
        self.assertEqual(audits[0][1]["scope_sha256"], self.digest)
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "operation_conflict"):
            self.review(receipt, operation, reason="changed review")

    def test_review_rejects_changed_scope_missing_receipt_and_unbound_evidence(self):
        task, receipt = self.uncertain_receipt()
        for changes, expected in [
            ({"scope_sha": "f" * 64}, "provider_budget_scope_mismatch"),
            ({"receipt": uuid4()}, "receipt_required"),
            ({"evidence": "c" * 64}, "audited_receipt_required"),
            ({"reason": " "}, "invalid_budget_review"),
            ({"evidence": None}, "invalid_budget_review"),
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(psycopg.errors.RaiseException, expected),
            ):
                self.review(receipt, uuid4(), **changes)
        self.assertEqual(self.snapshot()["state"], "blocked")
        self.assertEqual(len(self.snapshot()["holds"]), 1)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))

    def test_concurrent_identical_review_has_one_audit_and_no_new_intent(self):
        task, receipt = self.uncertain_receipt()
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.review(receipt, operation), range(4)))
        self.assertEqual(outcomes, [self.budget] * 4)
        self.assertEqual(len(self.snapshot()["holds"]), 1)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )
        self.assertEqual(self.run_protocol(task), "not_claimed")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))

    def test_review_cannot_extend_expired_authorization(self):
        task, receipt = self.uncertain_receipt(lifetime=4)
        while datetime.now(timezone.utc) < self.scope.expires_at:
            time.sleep(0.05)
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "provider_budget_expired"):
            self.review(receipt, uuid4())
        self.assertEqual(self.snapshot()["state"], "blocked")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (84, 16))
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")

    def test_any_restoration_quarantine_prevents_reopening_original_authorization(self):
        task, receipt = self.uncertain_receipt()
        # Controlled audit fixture exercises the SQL guard; not a new restore rehearsal.
        with psycopg.connect(self.owner) as c:
            c.execute(
                "INSERT INTO ops.audit_events(actor,action,target_type,target_id,reason,evidence_digest,request_id) VALUES(current_user,'restore_quarantine','task',%s,'controlled restoration guard fixture',%s,%s)",
                (task, "c" * 64, uuid4()),
            )
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "restored_budget_cannot_reopen"):
            self.review(receipt, uuid4())
        self.assertEqual(self.snapshot()["state"], "blocked")

    def test_review_is_operator_only_and_rejects_queued_and_closed_tasks(self):
        task = self.submit(self.bind)
        query = "SELECT ops.review_blocked_provider_budget(%s,%s,%s,%s,%s,%s)"
        params = (self.scope.authorization_id, uuid4(), self.digest, uuid4(), "review", "b" * 64)
        for dsn in (self.worker_dsn, self.settings.api_database_url):
            with self.subTest(dsn_role="worker" if dsn == self.worker_dsn else "api"):
                with psycopg.connect(dsn) as c:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        c.execute(query, params)
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "budget_review_requires_uncertain_task"
        ):
            self.review(uuid4(), uuid4())
        self.assertEqual(self.run_protocol(task), "succeeded")
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "budget_review_requires_uncertain_task"
        ):
            self.review(uuid4(), uuid4())
        self.assertEqual(self.snapshot()["state"], "closed")

    def test_known_receipts_keep_two_exact_ceilings_distinct_from_actual_cost_and_credits(self):
        task = self.submit(self.bind)
        reviewed = provider_quote_review(self.operator_dsn, self.user, self.last_quote)
        self.assertEqual(reviewed["input_sha256"], self.scope.quote_input_sha256)
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual(self.run_protocol(task), "not_claimed")
        value = self.snapshot()
        self.assertEqual(value["state"], "closed")
        self.assertEqual(Decimal(value["reserved_ceiling_total"]), Decimal("0.12"))
        self.assertFalse(value["actual_spending_verified"])
        self.assertEqual(
            {(h["step"], h["outcome"]) for h in value["holds"]},
            {("generate", "known"), ("detect", "known")},
        )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (84, 0, 16)
        )

    def test_response_loss_blocks_authorization_and_never_resubmits(self):
        task = self.submit(self.bind)
        self.timeout_step = "generate"
        self.assertEqual(self.run_protocol(task), "stopped")
        self.assertEqual(self.run_protocol(task), "not_claimed")
        value = self.snapshot()
        self.assertEqual(value["state"], "blocked")
        self.assertEqual(len(value["holds"]), 1)
        self.assertEqual(value["holds"][0]["outcome"], "uncertain")
        self.assertEqual(Decimal(value["reserved_ceiling_total"]), Decimal("0.10"))
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (84, 16))

    def test_expired_scope_rolls_back_intent_and_hold_before_any_post_and_refunds_credits(self):
        task = self.submit(lambda quote, snapshot: self.bind(quote, snapshot, lifetime=3))
        while datetime.now(timezone.utc) < self.scope.expires_at:
            time.sleep(0.05)
        self.assertEqual(self.run_protocol(task), "stopped")
        value = self.snapshot()
        self.assertEqual(value["holds"], [])
        self.assertEqual(Decimal(value["reserved_ceiling_total"]), 0)
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (100, 0))

    def test_concurrent_intent_and_hold_have_one_winner(self):
        task = self.submit(self.bind)
        with psycopg.connect(self.worker_dsn) as c:
            claim = c.execute(
                "SELECT ops.claim_task(%s,'budget-worker','offline_protocol_mock')", (task,)
            ).fetchone()[0]

        def reserve(_):
            try:
                with psycopg.connect(self.worker_dsn) as c:
                    return c.execute(
                        "SELECT app.record_intent(%s,'budget-worker',%s,'generate',%s)",
                        (task, claim["fencing_token"], "a" * 64),
                    ).fetchone()[0]
            except psycopg.errors.ObjectNotInPrerequisiteState:
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            winners = list(pool.map(reserve, range(4)))
        self.assertEqual(sum(x is not None for x in winners), 1)
        value = self.snapshot()
        self.assertEqual(len(value["holds"]), 1)
        self.assertEqual(Decimal(value["reserved_ceiling_total"]), Decimal("0.10"))
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))

    def test_registration_is_idempotent_and_cannot_expand_reviewed_scope(self):
        task = self.submit(self.bind)
        # The quote is now consumed, but exact operation replay still returns the original record.
        self.assertEqual(
            register_provider_budget(
                self.operator_dsn,
                self.last_quote,
                self.scope,
                self.digest,
                self.operation,
                "synthetic offline budget",
            ),
            self.budget,
        )
        changed = SmokeAuthorization.model_validate_json(
            json.dumps({**self.scope.model_dump(mode="json"), "total_ceiling": "0.13"})
        )
        digest = hashlib.sha256(canonical_authorization(changed)).hexdigest()
        with self.assertRaises(psycopg.errors.RaiseException):
            register_provider_budget(
                self.operator_dsn,
                self.last_quote,
                changed,
                digest,
                self.operation,
                "synthetic offline budget",
            )
        self.assertEqual(self.snapshot()["scope_sha256"], self.digest)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "queued")

    def test_sql_itself_rejects_unreviewed_types_limits_and_secret_fields(self):
        def check(quote, snapshot):
            self.bind(quote, snapshot)
            for changed, expected in [
                ({"total_ceiling": "0.11"}, "invalid_provider_ceiling"),
                ({"generate_ceiling": 0.1}, "invalid_provider_scope"),
                ({"max_generate_calls": 2}, "invalid_provider_scope"),
                ({"max_generate_calls": "1"}, "invalid_provider_scope"),
                ({"max_detect_calls": True}, "invalid_provider_scope"),
                ({"api_key": "test"}, "invalid_provider_scope"),
            ]:
                identity = uuid4()
                raw = {
                    **self.scope.model_dump(mode="json"),
                    **changed,
                    "authorization_id": str(identity),
                }
                text = json.dumps(raw, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(text.encode()).hexdigest()
                with self.subTest(changed=changed), psycopg.connect(self.operator_dsn) as c:
                    with self.assertRaises(psycopg.errors.RaiseException) as rejected:
                        c.execute(
                            "SELECT ops.register_provider_budget(%s,%s,%s,%s,%s,'negative SQL fixture')",
                            (identity, quote, text, digest, uuid4()),
                        )
                    self.assertEqual(str(rejected.exception).splitlines()[0], expected)
            self.assertEqual(self.snapshot()["scope_sha256"], self.digest)

        self.submit(check)

    def test_wrong_quote_hash_and_unprivileged_calls_do_not_create_budget_records(self):
        for changed in [
            {"quote_input_sha256": "f" * 64},
            {"user_id": str(uuid4())},
            {"source_sha256": "f" * 64},
        ]:
            with self.subTest(changed=changed), self.assertRaises(psycopg.errors.RaiseException):
                self.submit(lambda quote, snapshot: self.bind(quote, snapshot, changed=changed))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.provider_budgets WHERE quote_id=%s",
                    (self.last_quote,),
                ).fetchone()[0],
                0,
            )
        for query, parameters in [
            (
                "SELECT ops.register_provider_budget(%s,%s,'{}',%s,%s,'forbidden')",
                (uuid4(), self.last_quote, "a" * 64, uuid4()),
            ),
            ("SELECT ops.reserve_provider_budget(%s,%s,'generate')", (uuid4(), uuid4())),
            ("SELECT * FROM ops.provider_budgets", ()),
        ]:
            with self.subTest(query=query), psycopg.connect(self.worker_dsn) as c:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute(query, parameters)
        with psycopg.connect(self.operator_dsn) as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    "UPDATE ops.provider_budgets SET total_ceiling=999 WHERE id=%s",
                    (self.scope.authorization_id,),
                )
