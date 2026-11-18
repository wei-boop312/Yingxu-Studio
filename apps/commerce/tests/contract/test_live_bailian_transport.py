"""Live selection is explicit; no test enables supplier network transport."""

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from visual_platform.providers.live_bailian import live_bailian_runtime
from visual_platform.providers.qwen_image import QwenImageProvider
from visual_platform.providers.qwen_vision import QwenVisionProvider
from visual_platform.providers.runtime import ProviderRuntime, credential_fingerprint


class LiveBailianTransportContracts(unittest.TestCase):
    def runtime(self):
        step = {
            "provider": "aliyun_bailian",
            "model": "qwen-image-3.0",
            "endpoint_origin": "https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            "merchant_fingerprint": "a" * 64,
            "credential_fingerprint": credential_fingerprint("synthetic-key"),
            "billing_group": "synthetic",
        }
        return ProviderRuntime.model_validate(
            {
                "schema_version": 3,
                "version": "synthetic",
                "generate": step,
                "detect": {**step, "provider": "aliyun_bailian_vision", "model": "qwen3.8-flash"},
            },
            strict=True,
        )

    def test_live_flag_cannot_mix_with_mock_or_implicit_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            for adapter in (QwenImageProvider, QwenVisionProvider):
                for extra in (
                    {},
                    {"live": 1},
                    {"live": True, "transport": httpx.MockTransport(lambda r: httpx.Response(200))},
                ):
                    with (
                        self.subTest(adapter=adapter.__name__, extra=extra),
                        self.assertRaises(ValueError),
                    ):
                        adapter(
                            "synthetic-key",
                            Path(folder),
                            endpoint_origin=self.runtime().generate.endpoint_origin,
                            **extra,
                        )

    def test_runtime_key_mismatch_stops_before_transport_creation(self):
        async def run():
            with tempfile.TemporaryDirectory() as folder, patch("httpx.HTTPTransport") as transport:
                with self.assertRaises(ValueError):
                    async with live_bailian_runtime(self.runtime(), Path(folder), key="wrong-key"):
                        self.fail("mismatched key admitted")
                transport.assert_not_called()

        asyncio.run(run())

    def test_live_composition_does_not_submit_on_construction(self):
        async def run():
            calls = []

            def route(request):
                calls.append(request)
                raise AssertionError("construction must not call a model")

            with (
                tempfile.TemporaryDirectory() as folder,
                patch("httpx.HTTPTransport", return_value=httpx.MockTransport(route)),
                patch("httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(route)),
            ):
                async with live_bailian_runtime(
                    self.runtime(), Path(folder), key="synthetic-key"
                ) as provider:
                    self.assertEqual(provider.execution_profile, "bailian_live")
                    self.assertFalse(provider.bfl.offline_transport)
                    self.assertEqual(provider.bfl.client.timeout.read, 120)
                    self.assertEqual(provider.bfl.client.timeout.connect, 5)
                    self.assertFalse(provider.gemini.offline_transport)
                    self.assertEqual(calls, [])

        asyncio.run(run())

    def test_operator_cli_default_does_not_activate_or_call_models(self):
        result = subprocess.run(
            [sys.executable, "scripts/live_provider.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("LIVE_CONFIGURATION_NOT_REQUESTED", result.stdout)
        self.assertIn('"paid_calls": 0', result.stdout)
