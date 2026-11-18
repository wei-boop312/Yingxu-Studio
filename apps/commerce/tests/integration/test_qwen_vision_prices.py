"""Actual PG/S3 review of synthetic visual prices, without enabling execution."""

import asyncio
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
from PIL import Image
from supplier_pricing_fixture import SupplierPricingFixture

from visual_platform.domain.visual_price_review import VisualPriceReviewScope
from visual_platform.providers.qwen_vision import QwenVisionProvider
from visual_platform.services.provider_pricing import (
    estimate_reviewed_visual_archive,
    supplier_price_context,
)


class QwenVisionPriceReviewTests(SupplierPricingFixture):
    def test_archived_wire_estimate_reads_private_price_without_creating_budget_or_cost(self):
        price, identity = self.publish(self.vision_price())
        image = BytesIO()
        Image.new("RGB", (256, 256), "white").save(image, "PNG")
        data = image.getvalue()
        calls = []
        facts = {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 256,
            "image_height": 256,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "white", "confidence": 0.95},
            "product_extent": None,
            "uncertainties": [],
        }

        def route(request):
            calls.append(request.method)
            return httpx.Response(
                200,
                json={
                    "model": "qwen3.8-flash",
                    "id": "chatcmpl-synthetic-price",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(facts)},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 3},
                        "completion_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            )

        async def archived(directory):
            provider = QwenVisionProvider(
                "synthetic-only",
                Path(directory),
                endpoint_origin=price.endpoint_origin,
                transport=httpx.MockTransport(route),
            )
            try:
                receipt = await provider.submit(
                    uuid4(),
                    data,
                    expected_sha256=hashlib.sha256(data).hexdigest(),
                    width=256,
                    height=256,
                )
                return provider.fetch_facts(receipt.provider_id)
            finally:
                await provider.aclose()

        with tempfile.TemporaryDirectory() as directory:
            record = asyncio.run(archived(directory))
        scope = VisualPriceReviewScope(
            final_sha256=record["final_sha256"],
            request_sha256=record["request_sha256"],
            response_digest=record["response_digest"],
            merchant_fingerprint=price.merchant_fingerprint,
            billing_group=price.billing_group,
            observed_at=datetime.now(timezone.utc),
        )

        def snapshot():
            with psycopg.connect(self.owner) as connection:
                return connection.execute(
                    "SELECT (SELECT count(*) FROM ops.provider_budgets),(SELECT count(*) FROM ops.provider_budget_holds),(SELECT count(*) FROM app.task_cost_records),(SELECT count(*) FROM ops.audit_events)"
                ).fetchone()

        before = snapshot()
        wallet = self.client.get("/api/v1/credits/balance").json()
        result = estimate_reviewed_visual_archive(self.operator_dsn, UUID(identity), record, scope)
        self.assertEqual((result["amount"], result["currency"]), ("0.00002910", "CNY"))
        self.assertTrue(result["advisory_only"])
        self.assertFalse(result["spending_authorized"])
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), wallet)
        self.assertEqual(calls, ["POST"])
        self.assertEqual(self.calls, [])

    def vision_price(self):
        return self.document(
            schema_version=3,
            provider="aliyun_bailian_vision",
            model="qwen3.8-flash",
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            currency="CNY",
            rates=[
                {"measure": "input_tokens", "amount": "0.8", "per": 1000000},
                {"measure": "cached_input_tokens", "amount": "0.1", "per": 1000000},
                {"measure": "output_tokens", "amount": "2.7", "per": 1000000},
            ],
        )

    def test_private_price_review_replays_without_balance_or_model_requests(self):
        price = self.vision_price()
        before = self.client.get("/api/v1/credits/balance").json()
        operation = uuid4()
        _, identity = self.publish(price, operation)
        self.assertEqual(self.publish(price, operation)[1], identity)
        context = supplier_price_context(self.operator_dsn, UUID(identity))
        self.assertEqual(context["document"], price.model_dump(mode="json"))
        self.assertTrue(context["evidence_archived"])
        self.assertFalse(context["actual_spending_verified"])
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), before)
        self.assertEqual(self.calls, [])

    def test_database_independently_rejects_mismatched_identity_and_meter_sets(self):
        valid = self.vision_price().model_dump(mode="json")
        for patch in (
            {"schema_version": 2},
            {"model": "gemini-3.8-flash"},
            {"currency": "USD"},
            {"endpoint_origin": "https://hubway.cc"},
            {"rates": valid["rates"][:-1]},
            {
                "rates": valid["rates"]
                + [{"measure": "thinking_tokens", "amount": "2.7", "per": 1000000}]
            },
        ):
            canonical = json.dumps({**valid, **patch}, sort_keys=True, separators=(",", ":"))
            with self.subTest(patch=patch), psycopg.connect(self.operator_dsn) as connection:
                with self.assertRaisesRegex(
                    psycopg.errors.RaiseException, "invalid_supplier_price"
                ):
                    connection.execute(
                        "SELECT ops.publish_supplier_price(%s,%s,%s,%s,%s,%s,%s)",
                        (
                            canonical,
                            hashlib.sha256(canonical.encode()).hexdigest(),
                            "synthetic-evidence",
                            "private/synthetic",
                            None,
                            uuid4(),
                            "synthetic negative",
                        ),
                    )
        self.assertEqual(self.calls, [])
