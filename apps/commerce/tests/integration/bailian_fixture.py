"""Real PG/S3 fixture with only synthetic Qwen and Gemini HTTP transports; no test cases."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from PIL import Image
from test_provider_runtime import RuntimeFixture

from visual_platform.db.pool import open_pool
from visual_platform.providers.authorization import (
    BailianSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.providers.runtime import (
    ProviderRuntime,
    credential_fingerprint,
    offline_runtime,
)
from visual_platform.services.provider_budget import provider_quote_review, register_provider_budget
from visual_platform.services.runtime_quote import register_runtime_configuration
from visual_platform.storage.s3 import S3Storage
from visual_platform.worker.runner import Worker


class BailianFixture(RuntimeFixture):
    runtime_output_edge = 512

    def setUp(self):
        super().setUp()
        stream = BytesIO()
        Image.new("RGB", (512, 512), (35, 45, 200)).save(stream, "PNG")
        self.qwen_png = stream.getvalue()
        document = self.runtime().model_dump()
        document["schema_version"] = 2
        document["generate"].update(
            provider="aliyun_bailian",
            model="qwen-image-3.0",
            endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
            credential_fingerprint=credential_fingerprint("offline-bailian"),
        )
        self.qwen_runtime = ProviderRuntime.model_validate(document, strict=True)
        register_runtime_configuration(
            self.operator_dsn,
            self.qwen_runtime,
            self.qwen_runtime.digest(),
            uuid4(),
            "synthetic Qwen identity",
        )

    def qwen_route(self, request):
        self.calls.append(("bailian", request.method, str(request.url)))
        if request.method == "POST":
            if self.timeout_step == "generate":
                raise httpx.ReadTimeout("synthetic Qwen response loss")
            self.qwen_payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={"output": {"task_id": "synthetic-bailian-task", "task_status": "PENDING"}},
            )
        if request.url.host.startswith("dashscope-result-"):
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=self.qwen_png, headers={"content-type": "image/png"})
        if self.pending:
            return httpx.Response(
                200,
                json={"output": {"task_id": "synthetic-bailian-task", "task_status": "RUNNING"}},
            )
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_id": "synthetic-bailian-task",
                    "task_status": "SUCCEEDED",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "image": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/synthetic.png?Expires=1"
                                    }
                                ],
                            },
                        }
                    ],
                },
                "usage": {
                    "output_width": 512,
                    "output_height": 512,
                    "input_image_count": 1,
                    "output_image_count": 1,
                    "input_image_type": "qima_input_1k",
                    "output_image_type": "qima_output_1k",
                },
            },
        )

    def qwen_detector(self, request):
        response = self.gemini_route(request)
        body = response.json()
        part = body["candidates"][0]["content"]["parts"][0]
        facts = json.loads(part["text"])
        facts.update(image_width=512, image_height=512)
        part["text"] = json.dumps(facts)
        return httpx.Response(200, json=body)

    def qwen_review(self):
        source, body = self.source_quote()
        prepared = self.prepare(source, self.qwen_runtime)
        quote = UUID(prepared["quote_id"])
        review = provider_quote_review(self.operator_dsn, self.user, quote)
        return quote, body, review

    def bind_qwen(self, quote, snapshot):
        now = datetime.now(timezone.utc)
        self.qwen_scope_data = {
            "schema_version": 4,
            "authorization_id": str(uuid4()),
            "user_id": str(self.user),
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "authorized_by": "synthetic offline rehearsal only",
            "authorized_at": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "source_path": "synthetic.png",
            "source_sha256": snapshot["_execution"]["source_sha256"],
            "quote_input_sha256": hashlib.sha256(
                json.dumps(
                    snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            "generation": snapshot["generation"],
            "targets": snapshot["targets"],
            "step_budgets": {
                "generate": {"currency": "CNY", "ceiling": "1"},
                "detect": {"currency": "USD", "ceiling": "0.1"},
            },
            "currency_totals": [
                {"currency": "CNY", "ceiling": "1"},
                {"currency": "USD", "ceiling": "0.1"},
            ],
        }
        scope = BailianSmokeAuthorization.model_validate_json(json.dumps(self.qwen_scope_data))
        self.qwen_scope_data = scope.model_dump(mode="json")
        digest = hashlib.sha256(canonical_authorization(scope)).hexdigest()
        self.budget = register_provider_budget(
            self.operator_dsn, quote, scope, digest, uuid4(), "synthetic unit-separated budget"
        )

    def qwen_task(self, *, with_budget=True):
        quote, body, review = self.qwen_review()
        if with_budget:
            self.bind_qwen(quote, review["input_snapshot"])
        response = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": str(quote)},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def run_qwen(self, task, storage_class=S3Storage):
        async def run():
            async with offline_runtime(
                self.qwen_runtime,
                Path(self.folder.name) / "qwen-runtime",
                generate_key="offline-bailian",
                detect_key="offline-hubway",
                generate_transport=httpx.MockTransport(self.qwen_route),
                detect_transport=httpx.MockTransport(self.qwen_detector),
            ) as provider:
                pool = await open_pool(self.worker_dsn, "app_worker")
                storage = storage_class(self.settings)
                try:
                    return await Worker(pool, storage, provider).run(task)
                finally:
                    storage.close()
                    await pool.close()

        return asyncio.run(run())
