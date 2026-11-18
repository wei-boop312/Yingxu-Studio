"""Adversarial review of cross-file/PG scope bindings, no credentials or network."""

import copy
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from visual_platform.domain.provider_pricing import SupplierPriceVersion, canonical_price
from visual_platform.providers.authorization import (
    BailianSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.providers.bailian_connection import BailianConnection
from visual_platform.providers.runtime import ProviderRuntime
from visual_platform.services.provider_preflight import review_bound_scope, verify_price_evidence


class BoundPreflightTests(unittest.TestCase):
    def test_evidence_readback_checks_hash_version_and_never_returns_bytes(self):
        payload = b"synthetic price evidence, no real price"
        digest = hashlib.sha256(payload).hexdigest()
        bundle = {"price_evidence": {}, "prices": {}}
        for step in ("generate", "detect"):
            bundle["price_evidence"][step] = (
                "synthetic-bucket",
                "private/supplier-price/" + str(uuid4()) + "/" + digest,
                "v1",
                digest,
            )
            bundle["prices"][step] = {"document": {"evidence_sha256": digest}}

        class Storage:
            bucket = "synthetic-bucket"
            returned = payload
            version = "v1"
            calls = 0

            def get(self, key, *, version, limit):
                self.calls += 1
                return self.returned, "application/octet-stream", self.version

        storage = Storage()
        self.assertEqual(verify_price_evidence(storage, bundle), {"generate": True, "detect": True})
        storage.returned = b"wrong evidence"
        self.assertEqual(
            verify_price_evidence(storage, bundle), {"generate": False, "detect": False}
        )
        storage.returned = payload
        storage.version = "v2"
        self.assertEqual(
            verify_price_evidence(storage, bundle), {"generate": False, "detect": False}
        )
        before = storage.calls
        for step in ("generate", "detect"):
            bundle["price_evidence"][step] = (
                "other-bucket",
                "private/credentials/forbidden",
                "v1",
                digest,
            )
        self.assertEqual(
            verify_price_evidence(storage, bundle), {"generate": False, "detect": False}
        )
        self.assertEqual(storage.calls, before)

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.user, self.product, self.source, self.quote, self.authorization = [
            str(uuid4()) for _ in range(5)
        ]
        self.runtime = ProviderRuntime.model_validate(
            {
                "schema_version": 2,
                "version": "synthetic-review",
                "generate": {
                    "provider": "aliyun_bailian",
                    "model": "qwen-image-3.0",
                    "endpoint_origin": "https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                    "merchant_fingerprint": "a" * 64,
                    "credential_fingerprint": "b" * 64,
                    "billing_group": "synthetic",
                },
                "detect": {
                    "provider": "hubway_gemini",
                    "model": "gemini-3.8-flash",
                    "endpoint_origin": "https://hubway.cc",
                    "merchant_fingerprint": "c" * 64,
                    "credential_fingerprint": "d" * 64,
                    "billing_group": "synthetic",
                },
            },
            strict=True,
        )
        self.connection = BailianConnection(
            endpoint_origin=self.runtime.generate.endpoint_origin, credential_fingerprint="b" * 64
        )
        self.prices = {}
        bindings = {}
        for step in ("generate", "detect"):
            selected = getattr(self.runtime, step)
            meters = (
                ["qima_input_1k", "qima_input_2k", "qima_output_1k", "qima_output_2k"]
                if step == "generate"
                else ["input_tokens", "cached_input_tokens", "output_tokens", "thinking_tokens"]
            )
            price = SupplierPriceVersion.model_validate_json(
                json.dumps(
                    {
                        "schema_version": 2 if step == "generate" else 1,
                        "provider": "aliyun_bailian" if step == "generate" else "gemini",
                        "model": selected.model,
                        "endpoint_origin": selected.endpoint_origin,
                        "merchant_fingerprint": selected.merchant_fingerprint,
                        "billing_group": selected.billing_group,
                        "version": "synthetic-" + step,
                        "currency": "CNY" if step == "generate" else "USD",
                        "valid_from": (self.now - timedelta(minutes=1)).isoformat(),
                        "valid_until": (self.now + timedelta(hours=1)).isoformat(),
                        "evidence_kind": "offline_fixture",
                        "evidence_sha256": "e" * 64,
                        "complete_meter_coverage": True,
                        "rates": [
                            {"measure": measure, "amount": "0.10", "per": 1} for measure in meters
                        ],
                    }
                )
            )
            identity = str(uuid4())
            digest = hashlib.sha256(canonical_price(price)).hexdigest()
            bindings[step] = {"id": identity, "document_sha256": digest}
            self.prices[step] = {
                "id": identity,
                "document": price.model_dump(mode="json"),
                "document_sha256": digest,
                "evidence_archived": True,
            }
        scope_data = {
            "schema_version": 4,
            "authorization_id": self.authorization,
            "user_id": self.user,
            "product_id": self.product,
            "source_asset_version_id": self.source,
            "authorized_by": "synthetic only",
            "authorized_at": (self.now - timedelta(seconds=1)).isoformat(),
            "expires_at": (self.now + timedelta(minutes=20)).isoformat(),
            "source_path": "synthetic.png",
            "source_sha256": "e" * 64,
            "quote_input_sha256": "f" * 64,
            "generation": {
                "type": "scene",
                "prompt": "synthetic",
                "output": {"width": 512, "height": 512, "format": "png"},
                "protection_mode": "none",
                "regions": [],
            },
            "targets": [
                {
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "generic",
                    "image_type": "MAIN",
                }
            ],
            "step_budgets": {
                "generate": {"currency": "CNY", "ceiling": "1"},
                "detect": {"currency": "USD", "ceiling": "0.1"},
            },
            "currency_totals": [
                {"currency": "CNY", "ceiling": "1"},
                {"currency": "USD", "ceiling": "0.1"},
            ],
        }
        initial = BailianSmokeAuthorization.model_validate_json(json.dumps(scope_data))
        snapshot = {
            "product_id": self.product,
            "source_asset_version_id": self.source,
            "generation": initial.generation.model_dump(mode="json"),
            "targets": [t.model_dump(mode="json") for t in initial.targets],
            "_execution": {
                **self.runtime.execution_binding(),
                "source_sha256": "e" * 64,
                "provider_profile": "offline_protocol_mock",
                "provider_price_versions": bindings,
            },
        }
        digest = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        scope_data["quote_input_sha256"] = digest
        self.scope = BailianSmokeAuthorization.model_validate_json(json.dumps(scope_data))
        self.bundle = {
            "quote": {
                "quote_id": self.quote,
                "user_id": self.user,
                "input_snapshot": snapshot,
                "input_sha256": digest,
                "expires_at": scope_data["expires_at"],
            },
            "runtime": {
                "document": self.runtime.model_dump(mode="json"),
                "document_sha256": self.runtime.digest(),
                "revoked": False,
            },
            "budget": {
                "authorization_id": self.authorization,
                "quote_id": self.quote,
                "schema_version": 4,
                "scope_sha256": hashlib.sha256(canonical_authorization(self.scope)).hexdigest(),
                "profile": "offline_protocol_mock",
                "state": "open",
                "holds": [],
                "blocked_for_cost_review": False,
                "expires_at": scope_data["expires_at"],
                "step_limits": [{"step": k, **v} for k, v in scope_data["step_budgets"].items()],
                "limits_by_currency": [
                    {"currency": r["currency"], "total_ceiling": r["ceiling"]}
                    for r in scope_data["currency_totals"]
                ],
            },
            "prices": self.prices,
            "projection": {
                "projection_status": "projected",
                "quote_input_sha256": digest,
                "price_document_sha256": bindings["generate"]["document_sha256"],
                "amount": "0.20000000",
                "currency": "CNY",
                "supplier_usage_observed": False,
                "quantities": {
                    "qima_input_1k": 1,
                    "qima_input_2k": 0,
                    "qima_output_1k": 1,
                    "qima_output_2k": 0,
                },
            },
        }

    def review(self, bundle=None, **kwargs):
        return review_bound_scope(
            self.scope,
            self.runtime,
            self.connection,
            bundle or self.bundle,
            now=self.now,
            allow_offline_fixture=True,
            **kwargs,
        )

    def test_bound_fixture_review_is_advisory_and_never_execution_authority(self):
        result = self.review()
        self.assertTrue(result["scope_checks_passed"])
        self.assertFalse(result["credential_check_performed"])
        self.assertFalse(result["execution_enabled"])
        self.assertFalse(result["actual_spending_verified"])
        self.assertTrue(result["advisory_snapshot_only"])
        self.assertEqual(result["paid_calls"], 0)
        strict = review_bound_scope(
            self.scope, self.runtime, self.connection, self.bundle, now=self.now
        )
        self.assertFalse(strict["scope_checks_passed"])
        self.assertEqual(
            set(strict["failures"]), {"generate_price_evidence_kind", "detect_price_evidence_kind"}
        )

    def test_mixed_quotes_budgets_revocation_holds_and_projection_changes_reject(self):
        cases = [
            ("quote", "user_id", str(uuid4()), "scope_identity"),
            ("quote", "input_sha256", "0" * 64, "quote_hash"),
            ("runtime", "revoked", True, "registered_runtime_active"),
            ("budget", "scope_sha256", "0" * 64, "budget_scope"),
            ("budget", "quote_id", str(uuid4()), "budget_scope"),
            ("budget", "state", "closed", "unused_open_budget"),
            ("budget", "holds", [{"synthetic": True}], "unused_open_budget"),
            ("projection", "amount", "0.00000000", "request_prediction"),
        ]
        for section, field, value, failure in cases:
            with self.subTest(section=section, field=field):
                bundle = copy.deepcopy(self.bundle)
                bundle[section][field] = value
                self.assertIn(failure, self.review(bundle)["failures"])

    def test_price_hash_evidence_and_currency_substitution_reject(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["prices"]["generate"]["document_sha256"] = "0" * 64
        self.assertIn("generate_price", self.review(bundle)["failures"])
        bundle = copy.deepcopy(self.bundle)
        bundle["prices"]["detect"]["evidence_archived"] = False
        self.assertIn("detect_price", self.review(bundle)["failures"])
        bundle = copy.deepcopy(self.bundle)
        bundle["budget"]["step_limits"][0]["currency"] = "USD"
        self.assertIn("budget_units", self.review(bundle)["failures"])

    def test_optional_credential_check_requires_both_exact_boolean_matches(self):
        for credentials in (
            {"generate": True, "detect": False},
            {"generate": True},
            {"generate": 1, "detect": True},
        ):
            self.assertIn("credential_matches", self.review(credentials=credentials)["failures"])
        self.assertTrue(
            self.review(credentials={"generate": True, "detect": True})["scope_checks_passed"]
        )
