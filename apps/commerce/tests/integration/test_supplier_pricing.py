"""Private price publication and immutable merchant binding using real PG/S3 only."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from supplier_pricing_fixture import SupplierPricingFixture

from visual_platform.domain.provider_pricing import canonical_price
from visual_platform.services.backup import object_references
from visual_platform.services.provider_pricing import (
    estimate_reviewed_price,
    publish_supplier_price,
    supplier_price_context,
)
from visual_platform.storage.s3 import S3Storage, StorageError


class SupplierPricingTests(SupplierPricingFixture):
    def test_publish_exact_archive_and_estimate_without_confirming_actual_cost(self):
        price, identity = self.publish()
        context = supplier_price_context(self.operator_dsn, UUID(identity))
        self.assertEqual(
            context["document_sha256"], hashlib.sha256(canonical_price(price)).hexdigest()
        )
        self.assertFalse(context["actual_spending_verified"])
        self.assertNotIn("evidence_object_key", context)
        storage = S3Storage(self.settings)
        try:
            with psycopg.connect(self.operator_dsn) as c:
                key, version = c.execute(
                    "SELECT evidence_object_key,evidence_storage_version_id FROM ops.supplier_price_versions WHERE id=%s",
                    (identity,),
                ).fetchone()
            data, _, _ = storage.get(key, version=version)
            self.assertEqual(data, self.evidence)
            estimate = estimate_reviewed_price(
                self.operator_dsn,
                UUID(identity),
                {"bfl_credit": "1.25"},
                provider=price.provider,
                model=price.model,
                endpoint_origin=price.endpoint_origin,
                merchant_fingerprint=price.merchant_fingerprint,
                billing_group=price.billing_group,
                observed_at=datetime.now(timezone.utc),
            )
            self.assertEqual(
                (estimate["cost_status"], estimate["amount"]), ("estimated", "0.06250000")
            )
            self.assertFalse(estimate["actual_spending_verified"])
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM app.task_cost_records WHERE price_version=%s",
                        (price.version,),
                    ).fetchone()[0],
                    0,
                )
                refs = object_references(c, storage.bucket)
                self.assertEqual(
                    len(
                        [
                            r
                            for r in refs
                            if r["kind"] == "supplier_price_evidence" and r["id"] == identity
                        ]
                    ),
                    1,
                )
        finally:
            storage.close()

    def test_concurrent_publication_exact_replay_and_conflict_preserve_one_version(self):
        price = self.document()
        operation = uuid4()
        with ThreadPoolExecutor(max_workers=4) as pool:
            identities = list(pool.map(lambda _: self.publish(price, operation)[1], range(4)))
        self.assertEqual(len(set(identities)), 1)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.audit_events WHERE request_id=%s", (operation,)
                ).fetchone()[0],
                1,
            )
            with self.assertRaisesRegex(
                psycopg.errors.ObjectNotInPrerequisiteState, "immutable_record"
            ):
                c.execute(
                    "UPDATE ops.supplier_price_versions SET version='override' WHERE id=%s",
                    (identities[0],),
                )
        changed = price.model_copy(update={"currency": "CNY"})
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "operation_conflict"):
            self.publish(changed, operation)

    def test_review_attestation_hash_and_storage_fail_before_publication(self):
        price = self.document()
        storage = S3Storage(self.settings)
        try:
            with patch.object(
                storage, "put_immutable", side_effect=StorageError("offline archive outage")
            ) as upload:
                with self.assertRaisesRegex(ValueError, "explicit_price_evidence_review_required"):
                    publish_supplier_price(
                        self.operator_dsn, storage, price, self.evidence, uuid4(), "test"
                    )
                with self.assertRaisesRegex(ValueError, "price_evidence_mismatch"):
                    publish_supplier_price(
                        self.operator_dsn,
                        storage,
                        price,
                        b"wrong",
                        uuid4(),
                        "test",
                        evidence_reviewed=True,
                    )
                self.assertEqual(upload.call_count, 0)
                with self.assertRaises(StorageError):
                    publish_supplier_price(
                        self.operator_dsn,
                        storage,
                        price,
                        self.evidence,
                        uuid4(),
                        "test",
                        evidence_reviewed=True,
                    )
            with psycopg.connect(self.owner) as c:
                self.assertEqual(
                    c.execute(
                        "SELECT count(*) FROM ops.supplier_price_versions WHERE version=%s",
                        (price.version,),
                    ).fetchone()[0],
                    0,
                )
        finally:
            storage.close()

    def test_sql_rejects_invalid_rate_types_scope_extra_fields_and_false_coverage(self):
        base = self.document().model_dump(mode="json")
        for change in [
            {"schema_version": True},
            {"schema_version": 1.0},
            {"complete_meter_coverage": 1},
            {"complete_meter_coverage": False},
            {"api_key": "never-store"},
            {"endpoint_origin": "https://other.invalid"},
            {"rates": [{"measure": "bfl_credit", "amount": 0.1, "per": 1}]},
            {"rates": [{"measure": "bfl_credit", "amount": "0.1", "per": True}]},
            {"rates": [{"measure": "bfl_credit", "amount": "1e3", "per": 1}]},
            {"rates": [{"measure": "bfl_credit", "amount": "1", "per": 1}] * 2},
        ]:
            d = {**copy.deepcopy(base), **change}
            raw = json.dumps(d, sort_keys=True, separators=(",", ":"))
            with self.subTest(change=change), psycopg.connect(self.operator_dsn) as c:
                with self.assertRaises(psycopg.Error):
                    c.execute(
                        "SELECT ops.publish_supplier_price(%s,%s,%s,%s,NULL,%s,%s)",
                        (
                            raw,
                            hashlib.sha256(raw.encode()).hexdigest(),
                            "private",
                            "unavailable",
                            uuid4(),
                            "SQL guard",
                        ),
                    )

    def test_price_is_ops_only_and_owner_service_is_rejected(self):
        _, identity = self.publish()
        for dsn in (self.settings.api_database_url, self.worker_dsn):
            for query, args in [
                ("SELECT * FROM ops.supplier_price_versions", ()),
                ("SELECT ops.supplier_price_context(%s)", (identity,)),
            ]:
                with psycopg.connect(dsn) as c:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        c.execute(query, args)
        with self.assertRaises(ValueError):
            supplier_price_context(self.owner, UUID(identity))

    def test_frozen_price_and_account_scope_delivers_once_with_matching_runtime(self):
        price, identity = self.publish()
        execution = self.execution(price, identity)
        task = self.submit(self.bind, execution_updates=execution)
        self.assertEqual(
            self.run_protocol(task, merchant_accounts=execution["provider_accounts"]), "succeeded"
        )
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 1))
        with psycopg.connect(self.owner) as c:
            snapshot = c.execute(
                "SELECT input_snapshot FROM app.generation_tasks WHERE id=%s", (task,)
            ).fetchone()[0]
            self.assertEqual(
                snapshot["_execution"]["provider_price_versions"],
                execution["provider_price_versions"],
            )
        self.assertFalse(self.snapshot()["actual_spending_verified"])

    def test_wrong_runtime_account_rejects_before_paid_intent_or_post(self):
        price, identity = self.publish()
        execution = self.execution(price, identity)
        task = self.submit(self.bind, execution_updates=execution)
        self.assertEqual(self.run_protocol(task), "stopped")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["available"], 100)

    def test_wrong_account_endpoint_price_sha_and_expired_version_cannot_quote(self):
        price, identity = self.publish()
        base = self.execution(price, identity)
        for kind in ["merchant", "endpoint", "sha"]:
            changed = copy.deepcopy(base)
            if kind == "merchant":
                changed["provider_accounts"]["generate"]["merchant_fingerprint"] = "b" * 64
            elif kind == "endpoint":
                changed["provider_endpoints"]["generate"] = "https://other.invalid/v1/flux-3-image"
            else:
                changed["provider_price_versions"]["generate"]["document_sha256"] = "b" * 64
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(psycopg.errors.RaiseException, "price_scope_mismatch"),
            ):
                self.submit(execution_updates=changed)
        now = datetime.now(timezone.utc)
        expired = self.document(
            valid_from=(now - timedelta(hours=2)).isoformat(),
            valid_until=(now - timedelta(hours=1)).isoformat(),
        )
        expired, identity = self.publish(expired)
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "price_scope_mismatch"):
            self.submit(execution_updates=self.execution(expired, identity))

        numeric_price, numeric_id = self.publish(
            self.document(merchant_fingerprint="1" * 64, billing_group="123")
        )
        numeric_scope = self.execution(numeric_price, numeric_id)
        numeric_scope["provider_accounts"]["generate"] = {
            "merchant_fingerprint": int("1" * 64),
            "billing_group": 123,
        }
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "price_scope_mismatch"):
            self.submit(execution_updates=numeric_scope)
