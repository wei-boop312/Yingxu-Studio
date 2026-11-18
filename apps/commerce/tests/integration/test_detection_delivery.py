"""Real PG/Worker/S3 reviewed local delivery; all supplier HTTP remains mocked."""

import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import psycopg
from provider_budget_fixture import ProviderBudgetFixture
from psycopg import sql
from psycopg.types.json import Jsonb

from visual_platform.providers.contract import ProviderFailure
from visual_platform.providers.pipeline import ProtocolProvider
from visual_platform.services.detection_delivery import (
    approve_detection_delivery,
    detection_delivery_context,
    export_detection_review,
)
from visual_platform.storage.s3 import S3Storage, StorageError
from visual_platform.worker.runner import Worker


class DetectionDeliveryTests(ProviderBudgetFixture):
    def unresolved(self, *, step="detect"):
        task = self.submit(self.bind)
        self.timeout_step = step
        self.assertEqual(self.run_protocol(task), "stopped")
        self.timeout_step = None
        context = detection_delivery_context(self.operator_dsn, UUID(task))
        return task, context

    def approve(self, task, context, operation=None, **changes):
        parameters = {
            "task": UUID(task),
            "attempt": UUID(context["detect_attempt"]["id"]),
            "final_sha256": context["final"]["sha256"],
            "operation": operation or uuid4(),
            "reason": "synthetic final-image local delivery decision, not supplier billing proof",
            "evidence": "d" * 64,
            **changes,
        }
        return approve_detection_delivery(self.operator_dsn, **parameters)

    def assert_delivery_invariants(self, task, context):
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        report = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        self.assertEqual(report["auto_overall"], "manual_review")
        self.assertFalse(report["facts"]["analysis_complete"])
        self.assertTrue(report["detection_degraded"])
        self.assertTrue(
            self.client.get("/api/v1/tasks/" + task).json()["compliance"]["detection_degraded"]
        )
        self.assertIn("detection_unavailable_operator_delivery", report["facts"]["uncertainties"])
        download = self.client.get("/api/v1/tasks/" + task + "/download", follow_redirects=False)
        self.assertEqual(download.status_code, 302)
        final = httpx.get(download.headers["location"], trust_env=False).content
        self.assertEqual(hashlib.sha256(final).hexdigest(), context["final"]["sha256"])
        self.assertEqual(report["final_sha256"], context["final"]["sha256"])
        with psycopg.connect(self.owner) as c:
            supplier = c.execute(
                "SELECT status,provider_id,response_digest FROM app.provider_attempts WHERE id=%s",
                (context["detect_attempt"]["id"],),
            ).fetchone()
            self.assertEqual(supplier, ("uncertain", None, None))
            costs = c.execute(
                "SELECT cost_record_kind,cost_status,amount,currency FROM app.task_cost_records WHERE attempt_id=%s",
                (context["detect_attempt"]["id"],),
            ).fetchall()
            self.assertEqual(costs, [("intent_observation", "unknown", None, None)])
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='refund'",
                    (task,),
                ).fetchone()[0],
                0,
            )
        budget = self.snapshot()
        self.assertEqual(budget["state"], "closed")
        self.assertFalse(budget["actual_spending_verified"])
        self.assertIn(("detect", "uncertain"), {(h["step"], h["outcome"]) for h in budget["holds"]})
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(
            (balance["available"], balance["reserved"], balance["spent_total"]), (84, 0, 16)
        )

    def test_review_delivers_exact_final_without_fabricating_supplier_result_or_zero_cost(self):
        task, context = self.unresolved()
        operation = uuid4()
        decision = self.approve(task, context, operation)
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assert_delivery_invariants(task, context)
        before = self.client.get("/api/v1/tasks/" + task).json()["revision"]
        self.assertEqual(self.approve(task, context, operation), decision)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["revision"], before)
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "operation_conflict"):
            self.approve(task, context, operation, reason="changed review")
        with psycopg.connect(self.owner) as c:
            audit = c.execute(
                "SELECT actor,after_state FROM ops.audit_events WHERE request_id=%s", (operation,)
            ).fetchone()
            self.assertEqual(audit[0], self.operator)
            self.assertFalse(audit[1]["supplier_cost_verified"])

    def test_wrong_scope_evidence_and_review_cannot_change_frozen_task(self):
        task, context = self.unresolved()
        for changes, expected in [
            ({"final_sha256": "f" * 64}, "final_hash_mismatch"),
            ({"attempt": uuid4()}, "unresolved_detection_required"),
            ({"reason": " "}, "invalid_detection_delivery_review"),
            ({"evidence": None}, "invalid_detection_delivery_review"),
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(psycopg.errors.RaiseException, expected),
            ):
                self.approve(task, context, **changes)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")
        self.assertIsNone(detection_delivery_context(self.operator_dsn, UUID(task))["decision"])
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_model_supplied_uncertainty_text_cannot_impersonate_operator_approval(self):
        original_route = self.gemini_route

        def untrusted_facts(request):
            response = original_route(request)
            body = response.json()
            facts = json.loads(body["candidates"][0]["content"]["parts"][0]["text"])
            facts["uncertainties"] = ["detection_unavailable_operator_delivery"]
            body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(facts)
            return httpx.Response(200, json=body)

        self.gemini_route = untrusted_facts
        task = self.submit(self.bind)
        self.assertEqual(self.run_protocol(task), "succeeded")
        report = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        self.assertIn("detection_unavailable_operator_delivery", report["facts"]["uncertainties"])
        self.assertFalse(report["detection_degraded"])
        self.assertFalse(
            self.client.get("/api/v1/tasks/" + task).json()["compliance"]["detection_degraded"]
        )
        self.assertIsNone(detection_delivery_context(self.operator_dsn, UUID(task))["decision"])

    def test_public_delivery_flag_is_tenant_scoped_without_exposing_operator_table(self):
        task, context = self.unresolved()
        self.approve(task, context)
        self.assertEqual(self.run_protocol(task), "succeeded")
        check = self.client.get("/api/v1/tasks/" + task + "/compliance").json()["id"]
        with psycopg.connect(self.settings.api_database_url) as c:
            self.assertFalse(
                c.execute("SELECT app.is_manual_detection_delivery(%s)", (check,)).fetchone()[0]
            )
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
            self.assertTrue(
                c.execute("SELECT app.is_manual_detection_delivery(%s)", (check,)).fetchone()[0]
            )
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(uuid4()),))
            self.assertFalse(
                c.execute("SELECT app.is_manual_detection_delivery(%s)", (check,)).fetchone()[0]
            )

    def test_unknown_generation_is_never_authorized_as_local_detection_delivery(self):
        task, context = self.unresolved(step="generate")
        self.assertIsNone(context["final"])
        with psycopg.connect(self.owner) as c:
            attempt = c.execute(
                "SELECT id FROM app.provider_attempts WHERE task_id=%s", (task,)
            ).fetchone()[0]
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "detection_delivery_requires_unresolved_final"
        ):
            approve_detection_delivery(
                self.operator_dsn,
                UUID(task),
                attempt,
                "f" * 64,
                uuid4(),
                "negative generation fixture",
                "d" * 64,
            )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")

    def test_explicit_detector_failure_awaits_review_and_retains_failed_supplier_status(self):
        task = self.submit(self.bind)
        # Controlled provider rejection exercises the definite-failure state branch;
        # no claim that this injected exception is a real supplier response.
        with patch.object(
            ProtocolProvider,
            "submit_detect",
            side_effect=ProviderFailure("controlled detector rejection"),
        ) as rejection:
            self.assertEqual(self.run_protocol(task), "stopped")
            self.assertEqual(rejection.await_count, 1)
        context = detection_delivery_context(self.operator_dsn, UUID(task))
        self.assertEqual(context["task"]["status"], "uncertain")
        self.assertEqual(context["detect_attempt"]["status"], "failed")
        self.approve(task, context)
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        self.assertEqual(
            self.client.get("/api/v1/tasks/" + task + "/compliance").json()["auto_overall"],
            "manual_review",
        )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT status FROM app.provider_attempts WHERE id=%s",
                    (context["detect_attempt"]["id"],),
                ).fetchone()[0],
                "failed",
            )
        self.assertIn(
            ("detect", "failed"), {(h["step"], h["outcome"]) for h in self.snapshot()["holds"]}
        )

    def test_terminal_and_queued_tasks_cannot_be_reopened_by_a_new_decision(self):
        task = self.submit(self.bind)
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "detection_delivery_requires_unresolved_final"
        ):
            approve_detection_delivery(
                self.operator_dsn,
                UUID(task),
                uuid4(),
                "f" * 64,
                uuid4(),
                "queued fixture",
                "d" * 64,
            )
        self.assertEqual(self.run_protocol(task), "succeeded")
        context = detection_delivery_context(self.operator_dsn, UUID(task))
        with self.assertRaisesRegex(
            psycopg.errors.RaiseException, "detection_delivery_requires_unresolved_final"
        ):
            self.approve(task, context)
        self.assertIsNone(detection_delivery_context(self.operator_dsn, UUID(task))["decision"])

    def test_review_is_operator_only_and_immutable(self):
        task, context = self.unresolved()
        query = "SELECT ops.approve_detection_manual_delivery(%s,%s,%s,%s,%s,%s)"
        args = (
            task,
            context["detect_attempt"]["id"],
            context["final"]["sha256"],
            uuid4(),
            "review",
            "d" * 64,
        )
        for dsn in (self.worker_dsn, self.settings.api_database_url):
            for statement, parameters in [
                (query, args),
                ("SELECT ops.detection_delivery_context(%s)", (task,)),
                ("SELECT * FROM ops.detection_manual_deliveries", ()),
            ]:
                with psycopg.connect(dsn) as c:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        c.execute(statement, parameters)
        with self.assertRaises(ValueError):
            approve_detection_delivery(
                self.owner,
                UUID(task),
                UUID(context["detect_attempt"]["id"]),
                context["final"]["sha256"],
                uuid4(),
                "owner rejected",
                "d" * 64,
            )
        decision = self.approve(task, context)
        with psycopg.connect(self.operator_dsn) as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    "UPDATE ops.detection_manual_deliveries SET reason='override' WHERE id=%s",
                    (decision,),
                )
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute(
                    "UPDATE ops.detection_manual_deliveries SET reason='override' WHERE id=%s",
                    (decision,),
                )

    def test_concurrent_review_creates_one_decision_audit_and_recovery_message(self):
        task, context = self.unresolved()
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=4) as pool:
            decisions = list(pool.map(lambda _: self.approve(task, context, operation), range(4)))
        self.assertEqual(len(set(decisions)), 1)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.detection_manual_deliveries WHERE task_id=%s", (task,)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.outbox WHERE task_id=%s AND dedupe_key=%s",
                    (task, task + "-manual-detection-" + str(operation)),
                ).fetchone()[0],
                1,
            )
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual(self.run_protocol(task), "not_claimed")
        self.assert_delivery_invariants(task, context)

    def test_outbox_write_failure_rolls_back_review_state_and_audit_atomically(self):
        task, context = self.unresolved()
        operation = uuid4()
        hook = "reject_delivery_" + uuid4().hex
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $body$ BEGIN IF NEW.task_id={} AND NEW.event_type='task.recovery' THEN RAISE EXCEPTION 'controlled_manual_outbox_failure'; END IF; RETURN NEW; END $body$"
                ).format(sql.Identifier(hook), sql.Literal(UUID(task)))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.outbox FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(hook), sql.Identifier(hook))
            )
        try:
            with self.assertRaisesRegex(
                psycopg.errors.RaiseException, "controlled_manual_outbox_failure"
            ):
                self.approve(task, context, operation)
            reviewed = detection_delivery_context(self.operator_dsn, UUID(task))
            self.assertIsNone(reviewed["decision"])
            self.assertIsNone(reviewed["latest_review"])
            self.assertEqual(reviewed["task"]["status"], "uncertain")
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                    ).fetchone()[0],
                    0,
                )
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(sql.SQL("DROP TRIGGER {} ON ops.outbox").format(sql.Identifier(hook)))
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(hook)))
        self.approve(task, context, operation)
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assert_delivery_invariants(task, context)

    def test_forged_complete_or_different_facts_are_rejected_before_report_write(self):
        task, context = self.unresolved()
        decision = self.approve(task, context)
        with psycopg.connect(self.worker_dsn) as c:
            claim = c.execute(
                "SELECT ops.claim_task(%s,'guard-worker','offline_protocol_mock')", (task,)
            ).fetchone()[0]
        with psycopg.connect(self.owner) as c:
            facts = c.execute(
                "SELECT facts FROM ops.detection_manual_deliveries WHERE id=%s", (decision,)
            ).fetchone()[0]
        for altered, overall in [
            ({**facts, "analysis_complete": True}, "manual_review"),
            (facts, "compliant"),
            ({**facts, "image_width": 257}, "manual_review"),
        ]:
            with psycopg.connect(self.worker_dsn) as c:
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "invalid_manual_detection_report"
                ):
                    c.execute(
                        "SELECT app.record_report(%s,'guard-worker',%s,%s,%s,%s,%s,%s,false,%s,'private','rejected-report',NULL)",
                        (
                            task,
                            claim["fencing_token"],
                            uuid4(),
                            context["final"]["sha256"],
                            Jsonb(altered),
                            Jsonb([]),
                            overall,
                            "e" * 64,
                        ),
                    )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.compliance_checks WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )

    def test_report_storage_interruption_resumes_local_phase_without_provider_replay(self):
        task, context = self.unresolved()
        self.approve(task, context)

        class ReportUnavailable(S3Storage):
            def put_immutable(self, key, data, content_type):
                if content_type == "application/json":
                    raise StorageError("controlled report archive failure")
                return super().put_immutable(key, data, content_type)

        self.assertEqual(self.run_protocol(task, storage_class=ReportUnavailable), "retryable")
        self.assertEqual(
            self.client.get("/api/v1/tasks/" + task).json()["phase"], "validating_rules"
        )
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assert_delivery_invariants(task, context)

    def test_after_report_before_settlement_restart_retains_unknown_detector_and_one_charge(self):
        task, context = self.unresolved()
        self.approve(task, context)

        class SettlementInterrupted(Worker):
            async def db(self, query, *args):
                if query.startswith("SELECT app.settle_task"):
                    raise OSError("controlled pre-settlement interruption")
                return await super().db(query, *args)

        self.assertEqual(self.run_protocol(task, worker_class=SettlementInterrupted), "retryable")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["phase"], "finalizing")
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assert_delivery_invariants(task, context)

    def test_recorded_detection_intent_gray_zone_never_resubmits_and_can_be_reviewed(self):
        task = self.submit(self.bind)

        class IntentInterrupted(Worker):
            async def cost(self, task, fence, attempt_id, stage):
                await super().cost(task, fence, attempt_id, stage)
                if stage == "intent" and (await self.context(task, fence))["phase"] == "detecting":
                    raise OSError("controlled stop after detection intent and before network")

        self.assertEqual(self.run_protocol(task, worker_class=IntentInterrupted), "retryable")
        self.assertEqual(self.run_protocol(task), "uncertain")
        context = detection_delivery_context(self.operator_dsn, UUID(task))
        self.assertEqual(context["detect_attempt"]["status"], "intent_recorded")
        self.approve(task, context)
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT status,provider_id FROM app.provider_attempts WHERE id=%s",
                    (context["detect_attempt"]["id"],),
                ).fetchone(),
                ("intent_recorded", None),
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (task,),
                ).fetchone()[0],
                1,
            )

    def test_quarantined_existing_decision_needs_fresh_review_without_overwriting_it(self):
        task, context = self.unresolved()
        original_operation = uuid4()
        decision = self.approve(task, context, original_operation)

        class SettlementInterrupted(Worker):
            async def db(self, query, *args):
                if query.startswith("SELECT app.settle_task"):
                    raise OSError("controlled stop before settlement")
                return await super().db(query, *args)

        self.assertEqual(self.run_protocol(task, worker_class=SettlementInterrupted), "retryable")
        with psycopg.connect(self.owner) as c:
            original = c.execute(
                "SELECT to_jsonb(d) FROM ops.detection_manual_deliveries d WHERE id=%s", (decision,)
            ).fetchone()[0]
            # Controlled restoration-state fixture; the independent restore rehearsal
            # remains a separate gate and is not claimed by this branch test.
            c.execute(
                "UPDATE app.generation_tasks SET status='uncertain',lease_owner=NULL,lease_until=NULL,fencing_token=fencing_token+1,error_code='restore_reconciliation_required' WHERE id=%s",
                (task,),
            )
            c.execute(
                "UPDATE ops.task_dispatch SET state='manual',lease_until=NULL WHERE task_id=%s",
                (task,),
            )
        self.assertEqual(self.approve(task, context, original_operation), decision)
        self.assertEqual(self.run_protocol(task), "not_claimed")
        fresh_operation = uuid4()
        self.assertEqual(
            self.approve(
                task,
                context,
                fresh_operation,
                reason="new controlled post-quarantine review",
                evidence="e" * 64,
            ),
            decision,
        )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT to_jsonb(d) FROM ops.detection_manual_deliveries d WHERE id=%s",
                    (decision,),
                ).fetchone()[0],
                original,
            )
        reviewed = detection_delivery_context(self.operator_dsn, UUID(task))
        self.assertEqual(reviewed["latest_review"]["operation_id"], str(fresh_operation))
        self.assertEqual(reviewed["task"]["phase"], "finalizing")
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assert_delivery_invariants(task, context)

    def test_failure_decision_racing_delivery_approval_has_one_terminal_credit_outcome(self):
        task, context = self.unresolved()
        barrier = Barrier(2)

        def approve():
            barrier.wait(timeout=10)
            try:
                self.approve(task, context)
                return "delivery"
            except psycopg.errors.RaiseException:
                return "lost"

        def fail():
            barrier.wait(timeout=10)
            try:
                with psycopg.connect(self.operator_dsn) as c:
                    c.execute(
                        "SELECT ops.reconcile_task(%s,%s,'fail',NULL,%s,%s)",
                        (task, uuid4(), "synthetic unable-to-deliver decision", "f" * 64),
                    )
                return "failed"
            except psycopg.errors.RaiseException:
                return "lost"

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(approve), pool.submit(fail)
            outcomes = [a.result(), b.result()]
        self.assertEqual(outcomes.count("lost"), 1)
        if "delivery" in outcomes:
            self.assertEqual(self.run_protocol(task), "succeeded")
            self.assert_delivery_invariants(task, context)
        else:
            self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "failed")
            self.assertEqual(self.run_protocol(task), "not_claimed")
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type IN ('charge','refund')",
                    (task,),
                ).fetchone()[0],
                1,
            )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))

    def test_late_supplier_receipt_after_manual_delivery_does_not_reopen_or_charge_again(self):
        task, context = self.unresolved()
        self.approve(task, context)
        self.assertEqual(self.run_protocol(task), "succeeded")
        with psycopg.connect(self.operator_dsn) as c:
            receipt = c.execute(
                "SELECT ops.accept_receipt(%s,%s,'late-detect-proof',%s,NULL,%s,%s,%s)",
                (
                    task,
                    context["detect_attempt"]["id"],
                    "e" * 64,
                    uuid4(),
                    "synthetic late supplier proof",
                    "f" * 64,
                ),
            ).fetchone()[0]
        with psycopg.connect(self.operator_dsn) as c:
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "task_not_uncertain"):
                c.execute(
                    "SELECT ops.reconcile_task(%s,%s,'resume',%s,%s,%s)",
                    (task, uuid4(), receipt, "cannot reopen delivery", "f" * 64),
                )
        self.assert_delivery_invariants(task, context)

    def test_operator_exports_exact_final_before_approval_without_changing_wallet_or_task(self):
        task, context = self.unresolved()
        storage = S3Storage(self.settings)
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "review.png"
                exported = export_detection_review(
                    self.operator_dsn, storage, UUID(task), output, Path(folder)
                )
                self.assertEqual(
                    hashlib.sha256(output.read_bytes()).hexdigest(), context["final"]["sha256"]
                )
                self.assertEqual(exported["final_sha256"], context["final"]["sha256"])
                self.assertFalse(exported["delivery_approved"])
                self.assertNotIn("object_key", exported)
                self.assertNotIn("bucket", exported)
                with self.assertRaisesRegex(ValueError, "private_review_output_required"):
                    export_detection_review(
                        self.operator_dsn, storage, UUID(task), output, Path(folder)
                    )
                with self.assertRaisesRegex(ValueError, "private_review_output_required"):
                    export_detection_review(
                        self.operator_dsn,
                        storage,
                        UUID(task),
                        Path(folder).parent / "outside.png",
                        Path(folder),
                    )
            self.assertIsNone(detection_delivery_context(self.operator_dsn, UUID(task))["decision"])
            self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")
            balance = self.client.get("/api/v1/credits/balance").json()
            self.assertEqual(
                (balance["available"], balance["reserved"], balance["spent_total"]), (84, 16, 0)
            )
            self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        finally:
            storage.close()

    def test_review_export_rejects_corrupt_storage_and_unprivileged_readers(self):
        task, _ = self.unresolved()
        storage = S3Storage(self.settings)
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "review.png"
                with patch.object(
                    storage, "get", return_value=(b"corrupt", "image/png", "wrong-version")
                ):
                    with self.assertRaisesRegex(ValueError, "review_artifact_mismatch"):
                        export_detection_review(
                            self.operator_dsn, storage, UUID(task), output, Path(folder)
                        )
                self.assertFalse(output.exists())
                for dsn in (self.settings.api_database_url, self.worker_dsn):
                    with psycopg.connect(dsn) as c:
                        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                            c.execute("SELECT ops.detection_review_artifact(%s)", (task,))
        finally:
            storage.close()

    def test_review_export_rejects_unknown_generation_without_writing_a_file(self):
        task, _ = self.unresolved(step="generate")
        storage = S3Storage(self.settings)
        try:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "review.png"
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "detection_review_artifact_unavailable"
                ):
                    export_detection_review(
                        self.operator_dsn, storage, UUID(task), output, Path(folder)
                    )
                self.assertFalse(output.exists())
        finally:
            storage.close()
