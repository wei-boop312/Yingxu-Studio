"""Offline HTTP protocol contracts. No BFL account or paid calls."""

import base64
import copy
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image
from pydantic import ValidationError

from visual_platform.providers.bfl import BflPoll, BflProvider, BflRequest
from visual_platform.providers.contract import ProviderFailure, ProviderUncertain, Receipt
from visual_platform.providers.pipeline import ProtocolProvider


class BflContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        stream = BytesIO()
        Image.new("RGB", (256, 256), "white").save(stream, "PNG")
        self.png = stream.getvalue()
        self.request = BflRequest(
            prompt="preserve product",
            images=[base64.b64encode(self.png).decode()],
            width=256,
            height=256,
        )
        self.identity = "task-123"
        self.url = "https://api.eu2.bfl.ai/v1/get_result?id=" + self.identity
        self.receipt = Receipt(self.identity, "a" * 64, self.url)
        self.calls = []

    def provider(self, handler=None):
        def route(request):
            self.calls.append(request)
            return (
                handler(request)
                if handler
                else httpx.Response(
                    200, json={"id": self.identity, "polling_url": self.url, "cost": 0.06}
                )
            )

        provider = BflProvider(
            "contract-key", Path(self.temp.name), transport=httpx.MockTransport(route)
        )
        self.addCleanup(provider.close)
        return provider

    def test_full_public_prompt_survives_internal_prefix_and_http_submission(self):
        provider = self.provider()
        for kind in ("white_background", "scene", "lifestyle"):
            generation = {
                "type": kind,
                "prompt": "商" * 4000,
                "output": {"width": 256, "height": 256, "format": "png"},
                "protection_mode": "none",
                "regions": [],
            }
            with self.subTest(kind=kind):
                request = BflRequest.from_canonical(generation, self.png)
                self.assertTrue(request.prompt.endswith(generation["prompt"]))
                self.assertGreater(len(request.prompt), 4000)
                provider.submit(uuid4(), request)
                body = json.loads(self.calls[-1].content)
                self.assertEqual(body["prompt"], request.prompt)
                self.assertFalse(body["grounding"])
                before = len(self.calls)
                generation["prompt"] += "商"
                with self.assertRaises(ValidationError):
                    BflRequest.from_canonical(generation, self.png)
                self.assertEqual(len(self.calls), before)

    def test_frozen_appearance_is_data_without_private_metadata_and_fits_maxima(self):
        generation = {
            "type": "scene",
            "prompt": "商" * 4000,
            "output": {"width": 256, "height": 256, "format": "png"},
            "protection_mode": "none",
            "regions": [],
        }
        config = {
            "description": '"' * 4000,
            "colors": ['"' * 255] * 20,
            "materials": ["\\" * 255] * 20,
        }
        appearance = {"config": config, "id": "private-identity", "name": "private-name"}
        request = BflRequest.from_canonical(generation, self.png, appearance)
        self.assertLessEqual(len(request.prompt), 32768)
        self.assertIn(generation["prompt"], request.prompt)
        self.assertEqual(json.loads(request.prompt.split("instructions):\n", 1)[1]), config)
        self.assertNotIn("private-identity", request.prompt)
        self.assertNotIn("private-name", request.prompt)
        provider = self.provider()
        provider.submit(uuid4(), request)
        self.assertEqual(json.loads(self.calls[-1].content)["prompt"], request.prompt)
        for invalid in ({"colors": ["x" * 256]}, {"materials": [False]}, {"api_key": "bad"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                BflRequest.from_canonical(generation, self.png, {"config": invalid})
        self.assertEqual(len(self.calls), 1)

    def test_submit_exact_endpoint_fields_auth_and_cost_provenance(self):
        provider = self.provider()
        intent = uuid4()
        receipt = provider.submit(intent, self.request)
        request = self.calls[0]
        self.assertEqual(str(request.url), "https://api.bfl.ai/v1/flux-3-image")
        self.assertEqual(request.headers["x-key"], "contract-key")
        self.assertNotIn("authorization", request.headers)
        body = json.loads(request.content)
        self.assertEqual(set(body), {"prompt", "images", "width", "height", "grounding"})
        self.assertFalse(body["grounding"])
        self.assertEqual(base64.b64decode(body["images"][0]), self.png)
        self.assertEqual(receipt.provider_id, self.identity)
        self.assertEqual(receipt.polling_url, self.url)
        cost = provider.receipt_record(intent)["cost"]
        self.assertEqual(cost["quantity"], "0.06")
        self.assertEqual(cost["unit"], "BFL_CREDIT")
        self.assertIsNone(cost["currency"])
        self.assertIsNone(cost["money_amount"])
        self.assertEqual(cost["source_digest"], receipt.response_digest)
        self.assertNotIn(
            "contract-key", "".join(p.read_text() for p in Path(self.temp.name).iterdir())
        )

    def test_wire_precision_and_digest_survive_without_float_rounding_or_intent_marker(self):
        raw = (
            ' {\n"id":"'
            + self.identity
            + '","polling_url":"'
            + self.url
            + '","cost":0.0600000000000000001}\n'
        ).encode()
        provider = self.provider(lambda _: httpx.Response(200, content=raw))
        intent = uuid4()
        receipt = provider.submit(intent, self.request)
        record = provider.receipt_record(intent)
        self.assertEqual(record["cost"]["quantity"], "0.0600000000000000001")
        self.assertEqual(record["archive_version"], 2)
        self.assertEqual(base64.b64decode(record["response_body_base64"]), raw)
        self.assertEqual(receipt.response_digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(record["wire_response_sha256"], receipt.response_digest)
        self.assertEqual(
            (Path(self.temp.name) / (str(intent) + ".response.body")).read_bytes(), raw
        )
        (Path(self.temp.name) / (str(intent) + ".intent")).unlink()
        with self.assertRaises(ProviderUncertain):
            provider.submit(intent, self.request)
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_and_nonstandard_json_preserve_raw_unknown_outcome_without_resubmit(self):
        for cost in ('0.1,"cost":0.2', "NaN", "Infinity"):
            raw = (
                '{"id":"'
                + self.identity
                + '","polling_url":"'
                + self.url
                + '","cost":'
                + cost
                + "}"
            ).encode()
            provider = self.provider(lambda _: httpx.Response(200, content=raw))
            intent = uuid4()
            before = len(self.calls)
            with self.subTest(cost=cost), self.assertRaises(ProviderUncertain):
                provider.submit(intent, self.request)
            self.assertEqual(
                (Path(self.temp.name) / (str(intent) + ".response.body")).read_bytes(), raw
            )
            with self.assertRaises(ProviderUncertain):
                provider.submit(intent, self.request)
            self.assertEqual(len(self.calls), before + 1)

    def test_unrepresentable_credit_precision_keeps_known_receipt_and_stops(self):
        for cost in ("1e-100000", "1e100000"):
            raw = (
                '{"id":"'
                + self.identity
                + '","polling_url":"'
                + self.url
                + '","cost":'
                + cost
                + "}"
            ).encode()
            provider = self.provider(lambda _: httpx.Response(200, content=raw))
            intent = uuid4()
            with self.subTest(cost=cost), self.assertRaises(ProviderUncertain):
                provider.submit(intent, self.request)
            record = provider.receipt_record(intent)
            self.assertEqual(record["provider_id"], self.identity)
            self.assertEqual(record["cost"]["validation"], "invalid")
            self.assertIsNone(record["cost"]["quantity"])

    def test_changed_receipt_fields_cannot_disagree_with_its_original_wire_response(self):
        provider = self.provider()
        intent = uuid4()
        provider.submit(intent, self.request)
        original = provider.receipt_record(intent)
        path = Path(self.temp.name) / (str(intent) + ".receipt.json")
        for field in ("quantity", "wire", "provider_id"):
            record = copy.deepcopy(original)
            if field == "quantity":
                record["cost"]["quantity"] = "0.01"
            elif field == "wire":
                record["response_body_base64"] = base64.b64encode(b"{}").decode()
            else:
                record["provider_id"] = "changed-id"
                record["polling_url"] = "https://api.eu2.bfl.ai/v1/get_result?id=changed-id"
            path.write_text(json.dumps(record))
            with self.subTest(field=field), self.assertRaises(ProviderUncertain):
                provider.receipt_record(intent)
        path.write_text(json.dumps(original))
        self.assertEqual(provider.receipt_record(intent), original)

    def test_legacy_local_receipt_remains_readable_but_cannot_invent_wire_cost_evidence(self):
        provider = self.provider()
        intent = uuid4()
        receipt = provider.submit(intent, self.request)
        record = provider.receipt_record(intent)
        for key in ("archive_version", "response_body_base64", "wire_response_sha256"):
            record.pop(key)
        record["cost"]["quantity"] = "1E-30"
        (Path(self.temp.name) / (str(intent) + ".receipt.json")).write_text(json.dumps(record))
        self.assertNotIn("wire_response_sha256", provider.receipt_record(intent))
        observed = ProtocolProvider.observation(
            SimpleNamespace(bfl=provider),
            "generate",
            {
                "id": str(intent),
                "provider_id": self.identity,
                "response_digest": receipt.response_digest,
            },
        )
        self.assertIsNone(observed["provider_cost"])
        self.assertEqual(observed["source"], "legacy_local_receipt_has_no_wire_evidence")

    def test_recreated_adapter_cannot_submit_same_intent(self):
        first = self.provider()
        intent = uuid4()
        receipt = first.submit(intent, self.request)
        second = self.provider()
        with self.assertRaises(ProviderUncertain):
            second.submit(intent, self.request)
        self.assertEqual(second.receipt_record(intent)["provider_id"], receipt.provider_id)
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_intent_exclusive_and_one_post(self):
        provider = self.provider()
        intent = uuid4()

        def submit():
            try:
                return provider.submit(intent, self.request).provider_id
            except ProviderUncertain:
                return "blocked"

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertCountEqual(results, [self.identity, "blocked"])
        self.assertEqual(len(self.calls), 1)

    def test_timeout_never_reposts_even_after_restart(self):
        def fail(request):
            raise httpx.ReadTimeout("sensitive contract-key " + str(request.url))

        provider = self.provider(fail)
        intent = uuid4()
        with self.assertRaises(ProviderUncertain) as caught:
            provider.submit(intent, self.request)
        self.assertNotIn("contract-key", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        with self.assertRaises(ProviderUncertain):
            self.provider().submit(intent, self.request)
        self.assertEqual(len(self.calls), 1)

    def test_rejections_and_server_errors_single_attempt(self):
        for code in (400, 401, 402, 403, 422, 429, 500, 503, 302):
            with self.subTest(code=code):
                provider = self.provider(
                    lambda _: httpx.Response(
                        code,
                        json={"error": "contract-key"},
                        headers={"location": "https://evil.example"},
                    )
                )
                before = len(self.calls)
                intent = uuid4()
                expected = (
                    ProviderFailure if code in (400, 401, 402, 403, 422, 429) else ProviderUncertain
                )
                with self.assertRaises(expected) as caught:
                    provider.submit(intent, self.request)
                self.assertNotIn("contract-key", str(caught.exception))
                with self.assertRaises(ProviderUncertain):
                    provider.submit(intent, self.request)
                self.assertEqual(len(self.calls) - before, 1)

    def test_invalid_receipts_are_unknown_and_never_resubmitted(self):
        values = [
            {},
            [],
            {"id": self.identity},
            {"id": 42, "polling_url": self.url},
            {"id": self.identity, "polling_url": "https://evil.example/v1/get_result?id=task-123"},
            {"id": self.identity, "polling_url": self.url, "cost": True},
            {"id": self.identity, "polling_url": self.url, "cost": -1},
        ]
        for result in values:
            provider = self.provider(lambda _, v=result: httpx.Response(200, json=v))
            intent = uuid4()
            with self.assertRaises(ProviderUncertain):
                provider.submit(intent, self.request)
            with self.assertRaises(ProviderUncertain):
                provider.submit(intent, self.request)

    def test_missing_cost_stays_unknown(self):
        provider = self.provider(
            lambda _: httpx.Response(200, json={"id": self.identity, "polling_url": self.url})
        )
        intent = uuid4()
        provider.submit(intent, self.request)
        self.assertIsNone(provider.receipt_record(intent)["cost"]["quantity"])

    def test_malformed_cost_preserves_known_receipt_for_reconciliation(self):
        provider = self.provider(
            lambda _: httpx.Response(
                200, json={"id": self.identity, "polling_url": self.url, "cost": True}
            )
        )
        intent = uuid4()
        with self.assertRaises(ProviderUncertain):
            provider.submit(intent, self.request)
        record = provider.receipt_record(intent)
        self.assertEqual(record["provider_id"], self.identity)
        self.assertEqual(record["cost"]["validation"], "invalid")
        self.assertIsNone(record["cost"]["quantity"])

    def test_unsafe_poll_hosts_paths_ids_and_download_urls(self):
        urls = [
            "http://api.bfl.ai/v1/get_result?id=task-123",
            "https://api.bfl.ai.evil.example/v1/get_result?id=task-123",
            "https://api.bfl.ai@127.0.0.1/v1/get_result?id=task-123",
            "https://api.bfl.ai:444/v1/get_result?id=task-123",
            "https://api.bfl.ai/v1/get_result?id=other",
            "https://api.bfl.ai/v1/flux-3-image?id=task-123",
            self.url + "#fragment",
            "https://api.bfl.ai\\@evil.example/v1/get_result?id=task-123",
        ]
        provider = self.provider()
        for url in urls:
            with self.assertRaises(ProviderUncertain):
                provider.poll(Receipt(self.identity, "x", url))
        for url in (
            "https://127.0.0.1/file",
            "https://delivery.eu.bfl.ai.evil.example/a",
            "http://delivery.eu.bfl.ai/a",
            "https://api.bfl.ai/a",
        ):
            with self.assertRaises(ProviderUncertain):
                provider.fetch_result(BflPoll(self.identity, "Ready", url))
        self.assertEqual(self.calls, [])

    def test_pending_poll_uses_exact_returned_url_and_no_post(self):
        for status in ("Pending", "Reasoning", "Generating"):
            provider = self.provider(lambda _, s=status: httpx.Response(200, json={"status": s}))
            self.assertEqual(provider.poll(self.receipt).status, status)
            self.assertEqual(self.calls[-1].method, "GET")
            self.assertEqual(str(self.calls[-1].url), self.url)

    def test_terminal_and_unknown_status(self):
        for status in (
            "Error",
            "Failed",
            "Request Moderated",
            "Content Moderated",
            "new_status",
            None,
        ):
            provider = self.provider(lambda _, s=status: httpx.Response(200, json={"status": s}))
            with self.assertRaises(
                ProviderFailure
                if status in ("Error", "Failed", "Request Moderated", "Content Moderated")
                else ProviderUncertain
            ):
                provider.poll(self.receipt)

    def test_ready_download_isolated_from_key_and_api_cookie(self):
        sample = "https://delivery.eu2.bfl.ai/result?token=private-result"

        def handler(request):
            if request.url.host.startswith("api"):
                return httpx.Response(
                    200,
                    json={"status": "Ready", "result": {"sample": sample}},
                    headers={"set-cookie": "sensitive=api-cookie; Domain=.bfl.ai; Secure"},
                )
            self.assertNotIn("x-key", request.headers)
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=self.png, headers={"content-type": "image/png"})

        provider = self.provider(handler)
        poll = provider.poll(self.receipt)
        artifact = provider.fetch_result(poll)
        self.assertEqual(artifact.data, self.png)
        self.assertEqual(artifact.sha256, hashlib.sha256(self.png).hexdigest())
        self.assertEqual(artifact.mime, "image/png")

    def test_download_redirect_mime_empty_and_size_rejected(self):
        cases = [
            httpx.Response(302, headers={"location": "https://evil.example/"}),
            httpx.Response(200, content=b"x", headers={"content-type": "text/html"}),
            httpx.Response(200, content=b"", headers={"content-type": "image/png"}),
            httpx.Response(
                200, content=b"x" * (20 * 1024 * 1024 + 1), headers={"content-type": "image/png"}
            ),
        ]
        for response in cases:
            provider = self.provider(lambda _, r=response: r)
            with self.assertRaises(ProviderUncertain):
                provider.fetch_result(
                    BflPoll(self.identity, "Ready", "https://delivery.eu.bfl.ai/a")
                )

    def test_request_schema_rejects_extra_url_reference_and_bad_geometry(self):
        for patch_value in (
            {"input_image": "old-field"},
            {"images": ["http://localhost/private.png"]},
            {"width": True},
            {"height": 255},
            {"width": 2001, "height": 2000},
            {"grounding": True},
            {"grounding": 0},
            {"prompt": "   "},
        ):
            with self.subTest(value=patch_value), self.assertRaises(ValidationError):
                BflRequest.model_validate({**self.request.model_dump(), **patch_value})
        self.request.images.append("https://evil.example/source")
        with self.assertRaises(ValidationError):
            self.provider().submit(uuid4(), self.request)
        self.assertEqual(self.calls, [])

    def test_canonical_mapping_omits_local_regions_and_internal_fields(self):
        generation = {
            "type": "white_background",
            "prompt": "soft light",
            "output": {"width": 256, "height": 256, "format": "png"},
            "protection_mode": "pixel_exact",
            "regions": [
                {
                    "source_x": 0,
                    "source_y": 0,
                    "width": 1,
                    "height": 1,
                    "target_x": 0,
                    "target_y": 0,
                }
            ],
        }
        request = BflRequest.from_canonical(generation, self.png)
        self.assertIn("white background", request.prompt)
        self.assertNotIn("regions", request.model_dump())
        self.assertEqual(request.width, 256)

    def test_oversized_json_and_malformed_ready_rejected(self):
        for response in (
            httpx.Response(200, content=b"not json"),
            httpx.Response(200, content=b"X" * (1024 * 1024 + 1)),
            httpx.Response(
                200, json={"status": "Ready", "result": {"sample": "https://evil.example"}}
            ),
        ):
            provider = self.provider(lambda _, r=response: r)
            with self.assertRaises(ProviderUncertain):
                provider.poll(self.receipt)

    def test_contract_transport_never_opens_socket(self):
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            self.provider().submit(uuid4(), self.request)

    def test_compressed_http_body_is_rejected_before_reading(self):
        import gzip

        provider = self.provider(
            lambda _: httpx.Response(
                200, content=gzip.compress(b"{}"), headers={"content-encoding": "gzip"}
            )
        )
        with self.assertRaises(ProviderUncertain):
            provider.poll(self.receipt)
        with self.assertRaises(ProviderUncertain):
            provider.fetch_result(BflPoll(self.identity, "Ready", "https://delivery.eu.bfl.ai/a"))
