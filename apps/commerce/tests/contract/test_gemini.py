"""Official SDK over offline MockTransport, not a mocked SDK method."""

import asyncio
import base64
import copy
import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image

from visual_platform.providers.contract import ProviderUncertain
from visual_platform.providers.gemini import MODEL, GeminiProvider
from visual_platform.providers.hubway import HubwayGeminiProvider


class GeminiContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
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
            "product_extent": {"bbox": [10, 10, 240, 240], "confidence": 0.95},
            "uncertainties": [],
        }

    async def asyncTearDown(self):
        for provider in self.providers:
            await provider.aclose()
        self.temp.cleanup()

    def response(self, facts=None, *, finish="STOP"):
        return {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": json.dumps(self.facts if facts is None else facts)}],
                    },
                    "finishReason": finish,
                }
            ],
            "responseId": "response-xyz",
            "modelVersion": MODEL,
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 80,
                "thoughtsTokenCount": 20,
                "totalTokenCount": 220,
            },
        }

    def provider(self, handler=None, *, provider_class=GeminiProvider, **options):
        async def route(request):
            self.calls.append(request)
            if handler:
                value = handler(request)
                return await value if asyncio.iscoroutine(value) else value
            return httpx.Response(200, json=self.response())

        provider = provider_class(
            "contract-key", Path(self.temp.name), transport=httpx.MockTransport(route), **options
        )
        self.providers.append(provider)
        return provider

    async def test_hubway_sdk_exact_origins_model_and_private_route_provenance(self):
        for origin in ("https://hubway.cc", "https://z.hubway.cc"):
            with self.subTest(origin=origin):
                provider = self.provider(provider_class=HubwayGeminiProvider, base_url=origin)
                intent = uuid4()
                await self.submit(provider, intent)
                request = self.calls[-1]
                self.assertEqual(
                    str(request.url), origin + "/v1beta/models/" + MODEL + ":generateContent"
                )
                self.assertEqual(request.headers["x-goog-api-key"], "contract-key")
                body = json.loads(request.content)
                self.assertEqual(
                    base64.urlsafe_b64decode(body["contents"][0]["parts"][1]["inlineData"]["data"]),
                    self.png,
                )
                record = json.loads((Path(self.temp.name) / (str(intent) + ".intent")).read_text())
                self.assertEqual(record["endpoint_origin"], origin)
                self.assertNotIn("contract-key", json.dumps(record))

    async def test_hubway_rejects_unapproved_destinations_before_any_request(self):
        for origin in (
            "http://hubway.cc",
            "https://hubway.cc/",
            "https://hubway.cc/v1",
            "https://hubway.cc.evil.example",
            "https://hubway.cc:8443",
            "https://user:password@hubway.cc",
            "https://hubway.cc?key=secret",
            "https://generativelanguage.googleapis.com",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.provider(provider_class=HubwayGeminiProvider, base_url=origin)
        self.assertEqual(self.calls, [])

    async def test_hubway_private_environment_does_not_silently_substitute_models(self):
        with patch.dict(
            "os.environ",
            {
                "HUBWAY_API_KEY": "contract-key",
                "HUBWAY_BASE_URL": "https://z.hubway.cc",
                "HUBWAY_VISION_MODEL": MODEL,
            },
        ):
            provider = HubwayGeminiProvider.from_environment(
                Path(self.temp.name), transport=httpx.MockTransport(lambda _: httpx.Response(503))
            )
            self.providers.append(provider)
            self.assertEqual(provider.endpoint_origin, "https://z.hubway.cc")
            with patch.dict("os.environ", {"HUBWAY_VISION_MODEL": "gemini-4-argon"}):
                with self.assertRaises(ValueError):
                    HubwayGeminiProvider.from_environment(Path(self.temp.name))
            with patch.dict("os.environ", {"HUBWAY_API_KEY": ""}):
                with self.assertRaises(ValueError):
                    HubwayGeminiProvider.from_environment(Path(self.temp.name))
        self.assertEqual(self.calls, [])

    async def test_hubway_unknown_or_rate_limited_submission_never_reposts(self):
        for status in (302, 429, 503):
            with self.subTest(status=status):
                provider = self.provider(
                    lambda request, code=status: httpx.Response(
                        code,
                        headers={"location": "https://untrusted.example/"},
                        json={"error": "unavailable"},
                    ),
                    provider_class=HubwayGeminiProvider,
                )
                intent = uuid4()
                before = len(self.calls)
                with self.assertRaises(ProviderUncertain):
                    await self.submit(provider, intent)
                with self.assertRaises(ProviderUncertain):
                    await self.submit(provider, intent)
                alternate = self.provider(
                    provider_class=HubwayGeminiProvider, base_url="https://z.hubway.cc"
                )
                with self.assertRaises(ProviderUncertain):
                    await self.submit(alternate, intent)
                self.assertEqual(len(self.calls) - before, 1)

    async def submit(self, provider, intent=None, **kwargs):
        return await provider.submit(
            intent or uuid4(),
            self.png,
            expected_sha256=kwargs.get("sha", self.sha),
            width=kwargs.get("width", 256),
            height=256,
        )

    async def test_real_sdk_request_model_final_bytes_schema_and_usage(self):
        provider = self.provider()
        receipt = await self.submit(provider)
        request = self.calls[0]
        body = json.loads(request.content)
        self.assertEqual(
            str(request.url),
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        )
        self.assertEqual(request.headers["x-goog-api-key"], "contract-key")
        inline = body["contents"][0]["parts"][1]["inlineData"]
        self.assertEqual(inline["mime_type"], "image/png")
        self.assertEqual(base64.urlsafe_b64decode(inline["data"]), self.png)
        config = body["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertIn("image_width", config["responseJsonSchema"]["properties"])
        self.assertNotIn("compliant", config["responseJsonSchema"]["properties"])
        self.assertNotIn("tools", body)
        result = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(result["final_sha256"], self.sha)
        self.assertFalse(result["manual_review_required"])
        self.assertEqual(result["provider_response_id"], "response-xyz")
        self.assertEqual(result["usage"]["total_token_count"], 220)
        self.assertIsNone(result["cost"]["money_amount"])
        self.assertIsNone(result["cost"]["currency"])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(
            "contract-key", "".join(path.read_text() for path in Path(self.temp.name).iterdir())
        )

    async def test_sdk_429_and_503_never_retry(self):
        for status in (429, 503):
            provider = self.provider(
                lambda _: httpx.Response(
                    status,
                    json={
                        "error": {
                            "code": status,
                            "message": "sensitive contract-key",
                            "status": "RESOURCE_EXHAUSTED" if status == 429 else "UNAVAILABLE",
                        }
                    },
                )
            )
            intent = uuid4()
            before = len(self.calls)
            with self.assertRaises(ProviderUncertain) as caught:
                await self.submit(provider, intent)
            self.assertNotIn("contract-key", str(caught.exception))
            self.assertEqual(len(self.calls) - before, 1)
            with self.assertRaises(ProviderUncertain):
                await self.submit(self.provider(), intent)
            self.assertEqual(len(self.calls) - before, 1)

    async def test_sdk_timeout_retains_intent_and_sanitizes(self):
        def timeout(request):
            raise httpx.ReadTimeout("contract-key " + str(request.url))

        provider = self.provider(timeout)
        intent = uuid4()
        with self.assertRaises(ProviderUncertain) as caught:
            await self.submit(provider, intent)
        self.assertNotIn("contract-key", str(caught.exception))
        with self.assertRaises(ProviderUncertain):
            await self.submit(provider, intent)
        self.assertEqual(len(self.calls), 1)

    async def test_sdk_continuation_does_not_issue_second_request(self):
        value = self.response(finish="CONTINUATION")
        value["continuationToken"] = "do-not-continue"
        provider = self.provider(lambda _: httpx.Response(200, json=value))
        receipt = await self.submit(provider)
        self.assertTrue(provider.fetch_facts(receipt.provider_id)["manual_review_required"])
        self.assertEqual(len(self.calls), 1)

    async def test_truncated_and_safety_results_degrade(self):
        for finish in ("MAX_TOKENS", "SAFETY", "RECITATION", "OTHER"):
            provider = self.provider(
                lambda _, f=finish: httpx.Response(200, json=self.response(finish=f))
            )
            receipt = await self.submit(provider)
            result = provider.fetch_facts(receipt.provider_id)
            self.assertFalse(result["facts"]["analysis_complete"])
            self.assertTrue(result["manual_review_required"])

    async def test_prompt_block_and_missing_candidate_degrade(self):
        for value in ({"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": []}, {}):
            provider = self.provider(lambda _, v=value: httpx.Response(200, json=v))
            receipt = await self.submit(provider)
            self.assertTrue(provider.fetch_facts(receipt.provider_id)["manual_review_required"])

    async def test_schema_missing_extra_compliance_and_invalid_bbox_degrade(self):
        cases = []
        missing = copy.deepcopy(self.facts)
        del missing["background"]
        cases.append(missing)
        extra = copy.deepcopy(self.facts)
        extra["compliant"] = True
        cases.append(extra)
        bad_bbox = copy.deepcopy(self.facts)
        bad_bbox["product_extent"]["bbox"] = [0, 0, 999, 999]
        cases.append(bad_bbox)
        wrong = copy.deepcopy(self.facts)
        wrong["image_width"] = 512
        cases.append(wrong)
        confidence = copy.deepcopy(self.facts)
        confidence["background"]["confidence"] = 1.1
        cases.append(confidence)
        numeric = copy.deepcopy(self.facts)
        numeric["image_height"] = "256"
        cases.append(numeric)
        for facts in cases:
            provider = self.provider(lambda _, f=facts: httpx.Response(200, json=self.response(f)))
            receipt = await self.submit(provider)
            result = provider.fetch_facts(receipt.provider_id)
            self.assertTrue(result["manual_review_required"])
            self.assertEqual(result["facts"]["image_width"], 256)
            self.assertEqual(
                result["facts"]["uncertainties"], ["gemini_facts_schema_or_dimensions_invalid"]
            )

    async def test_low_confidence_preserves_observations_but_degrades(self):
        facts = copy.deepcopy(self.facts)
        facts["text_items"] = [
            {"text": "brand", "bbox": [10, 10, 80, 40], "origin": "on_product", "confidence": 0.5}
        ]
        provider = self.provider(lambda _: httpx.Response(200, json=self.response(facts)))
        receipt = await self.submit(provider)
        result = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(result["facts"]["text_items"][0]["text"], "brand")
        self.assertIn("gemini_low_confidence", result["facts"]["uncertainties"])
        self.assertFalse(result["facts"]["analysis_complete"])

    async def test_malformed_json_and_function_call_degrade(self):
        malformed = self.response()
        malformed["candidates"][0]["content"]["parts"] = [{"text": '{"schema_version":'}]
        function = self.response()
        function["candidates"][0]["content"]["parts"] = [
            {"functionCall": {"name": "declare_compliant", "args": {}}}
        ]
        for value in (malformed, function):
            provider = self.provider(lambda _, v=value: httpx.Response(200, json=v))
            receipt = await self.submit(provider)
            self.assertTrue(provider.fetch_facts(receipt.provider_id)["manual_review_required"])

    async def test_preflight_hash_dimensions_and_non_png_do_not_submit(self):
        provider = self.provider()
        for args in ({"sha": "a" * 64}, {"width": 100}):
            with self.assertRaises(ValueError):
                await self.submit(provider, **args)
        with self.assertRaises(ValueError):
            await provider.submit(
                uuid4(),
                b"not PNG",
                expected_sha256=hashlib.sha256(b"not PNG").hexdigest(),
                width=256,
                height=256,
            )
        self.assertEqual(self.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    async def test_concurrent_same_intent_and_restart_only_one_post(self):
        provider = self.provider()
        intent = uuid4()
        results = await asyncio.gather(
            self.submit(provider, intent),
            self.submit(self.provider(), intent),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(value, ProviderUncertain) for value in results), 1)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ProviderUncertain):
            await self.submit(self.provider(), intent)
        self.assertEqual(len(self.calls), 1)

    async def test_cancel_after_request_prevents_resubmit(self):
        entered = asyncio.Event()

        async def block(request):
            entered.set()
            await asyncio.Event().wait()

        provider = self.provider(block)
        intent = uuid4()
        task = asyncio.create_task(self.submit(provider, intent))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(ProviderUncertain):
            await self.submit(self.provider(), intent)
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_usage_and_no_response_id_not_invented(self):
        value = self.response()
        del value["usageMetadata"]
        del value["responseId"]
        provider = self.provider(lambda _: httpx.Response(200, json=value))
        receipt = await self.submit(provider)
        result = provider.fetch_facts(receipt.provider_id)
        self.assertIsNone(result["usage"])
        self.assertIsNone(result["provider_response_id"])
        self.assertTrue(receipt.provider_id.startswith("gemini-"))

    async def test_redirect_is_not_followed(self):
        provider = self.provider(
            lambda _: httpx.Response(302, headers={"location": "https://evil.example/steal"})
        )
        with self.assertRaises(ProviderUncertain):
            await self.submit(provider)
        self.assertEqual(len(self.calls), 1)

    async def test_fetch_is_local_and_corrupt_archive_never_calls_again(self):
        provider = self.provider()
        receipt = await self.submit(provider)
        for _ in range(3):
            self.assertEqual(provider.fetch_facts(receipt.provider_id)["final_sha256"], self.sha)
        next(Path(self.temp.name).glob("*.facts.json")).write_text("{}")
        with self.assertRaises(ProviderUncertain):
            provider.fetch_facts(receipt.provider_id)
        self.assertEqual(len(self.calls), 1)

    async def test_oversized_and_compressed_body_fail_without_retry(self):
        import gzip

        oversized = json.dumps(
            {"padding": "X" * (1024 * 1024), "candidates": self.response()["candidates"]}
        ).encode()
        for response in (
            httpx.Response(200, stream=httpx.ByteStream(oversized)),
            httpx.Response(
                200,
                stream=httpx.ByteStream(gzip.compress(b"{}")),
                headers={"content-encoding": "gzip"},
            ),
        ):
            provider = self.provider(lambda _, r=response: r)
            before = len(self.calls)
            with self.assertRaises(ProviderUncertain):
                await self.submit(provider)
            self.assertEqual(len(self.calls) - before, 1)

    async def test_multiple_candidates_and_unspecified_prompt_feedback(self):
        multiple = self.response()
        multiple["candidates"] *= 2
        provider = self.provider(lambda _: httpx.Response(200, json=multiple))
        receipt = await self.submit(provider)
        self.assertTrue(provider.fetch_facts(receipt.provider_id)["manual_review_required"])
        value = self.response()
        value["promptFeedback"] = {"blockReason": "BLOCKED_REASON_UNSPECIFIED"}
        provider = self.provider(lambda _: httpx.Response(200, json=value))
        receipt = await self.submit(provider)
        self.assertFalse(provider.fetch_facts(receipt.provider_id)["manual_review_required"])

    async def test_environment_cannot_redirect_key_or_switch_backend(self):
        with patch.dict(
            "os.environ",
            {
                "GOOGLE_GEMINI_BASE_URL": "https://evil.example",
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_GENAI_USE_ENTERPRISE": "true",
            },
        ):
            provider = self.provider()
            await self.submit(provider)
        self.assertEqual(self.calls[0].url.host, "generativelanguage.googleapis.com")
        self.assertEqual(len(self.calls), 1)

    async def test_checked_in_schema_matches_sdk_request_contract(self):
        from visual_platform.domain.facts import Facts

        root = Path(__file__).resolve().parents[2]
        self.assertEqual(
            json.loads((root / "contracts/facts.schema.json").read_text()),
            Facts.model_json_schema(),
        )

    async def test_usage_is_checked_on_wire_before_sdk_number_coercion(self):
        response = self.response()
        response["usageMetadata"] = {
            "promptTokenCount": True,
            "cachedContentTokenCount": 0,
            "candidatesTokenCount": 80,
            "thoughtsTokenCount": 20,
            "toolUsePromptTokenCount": 0,
            "totalTokenCount": 101,
        }
        provider = self.provider(lambda _: httpx.Response(200, json=response))
        receipt = await provider.submit(
            uuid4(), self.png, expected_sha256=self.sha, width=256, height=256
        )
        record = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(record["billing_usage_integrity"], "invalid")
        self.assertNotIn("prompt_token_count", record["usage"])
        self.assertIsNone(record["cost"]["money_amount"])
        self.assertEqual(len(self.calls), 1)

    async def test_parallel_sdk_responses_cannot_swap_usage_between_intents(self):
        async def route(request):
            payload = json.loads(request.content)
            data = base64.urlsafe_b64decode(
                payload["contents"][0]["parts"][1]["inlineData"]["data"]
            )
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
            response = self.response({**self.facts, "image_width": width, "image_height": height})
            response["usageMetadata"] = {
                "promptTokenCount": width,
                "cachedContentTokenCount": 0,
                "candidatesTokenCount": 1,
                "thoughtsTokenCount": 0,
                "toolUsePromptTokenCount": 0,
                "totalTokenCount": width + 1,
            }
            await asyncio.sleep(0.01)
            return httpx.Response(200, json=response)

        provider = self.provider(route)
        second = BytesIO()
        Image.new("RGB", (300, 256), "white").save(second, "PNG")
        second = second.getvalue()
        first_receipt, second_receipt = await asyncio.gather(
            provider.submit(uuid4(), self.png, expected_sha256=self.sha, width=256, height=256),
            provider.submit(
                uuid4(),
                second,
                expected_sha256=hashlib.sha256(second).hexdigest(),
                width=300,
                height=256,
            ),
        )
        for receipt, width in [(first_receipt, 256), (second_receipt, 300)]:
            record = provider.fetch_facts(receipt.provider_id)
            self.assertEqual(record["billing_usage_integrity"], "complete")
            self.assertEqual(record["usage"]["prompt_token_count"], width)
        self.assertEqual(len(self.calls), 2)

    async def test_reported_different_model_degrades_facts_without_substitution_or_resubmit(self):
        response = self.response()
        response["modelVersion"] = "gemini-4-argon"
        provider = self.provider(lambda _: httpx.Response(200, json=response))
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        self.assertFalse(record["facts"]["analysis_complete"])
        self.assertIn("gemini_reported_model_mismatch", record["facts"]["uncertainties"])
        self.assertIsNone(record["cost"]["money_amount"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn(MODEL, self.calls[0].url.path)

    async def test_wire_body_and_sdk_response_are_saved_with_independent_recomputable_digests(self):
        raw = json.dumps(self.response(), separators=(",", ":")).encode()
        provider = self.provider(
            lambda _: httpx.Response(200, content=raw, headers={"content-type": "application/json"})
        )
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(record["archive_version"], 2)
        self.assertEqual(base64.b64decode(record["response_body_base64"], validate=True), raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), record["wire_response_sha256"])
        self.assertEqual(
            hashlib.sha256(record["canonical_response"].encode()).hexdigest(),
            receipt.response_digest,
        )
        self.assertEqual(record["response_structure_integrity"], "valid")
        self.assertEqual(len(self.calls), 1)

    async def test_cached_facts_usage_body_and_review_flags_cannot_change_silently(self):
        provider = self.provider()
        receipt = await self.submit(provider)
        original = provider.fetch_facts(receipt.provider_id)
        path = Path(self.temp.name) / (receipt.provider_id[7:] + ".facts.json")
        variants = []
        changed = copy.deepcopy(original)
        changed["facts"]["background"]["classification"] = "non_white"
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["usage"]["prompt_token_count"] = True
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["usage"]["prompt_token_count"] = float(changed["usage"]["prompt_token_count"])
        variants.append(changed)
        for field, value in (
            ("wire_response_sha256", "f" * 64),
            ("canonical_response", original["canonical_response"] + " "),
            ("manual_review_required", True),
            ("response_structure_integrity", "ambiguous"),
            ("archive_version", 2.0),
        ):
            variants.append({**original, field: value})
        for changed in variants:
            path.write_text(json.dumps(changed))
            with self.assertRaises(ProviderUncertain):
                provider.fetch_facts(receipt.provider_id)
        path.write_text(json.dumps(original))
        self.assertEqual(provider.fetch_facts(receipt.provider_id), original)
        self.assertEqual(len(self.calls), 1)

    async def test_duplicate_wire_object_keys_degrade_facts_but_preserve_original_response(self):
        raw = json.dumps(self.response()).encode()
        raw = raw[:-1] + b',"responseId":"second-id"}'
        provider = self.provider(
            lambda _: httpx.Response(200, content=raw, headers={"content-type": "application/json"})
        )
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        self.assertEqual(record["response_structure_integrity"], "ambiguous")
        self.assertFalse(record["facts"]["analysis_complete"])
        self.assertIn("gemini_ambiguous_wire_response", record["facts"]["uncertainties"])
        self.assertEqual(base64.b64decode(record["response_body_base64"], validate=True), raw)
        self.assertEqual(record["billing_usage_integrity"], "invalid")
        self.assertEqual(len(self.calls), 1)

    async def test_legacy_fact_record_remains_readable_without_claiming_wire_version_two(self):
        provider = self.provider()
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        for field in (
            "archive_version",
            "canonical_response",
            "response_body_base64",
            "wire_response_sha256",
            "response_structure_integrity",
        ):
            record.pop(field)
        path = Path(self.temp.name) / (receipt.provider_id[7:] + ".facts.json")
        path.write_text(json.dumps(record))
        self.assertEqual(provider.fetch_facts(receipt.provider_id), record)
        self.assertNotIn("archive_version", record)
        self.assertEqual(len(self.calls), 1)

    async def test_wire_body_and_normalized_facts_cannot_be_cross_attached(self):
        provider = self.provider()
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        raw = json.loads(base64.b64decode(record["response_body_base64"]))
        raw["responseId"] = "different-upstream-response"
        replacement = json.dumps(raw).encode()
        record["response_body_base64"] = base64.b64encode(replacement).decode()
        record["wire_response_sha256"] = hashlib.sha256(replacement).hexdigest()
        path = Path(self.temp.name) / (receipt.provider_id[7:] + ".facts.json")
        path.write_text(json.dumps(record))
        with self.assertRaises(ProviderUncertain):
            provider.fetch_facts(receipt.provider_id)
        self.assertEqual(len(self.calls), 1)

    async def test_duplicate_visual_fact_keys_degrade_instead_of_accepting_last_value(self):
        response = self.response()
        text = (
            json.dumps({**self.facts, "analysis_complete": False})[:-1]
            + ',"analysis_complete":true}'
        )
        response["candidates"][0]["content"]["parts"][0]["text"] = text
        provider = self.provider(lambda _: httpx.Response(200, json=response))
        receipt = await self.submit(provider)
        record = provider.fetch_facts(receipt.provider_id)
        self.assertFalse(record["facts"]["analysis_complete"])
        self.assertIn("gemini_facts_schema_or_dimensions_invalid", record["facts"]["uncertainties"])
        self.assertIn(
            text,
            json.loads(record["canonical_response"])["candidates"][0]["content"]["parts"][0][
                "text"
            ],
        )
        self.assertEqual(len(self.calls), 1)
