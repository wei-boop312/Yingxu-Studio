"""Actual isolated PG/S3 and live routing code; both supplier HTTP transports mocked."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import psycopg
from bailian_fixture import BailianFixture
from psycopg.types.json import Jsonb

from visual_platform.db.pool import open_pool
from visual_platform.providers.live_bailian import LiveRoutingWorker, live_bailian_runtime
from visual_platform.providers.runtime import ProviderRuntime
from visual_platform.services.runtime_quote import register_runtime_configuration
from visual_platform.storage.s3 import S3Storage


class LiveBailianTests(BailianFixture):
    def setUp(self):
        super().setUp()
        document = self.qwen_runtime.model_dump()
        document["schema_version"] = 3
        document["version"] += "-live"
        document["detect"] = {
            **document["generate"],
            "provider": "aliyun_bailian_vision",
            "model": "qwen3.8-flash",
        }
        self.live_runtime = ProviderRuntime.model_validate(document, strict=True)
        register_runtime_configuration(
            self.operator_dsn,
            self.live_runtime,
            self.live_runtime.digest(),
            uuid4(),
            "synthetic live routing identity",
        )
        self.live_rates = {
            "generate": {
                "qima_input_1k": "0.02",
                "qima_input_2k": "0.02",
                "qima_output_1k": "0.18",
                "qima_output_2k": "0.18",
            },
            "detect": {"input_token": "0.8", "output_token": "2.7", "input_token_cache": "0.1"},
        }
        self.client.app.state.settings = replace(self.settings, provider_profile="bailian_live")
        self.empty_detection = False

    def policy(self, enabled=True):
        with psycopg.connect(self.operator_dsn) as c:
            return c.execute(
                "SELECT ops.activate_live_provider(%s,%s,%s,%s,%s,%s,%s)",
                (
                    self.user,
                    self.live_runtime.digest(),
                    Jsonb(self.live_rates),
                    Jsonb({"generate": "a" * 64, "detect": "b" * 64}),
                    enabled,
                    uuid4(),
                    "synthetic policy, HTTP mocks only",
                ),
            ).fetchone()[0]

    def live_quote(self):
        _, body = self.source_quote()
        response = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
        return response, body

    def live_task(self):
        self.policy()
        response, body = self.live_quote()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["provider_profile"], "bailian_live")
        self.assertEqual(response.json()["supplier_reference_upper_cny"], "1.01556480")
        task = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": response.json()["quote_id"]},
            headers={
                **self.write_headers(),
                "idempotency-key": str(uuid4()),
                "X-Provider-Confirmation": "bailian_live",
            },
        )
        self.assertEqual(task.status_code, 201, task.text)
        return task.json()["id"]

    def qwen_route(self, request):
        if request.url.host == "dashscope-a717.oss-accelerate.aliyuncs.com":
            self.calls.append(("live-download", request.method, request.url.host))
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=self.qwen_png, headers={"content-type": "image/png"})
        response = super().qwen_route(request)
        if request.method == "GET" and response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            document = response.json()
            if document.get("output", {}).get("task_status") == "SUCCEEDED":
                document["output"]["choices"][0]["message"]["content"][0]["image"] = (
                    "https://dashscope-a717.oss-accelerate.aliyuncs.com/synthetic.png?fixture=1"
                )
                return httpx.Response(200, json=document)
        return response

    def live_vision_route(self, request):
        self.calls.append(("live-vision", request.method, request.url.host))
        payload = json.loads(request.content)
        self.assertFalse(payload["enable_thinking"])
        self.assertEqual(payload["model"], "qwen3.8-flash")
        self.assertNotIn("tools", payload)
        facts = {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 512,
            "image_height": 512,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "white", "confidence": 0.95},
            "product_extent": None,
            "uncertainties": [],
        }
        return httpx.Response(
            200,
            json={
                "model": "qwen3.8-flash",
                "id": "chatcmpl-synthetic-live",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "[]" if self.empty_detection else json.dumps(facts),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    def run_live(self, task):
        async def run():
            generation = httpx.MockTransport(self.qwen_route)
            vision = httpx.MockTransport(self.live_vision_route)
            # Only the low-level wire factory is replaced; production still explicitly
            # selects live adapters. This test cannot make a real supplier request.
            with (
                patch("httpx.HTTPTransport", return_value=generation),
                patch("httpx.AsyncHTTPTransport", return_value=vision),
            ):
                async with live_bailian_runtime(
                    self.live_runtime, Path(self.folder.name) / "live", key="offline-bailian"
                ) as provider:
                    pool = await open_pool(self.worker_dsn, "app_worker")
                    storage = S3Storage(self.settings)
                    try:
                        return await LiveRoutingWorker(
                            pool, storage, provider, Path(self.folder.name) / "fake-cache"
                        ).run(UUID(task))
                    finally:
                        await pool.close()
                        storage.close()

        return asyncio.run(run())

    def test_live_http_quote_requires_separate_policy(self):
        # The existing source-quote helper uses this app: without a policy even
        # the first attempted quote must be denied before an intent exists.
        response = self.client.post(
            "/api/v1/quotes",
            json={
                "product_id": self.product,
                "source_asset_version_id": self.version,
                "generation": {
                    "type": "scene",
                    "prompt": "synthetic",
                    "output": {"width": 512, "height": 512, "format": "png"},
                    "protection_mode": "none",
                    "regions": [],
                },
                "targets": [
                    {
                        "platform": "amazon",
                        "marketplace": "US",
                        "category": "generic",
                        "image_type": "MAIN",
                    }
                ],
            },
            headers=self.write_headers(),
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "live_provider_not_authorized")
        self.assertEqual(self.calls, [])

    def test_live_complete_archives_real_profile_and_exact_cost_without_mock_budget(self):
        task = self.live_task()
        self.assertEqual(self.run_live(task), "succeeded")
        with psycopg.connect(self.owner) as c:
            outbox = c.execute(
                "SELECT id FROM ops.outbox WHERE task_id=%s AND event_type<>'task.manual' LIMIT 1",
                (task,),
            ).fetchone()[0]
        with psycopg.connect(self.worker_dsn) as c:
            self.assertFalse(
                c.execute("SELECT ops.validate_delivery(%s,%s)", (outbox, task)).fetchone()[0]
            )
            self.assertTrue(
                c.execute(
                    "SELECT ops.validate_delivery(%s,%s,%s)", (outbox, task, "bailian_live")
                ).fetchone()[0]
            )
        calls = len(self.calls)
        self.assertEqual(self.run_live(task), "not_claimed")
        self.assertEqual(len(self.calls), calls)
        with psycopg.connect(self.owner) as c:
            providers = c.execute(
                "SELECT provider FROM app.provider_attempts WHERE task_id=%s ORDER BY step", (task,)
            ).fetchall()
            self.assertEqual({r[0] for r in providers}, {"bailian_live", "bailian_vision_live"})
            archive = c.execute(
                "SELECT representation_version,document->>'transport' FROM ops.detection_archives WHERE task_id=%s",
                (task,),
            ).fetchone()
            self.assertEqual(archive, (3, "bailian_live"))
            costs = c.execute(
                "SELECT provider,amount::text,currency FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='estimate' ORDER BY provider",
                (task,),
            ).fetchall()
            self.assertEqual(
                [(r[0], r[2]) for r in costs],
                [("bailian_live", "CNY"), ("bailian_vision_live", "CNY")],
            )
            from decimal import Decimal

            self.assertEqual(
                [Decimal(r[1]) for r in costs], [Decimal("0.20"), Decimal("0.0000291")]
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM ops.provider_budget_holds h JOIN app.provider_attempts a ON a.id=h.attempt_id WHERE a.task_id=%s",
                    (task,),
                ).fetchone()[0],
                0,
            )

    def test_revocation_after_quote_prevents_task_and_any_supplier_post(self):
        self.policy()
        quote, body = self.live_quote()
        self.assertEqual(quote.status_code, 201, quote.text)
        self.policy(False)
        task = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": quote.json()["quote_id"]},
            headers={
                **self.write_headers(),
                "idempotency-key": str(uuid4()),
                "X-Provider-Confirmation": "bailian_live",
            },
        )
        self.assertEqual(task.status_code, 409, task.text)
        self.assertEqual(self.calls, [])

    def test_live_unknown_generation_has_one_post_and_never_resubmits(self):
        task = self.live_task()
        self.timeout_step = "generate"
        self.assertEqual(self.run_live(task), "stopped")
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT status,error_code FROM app.generation_tasks WHERE id=%s", (task,)
                ).fetchone(),
                ("uncertain", "submission_response_timeout"),
            )
        self.assertEqual(self.run_live(task), "not_claimed")
        self.assertEqual(sum(method == "POST" for _, method, _ in self.calls), 1)

    def test_live_empty_array_stays_manual_and_restore_revokes_live_authority(self):
        task = self.live_task()
        self.empty_detection = True
        self.assertEqual(self.run_live(task), "succeeded")
        with psycopg.connect(self.owner) as c:
            facts = c.execute(
                "SELECT document->'facts',document->'manual_review_required' FROM ops.detection_archives WHERE task_id=%s",
                (task,),
            ).fetchone()
            self.assertFalse(facts[0]["analysis_complete"])
            self.assertTrue(facts[1])
            self.assertGreaterEqual(
                c.execute("SELECT ops.revoke_restored_live_policies()").fetchone()[0], 1
            )
            self.assertFalse(
                c.execute("SELECT ops.live_policy(%s)->'enabled'", (self.user,)).fetchone()[0]
            )

    def test_old_browser_cannot_silently_submit_live_quote_without_confirmation(self):
        self.policy()
        quote, body = self.live_quote()
        self.assertEqual(quote.status_code, 201, quote.text)
        response = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": quote.json()["quote_id"]},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "live_provider_confirmation_required")
        self.assertEqual(self.calls, [])

    def test_operator_can_disable_without_new_catalogue_and_operation_is_idempotent(self):
        self.policy()
        operation = uuid4()
        with psycopg.connect(self.operator_dsn) as c:
            first = c.execute(
                "SELECT ops.deactivate_live_provider(%s,%s,%s)",
                (self.user, operation, "synthetic disable"),
            ).fetchone()[0]
            replay = c.execute(
                "SELECT ops.deactivate_live_provider(%s,%s,%s)",
                (self.user, operation, "synthetic disable"),
            ).fetchone()[0]
        self.assertEqual(first, replay)
        with psycopg.connect(self.owner) as c:
            self.assertFalse(
                c.execute("SELECT ops.live_policy(%s)->'enabled'", (self.user,)).fetchone()[0]
            )
        self.assertEqual(self.calls, [])
