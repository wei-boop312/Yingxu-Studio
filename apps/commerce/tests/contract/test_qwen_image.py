"""Synthetic Qwen async edits; no real credentials or network transports."""

import base64
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from qwen_fixture import canonical_png

from visual_platform.providers.contract import ProviderFailure, ProviderUncertain, Receipt
from visual_platform.providers.qwen_image import (
    ASYNC_PATH,
    MAX_BODY,
    MAX_IMAGE,
    QwenImageProvider,
    QwenImageRequest,
    image_url,
    workspace_origin,
)

ORIGIN = "https://ws-synthetic.cn-beijing.maas.aliyuncs.com"
PNG = canonical_png()
URL = "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.png?Expires=1"


class QwenImageContracts(unittest.TestCase):
    def test_oversized_submit_body_preserves_intent_and_cannot_trigger_a_second_post(self):
        def route(request):
            self.calls.append(request)
            return httpx.Response(
                200, content=b" " * (MAX_BODY + 1), headers={"content-type": "application/json"}
            )

        provider = self.provider(route)
        intent = uuid4()
        with self.assertRaises(ProviderUncertain):
            provider.submit(intent, self.request())
        with self.assertRaises(ProviderUncertain):
            provider.submit(intent, self.request())
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((Path(self.temp.name) / (str(intent) + ".intent")).is_file())

    def test_download_rejects_non_png_mime_and_false_png_signature_without_authentication(self):
        for mime, raw in (("image/jpeg", PNG), ("image/png", b"not PNG bytes")):

            def route(request):
                if request.url.host.startswith("dashscope-result-"):
                    self.calls.append(request)
                    self.assertNotIn("authorization", request.headers)
                    return httpx.Response(200, content=raw, headers={"content-type": mime})
                return self.route(request)

            provider = self.provider(route)
            receipt = provider.submit(uuid4(), self.request())
            with self.subTest(mime=mime), self.assertRaises(ProviderUncertain):
                provider.fetch_result(provider.poll(receipt))

    def test_source_limits_reject_before_journal_or_network_even_after_model_copy(self):
        provider = self.provider()
        damaged = bytearray(PNG)
        damaged[29] ^= 1
        invalid = (
            canonical_png(8001, 801),
            canonical_png(1024, 64),
            PNG[:24],
            bytes(damaged),
            PNG + b"0" * (MAX_IMAGE + 1 - len(PNG)),
        )
        for data in invalid:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                provider.submit(
                    uuid4(),
                    self.request().model_copy(update={"images": [base64.b64encode(data).decode()]}),
                )
        self.assertEqual(self.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        admitted = QwenImageRequest(
            prompt="synthetic boundary",
            width=512,
            height=512,
            images=[base64.b64encode(canonical_png(8000, 800)).decode()],
        )
        self.assertEqual(len(admitted.payload()["input"]["messages"][0]["content"]), 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.submit_raw = b'{"output":{"task_id":"synthetic-task","task_status":"PENDING"}}'
        self.result = {
            "output": {
                "task_id": "synthetic-task",
                "task_status": "SUCCEEDED",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": [{"image": URL}]},
                    }
                ],
            },
            "usage": {
                "input_image_count": 1,
                "output_image_count": 1,
                "input_image_type": "qima_input_1k",
                "output_image_type": "qima_output_1k",
                "output_width": 1024,
                "output_height": 1024,
            },
        }

    def request(self):
        return QwenImageRequest(
            prompt="Preserve product; change background only.",
            images=[base64.b64encode(PNG).decode()],
            width=1024,
            height=1024,
        )

    def route(self, request):
        self.calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                200, content=self.submit_raw, headers={"content-type": "application/json"}
            )
        if request.url.host.startswith("dashscope-result-"):
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("x-key", request.headers)
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(200, json=self.result)

    def provider(self, route=None):
        provider = QwenImageProvider(
            "synthetic-key",
            Path(self.temp.name),
            endpoint_origin=ORIGIN,
            transport=httpx.MockTransport(route or self.route),
        )
        self.addCleanup(provider.close)
        return provider

    def test_edit_envelope_async_receipt_poll_and_keyless_download(self):
        provider = self.provider()
        intent = uuid4()
        receipt = provider.submit(intent, self.request())
        self.assertEqual(receipt.response_digest, hashlib.sha256(self.submit_raw).hexdigest())
        self.assertEqual(
            (Path(self.temp.name) / (str(intent) + ".response.body")).read_bytes(), self.submit_raw
        )
        sent = self.calls[0]
        self.assertEqual(str(sent.url), ORIGIN + ASYNC_PATH)
        self.assertEqual(sent.headers["x-dashscope-async"], "enable")
        payload = json.loads(sent.content)
        self.assertEqual(payload["model"], "qwen-image-3.0")
        self.assertEqual(
            payload["parameters"],
            {"n": 1, "size": "1024*1024", "prompt_extend": False, "watermark": False},
        )
        image = payload["input"]["messages"][0]["content"][0]["image"]
        self.assertEqual(base64.b64decode(image.split(",", 1)[1]), PNG)
        poll = provider.poll(receipt)
        self.assertEqual(poll.usage, self.result["usage"])
        artifact = provider.fetch_result(poll)
        self.assertEqual(artifact.data, PNG)
        result_raw = base64.b64decode(artifact.result_wire_body_base64)
        self.assertEqual(json.loads(result_raw), self.result)
        self.assertEqual(hashlib.sha256(result_raw).hexdigest(), artifact.result_response_sha256)
        self.assertEqual([r.method for r in self.calls], ["POST", "GET", "GET"])

    def test_live_transport_and_unreviewed_origins_rejected_before_file_creation(self):
        absent = Path(self.temp.name) / "must-not-exist"
        with self.assertRaises(ValueError):
            QwenImageProvider("synthetic-key", absent, endpoint_origin=ORIGIN)
        self.assertFalse(absent.exists())
        for origin in (
            ORIGIN + "/",
            ORIGIN + "@evil.invalid",
            ORIGIN.replace("cn-beijing", "ap-southeast-1"),
            "http://127.0.0.1",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                workspace_origin(origin)

    def test_competing_submissions_and_lost_intent_never_duplicate_post(self):
        provider = self.provider()
        intent = uuid4()

        def submit(_):
            try:
                return provider.submit(intent, self.request())
            except ProviderUncertain:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual(sum(isinstance(r, Receipt) for r in results), 1)
        (Path(self.temp.name) / (str(intent) + ".intent")).unlink()
        with self.assertRaises(ProviderUncertain):
            provider.submit(intent, self.request())
        self.assertEqual(len(self.calls), 1)

    def test_timeout_diagnostic_is_private_safe_and_blocks_lost_intent_resubmission(self):
        def timeout(request):
            self.calls.append(request)
            self.assertEqual(request.extensions["timeout"]["read"], 30)
            raise httpx.ReadTimeout("secret-key signed-url private-prompt")

        provider = self.provider(timeout)
        intent = uuid4()
        with self.assertRaisesRegex(ProviderUncertain, "qwen_read_timeout"):
            provider.submit(intent, self.request())
        path = Path(self.temp.name) / (str(intent) + ".submission-diagnostic.json")
        raw = path.read_text()
        diagnostic = json.loads(raw)
        self.assertEqual(diagnostic["reason"], "qwen_read_timeout")
        self.assertEqual(diagnostic["outcome"], "unknown")
        self.assertFalse(diagnostic["automatic_resubmission"])
        self.assertGreaterEqual(diagnostic["elapsed_ms"], 0)
        self.assertNotIn("secret", raw)
        self.assertNotIn("signed-url", raw)
        (Path(self.temp.name) / (str(intent) + ".intent")).unlink()
        with self.assertRaises(ProviderUncertain):
            provider.submit(intent, self.request())
        self.assertEqual(len(self.calls), 1)

    def test_timeout_unknown_keeps_intent_and_never_retries_post(self):
        def timeout(request):
            self.calls.append(request)
            raise httpx.ReadTimeout("synthetic uncertainty")

        provider = self.provider(timeout)
        intent = uuid4()
        for _ in range(2):
            with self.assertRaises(ProviderUncertain):
                provider.submit(intent, self.request())
        self.assertEqual(len(self.calls), 1)

    def test_rejections_and_ambiguous_http_are_not_retried(self):
        for status in (400, 401, 403, 429, 302, 500):
            with self.subTest(status=status):
                count = []

                def route(request):
                    count.append(request)
                    return httpx.Response(status, headers={"location": "https://evil.invalid"})

                provider = self.provider(route)
                intent = uuid4()
                expected = ProviderFailure if status in (400, 401, 403, 429) else ProviderUncertain
                with self.assertRaises(expected):
                    provider.submit(intent, self.request())
                with self.assertRaises(ProviderUncertain):
                    provider.submit(intent, self.request())
                self.assertEqual(len(count), 1)

    def test_malformed_duplicate_or_unknown_submit_retained_without_resubmission(self):
        for raw in (
            b'{"output":{},"output":{}}',
            b'{"output":NaN}',
            b'{"output":{"task_id":"../escape","task_status":"PENDING"}}',
        ):
            with self.subTest(raw=raw):
                self.submit_raw = raw
                provider = self.provider()
                intent = uuid4()
                for _ in range(2):
                    with self.assertRaises(ProviderUncertain):
                        provider.submit(intent, self.request())
                self.assertEqual(
                    (Path(self.temp.name) / (str(intent) + ".response.body")).read_bytes(), raw
                )
        self.assertEqual(len(self.calls), 3)

    def test_known_task_poll_states_and_workspace_substitution(self):
        provider = self.provider()
        receipt = provider.submit(uuid4(), self.request())
        for status in ("PENDING", "RUNNING", "FAILED", "CANCELED", "UNKNOWN", "invented"):
            self.result["output"]["task_status"] = status
            if status in ("PENDING", "RUNNING"):
                self.assertEqual(provider.poll(receipt).status, "Pending")
            else:
                exception = (
                    ProviderFailure if status in ("FAILED", "CANCELED") else ProviderUncertain
                )
                with self.assertRaises(exception):
                    provider.poll(receipt)
        before = len(self.calls)
        bad = Receipt(receipt.provider_id, receipt.response_digest, "https://evil.invalid/task")
        with self.assertRaises(ProviderUncertain):
            provider.poll(bad)
        self.assertEqual(len(self.calls), before)

    def test_download_url_ssrf_and_dimension_copy_bypass_rejected(self):
        for url in (
            URL.replace("oss-cn-beijing", "oss-cn-beijing-internal"),
            URL.replace("oss-cn-beijing", "oss-cn-invented-region"),
            "http://127.0.0.1/a.png",
            URL.replace("aliyuncs.com", "aliyuncs.com.evil.invalid"),
            URL.replace("https://", "https://user:pass@"),
            URL + "#secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                image_url(url)
        provider = self.provider()
        invalid = self.request().model_copy(update={"images": self.request().images * 4})
        with self.assertRaises(ValueError):
            provider.submit(uuid4(), invalid)
        invalid = self.request().model_copy(update={"width": 256, "height": 256})
        with self.assertRaises(ValueError):
            provider.submit(uuid4(), invalid)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
