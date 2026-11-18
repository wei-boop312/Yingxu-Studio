"""Synthetic reviewed CNY/USD rates on a real PG/S3 Qwen rehearsal."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from bailian_fixture import BailianFixture

from visual_platform.domain.provider_pricing import SupplierPriceVersion, canonical_price
from visual_platform.services.provider_budget import provider_quote_review
from visual_platform.services.provider_pricing import publish_supplier_price
from visual_platform.services.runtime_quote import prepare_runtime_quote
from visual_platform.storage.s3 import S3Storage


class BailianPricingFixture(BailianFixture):
    evidence = b"Synthetic Qwen and Gemini rates; not actual prices, balances or invoices."

    def setUp(self):
        super().setUp()
        self.qwen_output_rate = "0.20"
        self.qwen_usage_complete = True

    def publish_price(self, step, *, merchant=None):
        runtime = self.qwen_runtime.generate if step == "generate" else self.qwen_runtime.detect
        now = datetime.now(timezone.utc)
        generation = step == "generate"
        document = {
            "schema_version": 2 if generation else 1,
            "provider": "aliyun_bailian" if generation else "gemini",
            "model": runtime.model,
            "endpoint_origin": runtime.endpoint_origin,
            "merchant_fingerprint": merchant or runtime.merchant_fingerprint,
            "billing_group": runtime.billing_group,
            "version": "synthetic-" + uuid4().hex,
            "currency": "CNY" if generation else "USD",
            "valid_from": (now - timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat(),
            "evidence_kind": "offline_fixture",
            "evidence_sha256": hashlib.sha256(self.evidence).hexdigest(),
            "complete_meter_coverage": True,
            "rates": [
                {"measure": "qima_input_1k", "amount": "0.10", "per": 1},
                {"measure": "qima_output_1k", "amount": self.qwen_output_rate, "per": 1},
                {"measure": "qima_input_2k", "amount": "0.40", "per": 1},
                {"measure": "qima_output_2k", "amount": "0.50", "per": 1},
            ]
            if generation
            else [
                {"measure": "input_tokens", "amount": "0.50", "per": 1000000},
                {"measure": "cached_input_tokens", "amount": "0.10", "per": 1000000},
                {"measure": "output_tokens", "amount": "1", "per": 1000000},
                {"measure": "thinking_tokens", "amount": "1", "per": 1000000},
            ],
        }
        if generation:
            for rate in document["rates"]:
                rate["per"] = getattr(self, "qwen_rate_per", 1)
        price = SupplierPriceVersion.model_validate_json(json.dumps(document))
        storage = S3Storage(self.settings)
        try:
            identity = publish_supplier_price(
                self.operator_dsn,
                storage,
                price,
                self.evidence,
                uuid4(),
                "synthetic reviewed image/token rates",
                evidence_reviewed=True,
            )
        finally:
            storage.close()
        return {
            "id": identity,
            "document_sha256": hashlib.sha256(canonical_price(price)).hexdigest(),
        }

    def qwen_review(self):
        self.price_bindings = {step: self.publish_price(step) for step in ("generate", "detect")}
        source, body = self.source_quote()
        prepared = prepare_runtime_quote(
            self.operator_dsn,
            self.user,
            source,
            self.qwen_runtime,
            self.qwen_runtime.digest(),
            uuid4(),
            "synthetic priced Qwen quote",
            price_versions=self.price_bindings,
        )
        quote = UUID(prepared["quote_id"])
        return quote, body, provider_quote_review(self.operator_dsn, self.user, quote)

    def qwen_route(self, request):
        response = super().qwen_route(request)
        if (
            request.method == "GET"
            and request.url.path.startswith("/api/v1/tasks/")
            and not self.qwen_usage_complete
        ):
            body = response.json()
            body.pop("usage", None)
            return httpx.Response(200, json=body)
        return response

    def qwen_detector(self, request):
        body = super().qwen_detector(request).json()
        body["modelVersion"] = "gemini-3.8-flash"
        body["usageMetadata"] = {
            "promptTokenCount": 10,
            "cachedContentTokenCount": 2,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 3,
            "toolUsePromptTokenCount": 0,
            "totalTokenCount": 33,
        }
        return httpx.Response(200, json=body)
