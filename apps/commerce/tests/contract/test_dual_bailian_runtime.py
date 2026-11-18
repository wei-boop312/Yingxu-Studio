"""Explicit dual-Bailian metadata/composition; never promotes old Gemini budgets."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from qwen_fixture import canonical_png
from test_qwen_runtime import qwen_runtime_document

from visual_platform.providers.runtime import ProviderRuntime, credential_status, offline_runtime
from visual_platform.services.runtime_quote import prepare_runtime_quote, runtime_snapshot


def dual_document():
    document = qwen_runtime_document()
    document["schema_version"] = 3
    document["detect"] = {
        **document["generate"],
        "provider": "aliyun_bailian_vision",
        "model": "qwen3.8-flash",
    }
    return document


class DualBailianRuntimeContracts(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_quote_is_rejected_before_database_connection(self):
        runtime = ProviderRuntime.model_validate(dual_document(), strict=True)
        with patch("visual_platform.services.runtime_quote.psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "qwen_vision_quote_budget_path_not_enabled"):
                prepare_runtime_quote(
                    "unused-dsn", uuid4(), uuid4(), runtime, runtime.digest(), uuid4(), "synthetic"
                )
            connect.assert_not_called()

    async def test_shipped_operator_examples_preserve_hubway_and_report_dual_path_blockers(self):
        root = Path(__file__).resolve().parents[2]
        for name, version, detector in (
            ("provider-runtime-bailian-hubway.example.json", 2, "gemini-3.8-flash"),
            ("provider-runtime-dual-bailian.example.json", 3, "qwen3.8-flash"),
        ):
            with self.subTest(example=name):
                path = root / "ops" / name
                runtime = ProviderRuntime.model_validate_json(path.read_text())
                self.assertEqual(runtime.schema_version, version)
                process = subprocess.run(
                    [
                        sys.executable,
                        str(root / "scripts/provider_runtime.py"),
                        "--config",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(process.returncode, 0, process.stdout)
                result = json.loads(process.stdout)
                self.assertEqual(result["models"]["detect"], detector)
                self.assertIsNone(result["reviewed_credential_present"])
                self.assertFalse(result["execution_enabled"])
                self.assertEqual(result["paid_calls"], 0)
                self.assertEqual(result["protocol_quote_preparation_supported"], version == 2)
                self.assertEqual(
                    len(result["additional_implementation_required"]), 2 if version == 3 else 0
                )

    def test_schema_three_requires_same_workspace_account_key_and_explicit_detector(self):
        runtime = ProviderRuntime.model_validate(dual_document(), strict=True)
        self.assertEqual(
            credential_status(runtime, {"DASHSCOPE_API_KEY": "synthetic-qwen"}),
            {"generate": True, "detect": True},
        )
        for changes in ({"schema_version": 2}, {"schema_version": True}):
            with self.assertRaises(ValueError):
                ProviderRuntime.model_validate({**dual_document(), **changes}, strict=True)
        for field, value in (
            ("model", "gemini-3.8-flash"),
            ("provider", "hubway_gemini"),
            ("endpoint_origin", "https://ws-other.cn-beijing.maas.aliyuncs.com"),
            ("merchant_fingerprint", "f" * 64),
            ("credential_fingerprint", "f" * 64),
            ("billing_group", "other-group"),
        ):
            document = dual_document()
            document["detect"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                ProviderRuntime.model_validate(document, strict=True)

    def test_registered_metadata_cannot_be_used_to_prepare_an_old_budget_quote(self):
        runtime = ProviderRuntime.model_validate(dual_document(), strict=True)
        with self.assertRaisesRegex(ValueError, "qwen_vision_quote_budget_path_not_enabled"):
            runtime_snapshot({"input_snapshot": {}}, runtime)

    async def test_two_explicit_mock_transports_keep_both_model_endpoints_and_fact_lineage(self):
        runtime = ProviderRuntime.model_validate(dual_document(), strict=True)
        calls = []
        png = canonical_png(512, 512)
        facts = {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 512,
            "image_height": 512,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "white", "confidence": 0.9},
            "product_extent": None,
            "uncertainties": [],
        }

        def generation(request):
            calls.append(("generate", request.url.path))
            return httpx.Response(
                200, json={"output": {"task_id": "synthetic-generation", "task_status": "PENDING"}}
            )

        def detection(request):
            calls.append(("detect", request.url.path))
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-synthetic-dual",
                    "model": "qwen3.8-flash",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(facts)},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "prompt_tokens_details": {"cached_tokens": 0},
                        "completion_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            async with offline_runtime(
                runtime,
                Path(directory),
                generate_key="synthetic-qwen",
                detect_key="synthetic-qwen",
                generate_transport=httpx.MockTransport(generation),
                detect_transport=httpx.MockTransport(detection),
            ) as provider:
                binding = runtime.execution_binding()
                provider.verify_execution_scope(binding)
                self.assertEqual(
                    binding["provider_models"],
                    {"generate": "qwen-image-3.0", "detect": "qwen3.8-flash"},
                )
                payload = provider.prepare_generate(
                    {
                        "type": "scene",
                        "prompt": "synthetic",
                        "output": {"width": 512, "height": 512, "format": "png"},
                        "protection_mode": "none",
                        "regions": [],
                    },
                    png,
                )
                await provider.submit_generate(uuid4(), payload)
                receipt = await provider.submit_detect(uuid4(), png)
                record = provider.fetch_facts(receipt.provider_id)
                self.assertEqual(
                    provider.validate_facts_record(record)["final_sha256"],
                    hashlib.sha256(png).hexdigest(),
                )
                foreign = copy.deepcopy(record)
                foreign["endpoint_origin"] = "https://ws-other.cn-beijing.maas.aliyuncs.com"
                from visual_platform.providers.contract import ProviderUncertain

                with self.assertRaises(ProviderUncertain):
                    provider.validate_facts_record(foreign)
                observed = provider.observation(
                    "detect",
                    {
                        "provider_id": receipt.provider_id,
                        "response_digest": receipt.response_digest,
                    },
                )
                self.assertEqual(observed["reported_model_version"], "qwen3.8-flash")
                self.assertEqual(observed["quantities"]["output_tokens"], 5)
                altered = copy.deepcopy(binding)
                altered["provider_models"]["detect"] = "gemini-3.8-flash"
                with self.assertRaises(ValueError):
                    provider.verify_execution_scope(altered)
        self.assertEqual(
            calls,
            [
                ("generate", "/api/v1/services/aigc/image-generation/generation"),
                ("detect", "/compatible-mode/v1/chat/completions"),
            ],
        )

    async def test_mixed_or_missing_transport_is_rejected_before_private_files(self):
        runtime = ProviderRuntime.model_validate(dual_document(), strict=True)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "not-created"
            with self.assertRaises(ValueError):
                async with offline_runtime(
                    runtime,
                    path,
                    generate_key="synthetic-qwen",
                    detect_key="synthetic-qwen",
                    generate_transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                    detect_transport=None,
                ):
                    self.fail("must not yield")
            self.assertFalse(path.exists())
