"""Read-only metadata HTTP contracts; synthetic keys and explicit mock transports."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import httpx

from visual_platform.providers.bailian_connection import BailianConnection
from visual_platform.providers.bailian_metadata import MAX_METADATA_BODY, probe_metadata
from visual_platform.providers.runtime import credential_fingerprint


class BailianMetadataContracts(unittest.TestCase):
    def test_optional_vision_model_requires_exact_catalogue_row_without_generation_claim(self):
        requests = []

        def route(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "output": {"models": [{"model": "qwen3.8-flash", "capabilities": ["VL"]}]},
                },
            )

        report, _ = probe_metadata(
            self.configuration,
            self.key,
            model="qwen3.8-flash",
            transport=httpx.MockTransport(route),
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].url.params["model"], "qwen3.8-flash")
        self.assertTrue(report["model_listed"])
        self.assertFalse(report["image_generation_capability_listed"])
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(report["model_inference_requests"], 0)
        with self.assertRaisesRegex(ValueError, "unsupported_metadata_model"):
            probe_metadata(
                self.configuration,
                self.key,
                model="unreviewed-model",
                transport=httpx.MockTransport(route),
            )
        self.assertEqual(len(requests), 1)

    def test_cli_without_probe_never_reads_configuration_or_requests_metadata(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "bailian_metadata.py"
        process = subprocess.run(
            [sys.executable, str(script), "--config", "missing-private-file.json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, 3)
        report = json.loads(process.stdout)
        self.assertEqual(report["result"], "METADATA_PROBE_NOT_REQUESTED")
        self.assertEqual(report["metadata_get_requests"], 0)

    def test_network_failure_does_not_echo_headers_or_confirm_partial_authentication(self):
        def route(request):
            raise httpx.ConnectError("synthetic exception containing " + self.key, request=request)

        report, body = probe_metadata(
            self.configuration, self.key, transport=httpx.MockTransport(route)
        )
        self.assertEqual(report["result"], "METADATA_UNREACHABLE")
        self.assertFalse(report["remote_authentication_observed"])
        self.assertFalse(report["captured_body_complete"])
        self.assertNotIn(self.key, json.dumps(report))
        self.assertEqual(body, b"")

    def test_permission_boolean_must_not_be_coerced_or_inferred_from_a_model_row(self):
        for allowed in ("true", 1, None):

            def route(request):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "output": {
                            "permissions": [
                                {"model": "qwen-image-3.0", "permissions": {"inference": allowed}}
                            ]
                        },
                    },
                )

            report, _ = probe_metadata(
                self.configuration,
                self.key,
                kind="permissions",
                transport=httpx.MockTransport(route),
            )
            self.assertEqual(report["result"], "METADATA_BODY_REJECTED")
            self.assertFalse(report["workspace_inference_permission_observed"])
            self.assertFalse(report["remote_authentication_observed"])

    def test_workspace_permission_get_never_claims_key_scope_or_paid_execution(self):
        seen = []

        def route(request):
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "output": {
                        "permissions": [
                            {
                                "model": "qwen-image-3.0",
                                "permissions": {"inference": True, "deploy": False},
                            }
                        ]
                    },
                },
            )

        report, _ = probe_metadata(
            self.configuration, self.key, kind="permissions", transport=httpx.MockTransport(route)
        )
        self.assertEqual(report["result"], "WORKSPACE_PERMISSION_METADATA_READ")
        self.assertTrue(report["workspace_inference_permission_observed"])
        self.assertFalse(report["generation_permission_verified"])
        self.assertFalse(report["provider_verified"])
        self.assertEqual((seen[0].method, seen[0].url.path), ("GET", "/api/v1/models/permissions"))
        self.assertEqual(seen[0].url.params["authorization_scope"], "AUTHORIZED")
        self.assertEqual(seen[0].url.params["action"], "INFERENCE")
        self.assertEqual(len(seen), 1)

    def setUp(self):
        self.key = "synthetic-metadata-key"
        self.configuration = BailianConnection(
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            credential_fingerprint=credential_fingerprint(self.key),
        )
        self.requests = []

    def probe(self, response):
        def route(request):
            self.requests.append(request)
            return response

        return probe_metadata(self.configuration, self.key, transport=httpx.MockTransport(route))

    def test_one_exact_get_can_observe_catalogue_authentication_but_never_execution(self):
        report, raw = self.probe(
            httpx.Response(
                200,
                json={
                    "success": True,
                    "code": None,
                    "output": {
                        "models": [
                            {
                                "model": "qwen-image-3.0",
                                "capabilities": ["IG"],
                                "description": self.key,
                            }
                        ]
                    },
                },
            )
        )
        self.assertEqual(report["result"], "REMOTE_MODEL_METADATA_READ")
        self.assertTrue(report["remote_authentication_observed"])
        self.assertTrue(report["image_generation_capability_listed"])
        self.assertFalse(report["generation_permission_verified"])
        self.assertFalse(report["provider_verified"])
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(report["paid_model_calls"], 0)
        self.assertNotIn(self.key, json.dumps(report))
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual((request.method, request.url.path), ("GET", "/api/v1/models"))
        self.assertEqual(
            dict(request.url.params),
            {"model": "qwen-image-3.0", "page_no": "1", "page_size": "1", "language": "zh-CN"},
        )
        self.assertEqual(request.headers["Authorization"], "Bearer " + self.key)
        self.assertTrue(raw)

    def test_denial_or_redirect_never_follows_location_or_retries(self):
        for status in (401, 403, 429, 500, 302, 307):
            before = len(self.requests)
            report, _ = self.probe(
                httpx.Response(
                    status,
                    headers={"location": "https://other.invalid/collect"},
                    json={"message": self.key},
                )
            )
            self.assertFalse(report["remote_authentication_observed"])
            self.assertNotIn(self.key, json.dumps(report))
            self.assertEqual(len(self.requests), before + 1)

    def test_invalid_and_contradictory_json_cannot_confirm_authentication(self):
        cases = [
            b'{"success":true,"success":false}',
            b'{"success":NaN}',
            b'{"success":true,"code":"InvalidApiKey","output":{"models":[]}}',
            b'{"success":1,"output":{"models":[]}}',
            b'{"success":true,"output":{"models":[null]}}',
        ]
        for body in cases:
            report, _ = self.probe(httpx.Response(200, content=body))
            self.assertEqual(report["result"], "METADATA_BODY_REJECTED")
            self.assertFalse(report["remote_authentication_observed"])

    def test_empty_or_different_model_is_not_model_availability_or_permission_proof(self):
        for models in ([], [{"model": "qwen-image-3.0-pro"}]):
            report, _ = self.probe(
                httpx.Response(200, json={"success": True, "output": {"models": models}})
            )
            self.assertTrue(report["remote_authentication_observed"])
            self.assertEqual(report["result"], "MODEL_NOT_LISTED_IN_METADATA")
            self.assertFalse(report["model_listed"])

    def test_capture_limit_is_not_misrepresented_as_a_complete_original_body(self):
        report, raw = self.probe(httpx.Response(200, content=b"a" * (MAX_METADATA_BODY + 100)))
        self.assertEqual(report["result"], "METADATA_BODY_REJECTED")
        self.assertFalse(report["captured_body_complete"])
        self.assertEqual(len(raw), MAX_METADATA_BODY + 1)

    def test_mismatched_key_or_workspace_is_rejected_before_a_transport_call(self):
        def route(request):
            self.fail("transport must not be reached")

        with self.assertRaises(ValueError):
            probe_metadata(
                self.configuration, "other-synthetic-key", transport=httpx.MockTransport(route)
            )
        with self.assertRaises(ValueError):
            probe_metadata(
                self.configuration.model_copy(update={"endpoint_origin": "http://127.0.0.1"}),
                self.key,
                transport=httpx.MockTransport(route),
            )
