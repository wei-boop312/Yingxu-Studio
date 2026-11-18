import tempfile
import unittest
from pathlib import Path

import httpx

from visual_platform.providers.bfl import BflProvider
from visual_platform.providers.gemini import GeminiProvider
from visual_platform.providers.pipeline import BASELINE_ENDPOINTS, ProtocolProvider


class ProtocolActivationContracts(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_origin_requires_exact_frozen_endpoints_without_any_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            for origin in (
                "https://generativelanguage.googleapis.com",
                "https://hubway.cc",
                "https://z.hubway.cc",
            ):
                bfl = BflProvider(
                    "offline-key",
                    Path(directory) / "bfl",
                    transport=httpx.MockTransport(lambda _: httpx.Response(503)),
                )
                gemini = GeminiProvider(
                    "offline-key",
                    Path(directory) / "gemini",
                    base_url=origin,
                    transport=httpx.MockTransport(lambda _: httpx.Response(503)),
                )
                try:
                    provider = ProtocolProvider(bfl, gemini)
                    provider.verify_execution_scope(
                        {"provider_endpoints": provider.request_endpoints}
                    )
                    if origin == "https://generativelanguage.googleapis.com":
                        provider.verify_execution_scope({})
                    else:
                        with self.assertRaises(ValueError):
                            provider.verify_execution_scope({})
                        with self.assertRaises(ValueError):
                            provider.verify_execution_scope(
                                {"provider_endpoints": BASELINE_ENDPOINTS}
                            )
                    for invalid in (None, {}, {**provider.request_endpoints, "extra": "ignored"}):
                        with self.assertRaises(ValueError):
                            provider.verify_execution_scope({"provider_endpoints": invalid})
                finally:
                    bfl.close()
                    await gemini.aclose()

    async def test_live_transports_cannot_activate_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            bfl = BflProvider("offline-test-key", Path(directory) / "bfl")
            gemini = GeminiProvider("offline-test-key", Path(directory) / "gemini")
            try:
                with self.assertRaises(ValueError):
                    ProtocolProvider(bfl, gemini)
            finally:
                bfl.close()
                await gemini.aclose()

    async def test_mixed_transport_cannot_activate_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            bfl = BflProvider(
                "offline-test-key",
                Path(directory) / "bfl",
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
            )
            gemini = GeminiProvider("offline-test-key", Path(directory) / "gemini")
            try:
                with self.assertRaises(ValueError):
                    ProtocolProvider(bfl, gemini)
            finally:
                bfl.close()
                await gemini.aclose()

    async def test_account_scope_is_explicit_validated_frozen_and_cannot_include_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            bfl = BflProvider(
                "offline-key",
                Path(directory) / "bfl",
                transport=httpx.MockTransport(lambda _: httpx.Response(503)),
            )
            gemini = GeminiProvider(
                "offline-key",
                Path(directory) / "gemini",
                transport=httpx.MockTransport(lambda _: httpx.Response(503)),
            )
            try:
                accounts = {
                    "generate": {
                        "merchant_fingerprint": "a" * 64,
                        "billing_group": "offline-fixture",
                    }
                }
                provider = ProtocolProvider(bfl, gemini, merchant_accounts=accounts)
                accounts["generate"]["merchant_fingerprint"] = "b" * 64
                provider.verify_execution_scope(
                    {
                        "provider_accounts": {
                            "generate": {
                                "merchant_fingerprint": "a" * 64,
                                "billing_group": "offline-fixture",
                            }
                        }
                    }
                )
                with self.assertRaisesRegex(ValueError, "locked_provider_account_mismatch"):
                    provider.verify_execution_scope({"provider_accounts": accounts})
                for invalid in [
                    {},
                    {"secret": "key"},
                    {"generate": {"api_key": "never-store"}},
                    {
                        "generate": {
                            "merchant_fingerprint": "not-a-hash",
                            "billing_group": "fixture",
                        }
                    },
                ]:
                    with self.assertRaisesRegex(ValueError, "invalid merchant account scope"):
                        ProtocolProvider(bfl, gemini, merchant_accounts=invalid)
            finally:
                bfl.close()
                await gemini.aclose()
