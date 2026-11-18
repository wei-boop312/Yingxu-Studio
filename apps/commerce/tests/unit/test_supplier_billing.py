"""Pure bill matching cannot confirm spending or turn local IDs/nominal units into money."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from visual_platform.domain.supplier_billing import (
    SupplierBillReview,
    canonical_bill,
    load_bill_review,
    review_supplier_bill,
)


class SupplierBillTests(unittest.TestCase):
    def setUp(self):
        self.evidence = b"Synthetic bill line, no actual charges."
        self.scope = {
            "task_id": str(uuid4()),
            "attempt_id": str(uuid4()),
            "quote_input_sha256": "a" * 64,
            "provider": "gemini",
            "model": "gemini-3.8-flash",
            "endpoint_origin": "https://hubway.cc",
            "merchant_fingerprint": "b" * 64,
            "billing_group": "synthetic-only",
            "provider_request_id": "synthetic-upstream-response",
            "response_digest": "c" * 64,
            "currency": "HUBWAY_BALANCE_UNIT",
        }
        self.document = {
            **self.scope,
            "schema_version": 1,
            "amount": "0.00003",
            "evidence_kind": "offline_fixture",
            "evidence_sha256": hashlib.sha256(self.evidence).hexdigest(),
            "statement_fingerprint": "d" * 64,
            "line_fingerprint": "e" * 64,
        }
        self.context = {
            "scope": self.scope,
            "profile": "offline_protocol_mock",
            "receipt_applied": True,
            "price_scope_verified": True,
            "step_ceiling": "0.02",
            "estimate": {
                "cost_status": "estimated",
                "currency": "HUBWAY_BALANCE_UNIT",
                "amount": "0.00002720",
            },
        }

    def review(self, document=None, context=None, **kwargs):
        model = SupplierBillReview.model_validate_json(json.dumps(document or self.document))
        return review_supplier_bill(
            model, context or self.context, self.evidence, evidence_reviewed=True, **kwargs
        )

    def test_matched_fixture_has_exact_delta_but_cannot_confirm_archive_or_move_money(self):
        result = self.review()
        self.assertEqual(result["same_unit_estimate_delta"], "0.00000280")
        self.assertEqual(result["currency"], "HUBWAY_BALANCE_UNIT")
        for key in (
            "actual_spending_verified",
            "confirmation_enabled",
            "evidence_archived",
            "budget_reopened",
            "user_credits_changed",
        ):
            self.assertFalse(result[key])
        self.assertEqual(result["total_ceiling_relation"], "not_evaluated")

    def test_every_frozen_scope_field_must_match_exactly(self):
        for field in self.scope:
            changed = copy.deepcopy(self.context)
            changed["scope"][field] = "different"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "scope_mismatch"):
                self.review(context=changed)

    def test_local_gemini_archive_id_is_never_a_supplier_bill_id(self):
        local = "gemini-" + self.scope["attempt_id"]
        doc = {**self.document, "provider_request_id": local}
        ctx = copy.deepcopy(self.context)
        ctx["scope"]["provider_request_id"] = local
        with self.assertRaisesRegex(ValueError, "local_gemini_id"):
            self.review(doc, ctx)

    def test_unknown_or_different_unit_estimate_has_no_invented_delta(self):
        for estimate in (
            None,
            {"cost_status": "unknown", "amount": None, "currency": None},
            {"cost_status": "estimated", "amount": "0", "currency": "USD"},
        ):
            self.assertIsNone(
                self.review(context={**self.context, "estimate": estimate})[
                    "same_unit_estimate_delta"
                ]
            )
        result = self.review({**self.document, "amount": "1"})
        self.assertEqual(result["step_ceiling_relation"], "over_ceiling")
        self.assertFalse(result["budget_reopened"])

    def test_genuine_statement_or_non_mock_namespace_cannot_enable_confirmation(self):
        with self.assertRaisesRegex(ValueError, "not_enabled"):
            self.review({**self.document, "evidence_kind": "reviewed_statement"})
        with self.assertRaisesRegex(ValueError, "not_enabled"):
            self.review(context={**self.context, "profile": "live"})
        for field in ("receipt_applied", "price_scope_verified"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "requires_applied"):
                self.review(context={**self.context, field: False})

    def test_amount_types_identity_and_evidence_are_strict(self):
        for field, value in (
            ("amount", True),
            ("amount", 0.1),
            ("amount", "-1"),
            ("amount", "1e2"),
            ("amount", "0.000000001"),
            ("amount", "1000000000000"),
            ("schema_version", True),
            ("model", "other"),
            ("endpoint_origin", "https://hubway.cc/untrusted"),
            ("api_key", "offline-secret-fixture"),
            ("provider_request_id", "\n"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.review({**self.document, field: value})
        with self.assertRaisesRegex(ValueError, "evidence_mismatch"):
            self.review({**self.document, "evidence_sha256": "0" * 64})
        model = SupplierBillReview.model_validate_json(json.dumps(self.document))
        with self.assertRaisesRegex(ValueError, "explicit_bill"):
            review_supplier_bill(model, self.context, self.evidence)
        result = self.review({**self.document, "amount": "0"})
        self.assertFalse(result["actual_spending_verified"])

    def test_bounded_duplicate_free_document_has_stable_review_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bill.json"
            path.write_text(json.dumps(self.document))
            model = load_bill_review(path)
            self.assertEqual(
                hashlib.sha256(canonical_bill(model)).hexdigest(), self.review()["review_sha256"]
            )
            path.write_text('{"amount":"0","amount":"1"}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_bill_review(path)
            path.write_bytes(b" " * 65537)
            with self.assertRaisesRegex(ValueError, "bounded"):
                load_bill_review(path)
