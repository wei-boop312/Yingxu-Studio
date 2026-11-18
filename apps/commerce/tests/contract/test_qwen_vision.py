"""Optional Qwen visual detector contracts; no real keys or live model clients."""

import asyncio
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image

from visual_platform.providers.contract import ProviderFailure, ProviderUncertain
from visual_platform.providers.qwen_vision import (
    MODEL,
    PATH,
    QwenVisionProvider,
    validate_vision_archive,
)

ORIGIN = "https://ws-synthetic.cn-beijing.maas.aliyuncs.com"


class QwenVisionContracts(unittest.IsolatedAsyncioTestCase):
    async def test_advisory_price_rederives_wire_counts_and_binds_all_three_provenance_hashes(self):
        from visual_platform.domain.provider_pricing import SupplierPriceVersion
        from visual_platform.domain.visual_price_review import (
            VisualPriceReviewScope,
            estimate_visual_archive,
        )

        provider = self.provider()
        receipt = await provider.submit(
            uuid4(), self.png, expected_sha256=self.sha, width=256, height=256
        )
        record = provider.fetch_facts(receipt.provider_id)
        now = datetime.now(timezone.utc)
        price = SupplierPriceVersion(
            schema_version=3,
            provider="aliyun_bailian_vision",
            model=MODEL,
            endpoint_origin=ORIGIN,
            merchant_fingerprint="a" * 64,
            billing_group="synthetic-only",
            version="offline-visual-wire",
            currency="CNY",
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1),
            evidence_kind="offline_fixture",
            evidence_sha256="b" * 64,
            complete_meter_coverage=True,
            rates=[
                {"measure": name, "amount": amount, "per": 1000000}
                for name, amount in (
                    ("input_tokens", "0.8"),
                    ("cached_input_tokens", "0.1"),
                    ("output_tokens", "2.7"),
                )
            ],
        )
        scope = VisualPriceReviewScope(
            final_sha256=self.sha,
            request_sha256=record["request_sha256"],
            response_digest=receipt.response_digest,
            merchant_fingerprint="a" * 64,
            billing_group="synthetic-only",
            observed_at=now,
        )
        result = estimate_visual_archive(price, record, scope)
        self.assertEqual((result["amount"], result["currency"]), ("0.00002910", "CNY"))
        self.assertFalse(result["actual_spending_verified"])
        self.assertFalse(result["spending_authorized"])
        self.assertFalse(result["task_cost_record_created"])
        for field in ("final_sha256", "request_sha256", "response_digest"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "provenance_mismatch"),
            ):
                estimate_visual_archive(price, record, scope.model_copy(update={field: "f" * 64}))
        modified = copy.deepcopy(record)
        modified["quantities"]["output_tokens"] = 0
        with self.assertRaises(ValueError):
            estimate_visual_archive(price, modified, scope)
        foreign = price.model_copy(
            update={"endpoint_origin": "https://ws-other.cn-beijing.maas.aliyuncs.com"}
        )
        self.assertEqual(
            estimate_visual_archive(foreign, record, scope)["reason"], "price_identity_mismatch"
        )
        self.body["usage"].pop("prompt_tokens_details")
        second = await provider.submit(
            uuid4(), self.png, expected_sha256=self.sha, width=256, height=256
        )
        unknown = provider.fetch_facts(second.provider_id)
        unknown_scope = scope.model_copy(
            update={
                "response_digest": second.response_digest,
                "request_sha256": unknown["request_sha256"],
            }
        )
        self.assertEqual(
            estimate_visual_archive(price, unknown, unknown_scope)["cost_status"], "unknown"
        )

    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.providers = []
        self.calls = []
        stream = BytesIO()
        Image.new("RGB", (256, 256), "white").save(stream, "PNG")
        self.png = stream.getvalue()
        self.sha = hashlib.sha256(self.png).hexdigest()
        self.facts = {
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
        self.body = {
            "id": "chatcmpl-synthetic",
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(self.facts)},
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }

    async def asyncTearDown(self):
        for provider in self.providers:
            await provider.aclose()
        self.folder.cleanup()

    def provider(self, route=None):
        def default(request):
            self.calls.append(request)
            return httpx.Response(200, json=self.body)

        provider = QwenVisionProvider(
            "synthetic-key",
            Path(self.folder.name),
            endpoint_origin=ORIGIN,
            transport=httpx.MockTransport(route or default),
        )
        self.providers.append(provider)
        return provider

    async def submit(self, provider, intent=None, **changes):
        return await provider.submit(
            intent or uuid4(),
            self.png,
            **{"expected_sha256": self.sha, "width": 256, "height": 256, **changes},
        )

    def test_real_transports_are_rejected_before_clients_or_directories(self):
        directory = Path(self.folder.name) / "never-created"
        with patch(
            "visual_platform.providers.qwen_vision.httpx.AsyncClient",
            side_effect=AssertionError("no clients"),
        ):
            with self.assertRaises(ValueError):
                QwenVisionProvider("synthetic", directory, endpoint_origin=ORIGIN)
        self.assertFalse(directory.exists())

    async def test_exact_final_png_json_mode_and_original_wire_archive(self):
        provider = self.provider()
        receipt = await self.submit(provider)
        request = self.calls[0]
        payload = json.loads(request.content)
        self.assertEqual((request.method, request.url.path), ("POST", PATH))
        self.assertEqual(request.url.host, "ws-synthetic.cn-beijing.maas.aliyuncs.com")
        self.assertEqual(payload["model"], MODEL)
        self.assertIs(payload["enable_thinking"], False)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", payload)
        inline = payload["messages"][1]["content"][0]["image_url"]["url"]
        self.assertEqual(base64.b64decode(inline.split(",", 1)[1]), self.png)
        record = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(record["facts"], self.facts)
        self.assertEqual(record["reported_model"], MODEL)
        self.assertEqual(record["response_digest"], receipt.response_digest)
        self.assertEqual(
            hashlib.sha256(base64.b64decode(record["response_body_base64"])).hexdigest(),
            record["wire_response_sha256"],
        )
        self.assertEqual(record["billing_usage_integrity"], "complete")
        self.assertIsNone(record["cost"]["money_amount"])
        self.assertFalse(record["cost"]["actual_spending_verified"])

    async def test_missing_or_wrong_final_input_rejected_without_intent_or_http(self):
        provider = self.provider()
        for change in (
            {"expected_sha256": "a" * 64},
            {"width": 255},
            {"width": True},
            {"width": 9000, "height": 9000},
        ):
            with self.assertRaises(ValueError):
                await self.submit(provider, **change)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    async def test_partial_wrong_model_refusal_tools_and_bad_facts_remain_manual(self):
        valid = copy.deepcopy(self.body)
        cases = []
        body = copy.deepcopy(valid)
        wrong_dimensions = json.loads(body["choices"][0]["message"]["content"])
        wrong_dimensions["image_width"] = 257
        body["choices"][0]["message"]["content"] = json.dumps(wrong_dimensions)
        cases.append(body)
        body = copy.deepcopy(valid)
        body["choices"][0]["finish_reason"] = "length"
        cases.append(body)
        body = copy.deepcopy(valid)
        body["model"] = "qwen-other-model"
        cases.append(body)
        body = copy.deepcopy(valid)
        body["choices"][0]["message"]["tool_calls"] = [{"id": "not-executed"}]
        cases.append(body)
        body = copy.deepcopy(valid)
        body["choices"][0]["message"]["refusal"] = "refused"
        cases.append(body)
        body = copy.deepcopy(valid)
        body["choices"][0]["message"]["content"] = (
            '{"analysis_complete":true,"analysis_complete":false}'
        )
        cases.append(body)
        provider = self.provider()
        for body in cases:
            self.body = body
            receipt = await self.submit(provider)
            record = provider.fetch_facts(receipt.provider_id)
            self.assertFalse(record["facts"]["analysis_complete"])
            self.assertTrue(record["manual_review_required"])
            if body["model"] != MODEL:
                self.assertEqual(record["billing_usage_integrity"], "reported_model_unverified")
                self.assertIsNone(record["quantities"])
        self.assertEqual(len(self.calls), len(cases))

    async def test_missing_usage_is_unknown_and_low_confidence_keeps_detected_text(self):
        self.body.pop("usage")
        facts = copy.deepcopy(self.facts)
        facts["text_items"] = [
            {
                "text": "on-product lettering",
                "bbox": [1, 2, 30, 40],
                "origin": "on_product",
                "confidence": 0.5,
            }
        ]
        self.body["choices"][0]["message"]["content"] = json.dumps(facts)
        provider = self.provider()
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(record["facts"]["text_items"][0]["text"], "on-product lettering")
        self.assertFalse(record["facts"]["analysis_complete"])
        self.assertIn("qwen_low_confidence", record["facts"]["uncertainties"])
        self.assertIsNone(record["quantities"])
        self.assertEqual(record["billing_usage_integrity"], "missing")

    async def test_timeout_explicit_failure_and_redirect_never_automatically_resubmit(self):
        for status in (None, 429, 503, 307):
            seen = []

            def route(request):
                seen.append(request)
                if status is None:
                    raise httpx.ReadTimeout("synthetic")
                return httpx.Response(
                    status,
                    headers={"location": "https://other.invalid/collect"},
                    json={"error": "synthetic"},
                )

            provider = self.provider(route)
            intent = uuid4()
            with self.assertRaises(ProviderFailure if status == 429 else ProviderUncertain):
                await self.submit(provider, intent)
            with self.assertRaises(ProviderUncertain):
                await self.submit(provider, intent)
            self.assertEqual(len(seen), 1)

    async def test_concurrent_intent_has_one_post_and_archive_mutation_is_rejected(self):
        provider = self.provider()
        intent = uuid4()
        outcomes = await asyncio.gather(
            *(self.submit(provider, intent) for _ in range(4)), return_exceptions=True
        )
        receipts = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(self.calls), 1)
        record = provider.fetch_facts(receipts[0].provider_id)
        for change in (
            {"archive_version": 2},
            {"response_digest": "f" * 64},
            {"manual_review_required": 0},
            {"final_sha256": "not-a-sha"},
            {"cost": {"money_amount": "0", "currency": "CNY", "actual_spending_verified": True}},
        ):
            altered = copy.deepcopy(record)
            altered.update(change)
            with self.assertRaises(ValueError):
                validate_vision_archive(altered)
        path = Path(self.folder.name) / (str(intent) + ".facts.json")
        altered = copy.deepcopy(record)
        altered["quantities"]["input_tokens"] = 999
        path.write_text(json.dumps(altered))
        with self.assertRaises(ProviderUncertain):
            provider.fetch_facts(receipts[0].provider_id)
