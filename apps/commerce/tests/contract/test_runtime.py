"""Runtime binding and actual SDK construction use two explicit offline transports."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from visual_platform.providers.pipeline import ProtocolProvider
from visual_platform.providers.runtime import (
    ProviderRuntime,
    credential_fingerprint,
    credential_status,
    load_runtime,
    offline_runtime,
)

ROOT = Path(__file__).resolve().parents[2]


def runtime_document():
    document = json.loads((ROOT / "ops/provider-runtime.example.json").read_text())
    document["generate"]["credential_fingerprint"] = credential_fingerprint("synthetic-bfl")
    document["detect"]["credential_fingerprint"] = credential_fingerprint("synthetic-hubway")
    return document


class RuntimeContracts(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_digest_binds_models_origins_accounts_and_version(self):
        document = runtime_document()
        runtime = ProviderRuntime.model_validate(document, strict=True)
        reordered = ProviderRuntime.model_validate(
            dict(reversed(list(document.items()))), strict=True
        )
        self.assertEqual(runtime.digest(), reordered.digest())
        for change in ("version", "merchant", "group", "endpoint", "credential"):
            modified = copy.deepcopy(document)
            if change == "version":
                modified["version"] = "review-2"
            elif change == "endpoint":
                modified["detect"]["endpoint_origin"] = "https://z.hubway.cc"
            elif change == "merchant":
                modified["detect"]["merchant_fingerprint"] = "a" * 64
            elif change == "credential":
                modified["detect"]["credential_fingerprint"] = "a" * 64
            else:
                modified["generate"]["billing_group"] = "another-reviewed-group"
            self.assertNotEqual(
                runtime.digest(), ProviderRuntime.model_validate(modified, strict=True).digest()
            )

    async def test_unknown_edit_models_arbitrary_hosts_secrets_and_wrong_stages_reject(self):
        for origin in (
            "http://hubway.cc",
            "https://hubway.cc/v1",
            "https://hubway.cc:443",
            "https://hubway.cc?key=secret",
            "https://user:secret@hubway.cc",
            "https://hubway.cc.evil.example",
            "https://127.0.0.1",
        ):
            document = runtime_document()
            document["detect"]["endpoint_origin"] = origin
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ProviderRuntime.model_validate(document, strict=True)
        for change in ("model", "secret", "stage", "boolean_version"):
            document = runtime_document()
            if change == "model":
                document["generate"]["model"] = "invented-hubway-image-model"
            elif change == "secret":
                document["detect"]["api_key"] = "synthetic-must-not-be-configured-here"
            elif change == "stage":
                document["generate"] = document["detect"]
            else:
                document["schema_version"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                ProviderRuntime.model_validate(document, strict=True)

    async def test_runtime_file_rejects_duplicate_and_nonstandard_json(self):
        document = json.dumps(runtime_document())
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "runtime.json"
            for raw in (
                '{"schema_version":2,' + document[1:],
                '{"unexpected":NaN,' + document[1:],
                " " * 16385,
            ):
                file.write_text(raw)
                with self.assertRaises(ValueError):
                    load_runtime(file)

    async def test_factory_freezes_actual_sdk_origins_and_refuses_legacy_bypass(self):
        calls = []
        transport = httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(503)
        )
        runtime = ProviderRuntime.model_validate(runtime_document(), strict=True)
        with tempfile.TemporaryDirectory() as folder:
            async with offline_runtime(
                runtime,
                Path(folder),
                generate_key="synthetic-bfl",
                detect_key="synthetic-hubway",
                generate_transport=transport,
                detect_transport=transport,
            ) as provider:
                binding = runtime.execution_binding()
                provider.verify_execution_scope(binding)
                self.assertEqual(provider.gemini.endpoint_origin, "https://hubway.cc")
                for key in (
                    "provider_runtime_sha256",
                    "provider_models",
                    "provider_accounts",
                    "provider_endpoints",
                ):
                    modified = copy.deepcopy(binding)
                    modified.pop(key)
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        provider.verify_execution_scope(modified)
                legacy = ProtocolProvider(
                    provider.bfl, provider.gemini, merchant_accounts=binding["provider_accounts"]
                )
                with self.assertRaisesRegex(ValueError, "runtime_mismatch"):
                    legacy.verify_execution_scope(binding)
            self.assertEqual(calls, [])

    async def test_missing_or_live_transport_cannot_construct_runtime_or_create_directory(self):
        runtime = ProviderRuntime.model_validate(runtime_document(), strict=True)
        mock = httpx.MockTransport(lambda _: httpx.Response(503))
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "must-not-exist"
            for first, second in ((None, mock), (mock, None), (None, None)):
                with self.assertRaisesRegex(ValueError, "two_explicit_mock"):
                    async with offline_runtime(
                        runtime,
                        directory,
                        generate_key="synthetic",
                        detect_key="synthetic",
                        generate_transport=first,
                        detect_transport=second,
                    ):
                        self.fail("live runtime must not activate")
                self.assertFalse(directory.exists())
            with self.assertRaisesRegex(ValueError, "credential_mismatch"):
                async with offline_runtime(
                    runtime,
                    directory,
                    generate_key="a-different-key",
                    detect_key="synthetic-hubway",
                    generate_transport=mock,
                    detect_transport=mock,
                ):
                    self.fail("unreviewed credential must not activate")
            self.assertFalse(directory.exists())

    async def test_credential_checks_require_reviewed_key_binding_without_returning_keys(self):
        runtime = ProviderRuntime.model_validate(runtime_document(), strict=True)
        self.assertEqual(credential_status(runtime, {}), {"generate": False, "detect": False})
        self.assertEqual(
            credential_status(runtime, {"BFL_API_KEY": "synthetic", "HUBWAY_API_KEY": "bad\nkey"}),
            {"generate": False, "detect": False},
        )
        self.assertEqual(
            credential_status(
                runtime, {"BFL_API_KEY": "synthetic-bfl", "HUBWAY_API_KEY": "synthetic-hubway"}
            ),
            {"generate": True, "detect": True},
        )
