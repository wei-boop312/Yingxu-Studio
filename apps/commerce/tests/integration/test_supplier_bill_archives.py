"""Real PG/S3 fixture bill evidence; no actual provider charges or settlement writes."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from supplier_estimate_fixture import SupplierEstimateFixture

from visual_platform.domain.supplier_billing import SupplierBillReview
from visual_platform.services.backup import object_references
from visual_platform.services.provider_pricing import (
    archive_supplier_bill,
    supplier_bill_review,
    supplier_billing_context,
)
from visual_platform.storage.s3 import S3Storage


class SupplierBillArchiveTests(SupplierEstimateFixture):
    def document(self, **changes):
        changes.setdefault(
            "merchant_fingerprint", hashlib.sha256(str(self.user).encode()).hexdigest()
        )
        return super().document(**changes)

    def prepared(self):
        task, accounts = self.metered(detect=True)
        self.assertEqual(self.run_protocol(task, merchant_accounts=accounts), "succeeded")
        with psycopg.connect(self.owner) as c:
            attempt = c.execute(
                "SELECT id FROM app.provider_attempts WHERE task_id=%s AND step='detect'", (task,)
            ).fetchone()[0]
        context = supplier_billing_context(self.operator_dsn, UUID(task), attempt)
        bill = SupplierBillReview.model_validate_json(
            json.dumps(
                {
                    **context["scope"],
                    "schema_version": 1,
                    "amount": "0.001",
                    "evidence_kind": "offline_fixture",
                    "evidence_sha256": hashlib.sha256(self.evidence).hexdigest(),
                    "statement_fingerprint": hashlib.sha256(str(uuid4()).encode()).hexdigest(),
                    "line_fingerprint": hashlib.sha256(str(uuid4()).encode()).hexdigest(),
                }
            )
        )
        return task, bill

    def archive(self, bill, operation=None, storage_class=S3Storage, **kwargs):
        storage = storage_class(self.settings)
        try:
            return archive_supplier_bill(
                self.operator_dsn,
                storage,
                bill,
                self.evidence,
                operation or uuid4(),
                "synthetic bill evidence review",
                evidence_reviewed=True,
                **kwargs,
            )
        finally:
            storage.close()

    def count(self, task):
        with psycopg.connect(self.owner) as c:
            return c.execute(
                "SELECT count(*) FROM ops.supplier_bill_reviews WHERE task_id=%s", (task,)
            ).fetchone()[0]

    def test_immutable_evidence_archive_is_private_and_not_a_settlement_or_wallet_change(self):
        task, bill = self.prepared()
        balance = self.client.get("/api/v1/credits/balance").json()
        budget = self.snapshot()
        identity = self.archive(bill)
        result = supplier_bill_review(self.operator_dsn, UUID(identity))
        self.assertEqual(result["review_status"], "offline_fixture_matched")
        self.assertFalse(result["actual_spending_verified"])
        self.assertEqual(Decimal(result["amount"]), Decimal("0.001"))
        self.assertEqual(
            (self.snapshot(), self.client.get("/api/v1/credits/balance").json()), (budget, balance)
        )
        with psycopg.connect(self.owner) as c:
            pointer = c.execute(
                "SELECT evidence_object_key,evidence_storage_version_id,evidence_sha256 FROM ops.supplier_bill_reviews WHERE id=%s",
                (identity,),
            ).fetchone()
            references = [
                r
                for r in object_references(c, self.settings.s3_bucket)
                if r["kind"] == "supplier_bill_evidence" and r["id"] == identity
            ]
            self.assertEqual(len(references), 1)
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_status='confirmed'",
                    (task,),
                ).fetchone()[0],
                0,
            )
            for role in ("app_api", "app_worker"):
                self.assertFalse(
                    c.execute(
                        "SELECT has_table_privilege(%s,'ops.supplier_bill_reviews','SELECT')",
                        (role,),
                    ).fetchone()[0]
                )
        storage = S3Storage(self.settings)
        try:
            data, _, _ = storage.get(pointer[0], version=pointer[1])
            self.assertEqual(data, self.evidence)
            self.assertEqual(hashlib.sha256(data).hexdigest(), pointer[2])
        finally:
            storage.close()
        with psycopg.connect(self.owner) as c:
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("UPDATE ops.supplier_bill_reviews SET amount=0 WHERE id=%s", (identity,))

    def test_concurrent_same_operation_is_one_archive_and_audit(self):
        task, bill = self.prepared()
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.archive(bill, operation), range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.count(task), 1)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )

    def test_duplicate_attempt_and_reused_supplier_request_cannot_be_archived_again(self):
        task, bill = self.prepared()
        self.archive(bill)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.archive(bill)
        other_task, other_bill = self.prepared()
        self.assertEqual(bill.provider_request_id, other_bill.provider_request_id)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.archive(other_bill)
        self.assertEqual((self.count(task), self.count(other_task)), (1, 0))

    def test_storage_failure_genuine_statement_and_changed_sql_context_leave_no_review(self):
        class Broken(S3Storage):
            def put_immutable(self, *args):
                raise RuntimeError("controlled bill evidence storage failure")

        task, bill = self.prepared()
        with self.assertRaisesRegex(RuntimeError, "controlled"):
            self.archive(bill, storage_class=Broken)
        changed = SupplierBillReview.model_validate_json(
            json.dumps({**bill.model_dump(mode="json"), "evidence_kind": "reviewed_statement"})
        )
        with self.assertRaisesRegex(ValueError, "not_enabled"):
            self.archive(changed)
        from visual_platform.domain.supplier_billing import canonical_bill

        canonical = canonical_bill(bill).decode()
        context = supplier_billing_context(self.operator_dsn, UUID(task), bill.attempt_id)
        context["receipt_applied"] = False
        context_text = json.dumps(context, sort_keys=True, separators=(",", ":"))
        operation = uuid4()
        with psycopg.connect(self.operator_dsn) as c:
            with self.assertRaisesRegex(psycopg.errors.RaiseException, "bill_context_changed"):
                c.execute(
                    "SELECT ops.archive_supplier_bill(%s,%s,%s,%s,%s,%s,NULL,%s,%s)",
                    (
                        canonical,
                        hashlib.sha256(canonical.encode()).hexdigest(),
                        context_text,
                        hashlib.sha256(context_text.encode()).hexdigest(),
                        self.settings.s3_bucket,
                        "private/supplier-bill/" + str(operation) + "/" + bill.evidence_sha256,
                        operation,
                        "synthetic stale context",
                    ),
                )
        self.assertEqual(self.count(task), 0)

    def test_audit_failure_rolls_back_review_and_same_operation_can_retry_immutable_object(self):
        task, bill = self.prepared()
        operation = uuid4()
        hook = "bill_abort_" + uuid4().hex
        with psycopg.connect(self.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE FUNCTION ops.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='supplier_bill_fixture_archived' THEN RAISE EXCEPTION 'controlled_bill_audit_failure'; END IF; RETURN NEW; END $$"
                ).format(sql.Identifier(hook))
            )
            c.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON ops.audit_events FOR EACH ROW EXECUTE FUNCTION ops.{}()"
                ).format(sql.Identifier(hook), sql.Identifier(hook))
            )
        try:
            with self.assertRaisesRegex(
                psycopg.errors.RaiseException, "controlled_bill_audit_failure"
            ):
                self.archive(bill, operation)
            self.assertEqual(self.count(task), 0)
        finally:
            with psycopg.connect(self.owner) as c:
                c.execute(
                    sql.SQL("DROP TRIGGER {} ON ops.audit_events").format(sql.Identifier(hook))
                )
                c.execute(sql.SQL("DROP FUNCTION ops.{}()").format(sql.Identifier(hook)))
        self.assertTrue(self.archive(bill, operation))
        self.assertEqual(self.count(task), 1)
