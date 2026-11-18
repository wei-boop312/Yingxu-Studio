"""Real PG/S3 + Worker + official SDK, suppliers remain offline HTTP mocks."""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import httpx
import psycopg
from PIL import Image
from test_offline_pipeline import PipelineFixture

from visual_platform.db.pool import open_pool
from visual_platform.domain.schemas import QuoteRequest, canonical_hash
from visual_platform.providers.bfl import BflProvider
from visual_platform.providers.gemini import OFFICIAL_ORIGIN, GeminiProvider
from visual_platform.providers.pipeline import ProtocolProvider
from visual_platform.services.quotes import hash_data
from visual_platform.storage.s3 import S3Storage, StorageError
from visual_platform.worker.runner import Worker


class ProtocolPipelineFixture(PipelineFixture):
    def setUp(self):
        super().setUp()
        stream = BytesIO()
        Image.new("RGB", (256, 256), (201, 15, 35)).save(stream, "PNG")
        self.source = stream.getvalue()
        self.version = self.complete(self.upload(self.source)).json()["version_id"]
        self.calls = []
        self.pending = False
        self.failure = False
        self.timeout_step = None
        self.detected = None
        stream = BytesIO()
        Image.new("RGB", (256, 256), (35, 45, 200)).save(stream, "JPEG")
        self.jpeg = stream.getvalue()

    def submit(self, before_task=None, business_updates=None, execution_updates=None):
        body = {
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "generation": {
                "type": "scene",
                "prompt": "offline protocol scene",
                "output": {"width": 256, "height": 256, "format": "png"},
                "protection_mode": "pixel_exact",
                "regions": [
                    {
                        "source_x": 0,
                        "source_y": 0,
                        "width": 5,
                        "height": 6,
                        "target_x": 8,
                        "target_y": 7,
                    }
                ],
            },
            "targets": [
                {
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "generic",
                    "image_type": "MAIN",
                }
            ],
        }
        body.update(business_updates or {})
        quoted = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
        self.assertEqual(quoted.status_code, 201, quoted.text)
        # Explicit trusted-server fixture profile; HTTP clients cannot supply _execution.
        with psycopg.connect(self.owner) as c:
            snapshot = c.execute(
                "SELECT input_snapshot FROM app.quotes WHERE id=%s", (quoted.json()["quote_id"],)
            ).fetchone()[0]
            snapshot["_execution"].update(
                provider_profile="offline_protocol_mock",
                provider_model="flux-3-image-protocol-mock",
                price_version="trial-protocol-mock-v1",
            )
            snapshot["_execution"].update(execution_updates or {})
            quote = uuid4()
            c.execute("SET LOCAL ROLE app_api")
            c.execute("SELECT set_config('app.user_id',%s,true)", (str(self.user),))
            c.execute(
                "SELECT app.create_quote(%s,%s,%s,%s::jsonb,%s::jsonb)",
                (
                    quote,
                    canonical_hash(QuoteRequest.model_validate(body)),
                    hash_data(snapshot),
                    json.dumps(snapshot),
                    json.dumps(snapshot["_execution"]["rules"]),
                ),
            )
        self.last_quote = quote
        self.last_snapshot = snapshot
        if before_task is not None:
            before_task(quote, snapshot)
        result = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": str(quote)},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["id"]

    def bfl_route(self, request):
        self.calls.append(("bfl", request.method, str(request.url)))
        if request.method == "POST":
            if self.timeout_step == "generate":
                raise httpx.ReadTimeout("offline injected response loss")
            payload = json.loads(request.content)
            self.sent_prompt = payload["prompt"]
            self.sent_source = base64.b64decode(payload["images"][0])
            self.request_digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            return httpx.Response(
                200,
                json={
                    "id": "known-result",
                    "polling_url": "https://api.eu2.bfl.ai/v1/get_result?id=known-result",
                    "cost": 0.06,
                },
            )
        if request.url.host.startswith("delivery"):
            self.assertNotIn("x-key", request.headers)
            return httpx.Response(200, content=self.jpeg, headers={"content-type": "image/jpeg"})
        if self.pending:
            return httpx.Response(200, json={"status": "Pending"})
        if self.failure:
            return httpx.Response(200, json={"status": "Failed"})
        return httpx.Response(
            200,
            json={"status": "Ready", "result": {"sample": "https://delivery.eu2.bfl.ai/result"}},
        )

    def gemini_route(self, request):
        self.calls.append(("gemini", request.method, str(request.url)))
        if self.timeout_step == "detect":
            raise httpx.ReadTimeout("offline detect response loss")
        body = json.loads(request.content)
        self.detected = base64.urlsafe_b64decode(
            body["contents"][0]["parts"][1]["inlineData"]["data"]
        )
        facts = {
            "schema_version": 1,
            "analysis_complete": True,
            "image_width": 256,
            "image_height": 256,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "non_white", "confidence": 0.9},
            "product_extent": None,
            "uncertainties": [],
        }
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": json.dumps(facts)}]},
                        "finishReason": "STOP",
                    }
                ],
                "responseId": "detector-result",
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 20,
                    "totalTokenCount": 30,
                },
            },
        )

    def run_protocol(
        self,
        task,
        storage_class=S3Storage,
        *,
        gemini_origin=OFFICIAL_ORIGIN,
        worker_class=Worker,
        merchant_accounts=None,
    ):
        async def run():
            bfl = BflProvider(
                "offline-key",
                Path(self.folder.name) / "bfl",
                transport=httpx.MockTransport(self.bfl_route),
            )
            gemini = GeminiProvider(
                "offline-key",
                Path(self.folder.name) / "gemini",
                transport=httpx.MockTransport(self.gemini_route),
                base_url=gemini_origin,
            )
            provider = ProtocolProvider(bfl, gemini, merchant_accounts=merchant_accounts)
            pool = await open_pool(self.worker_dsn, "app_worker")
            storage = storage_class(self.settings)
            try:
                return await worker_class(pool, storage, provider).run(task)
            finally:
                await pool.close()
                storage.close()
                bfl.close()
                await gemini.aclose()

        return asyncio.run(run())

    def post_count(self, supplier):
        return sum(name == supplier and method == "POST" for name, method, _ in self.calls)

    def assert_response_loss(self, step):
        self.timeout_step = step
        task = self.submit()
        supplier = "bfl" if step == "generate" else "gemini"
        before = self.post_count(supplier)
        self.assertEqual(self.run_protocol(task), "stopped")
        self.assertEqual(self.run_protocol(task), "not_claimed")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")
        self.assertEqual(self.post_count(supplier) - before, 1)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type IN ('charge','refund')",
                    (task,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_record_kind='intent_observation'",
                    (task,),
                ).fetchone()[0],
                1 if step == "generate" else 2,
            )


class ProtocolPipelineTests(ProtocolPipelineFixture):
    def test_gateway_change_without_frozen_endpoint_rejects_before_intent_or_post(self):
        task = self.submit()
        self.assertEqual(self.run_protocol(task, gemini_origin="https://hubway.cc"), "stopped")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (0, 0))
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (100, 0))

    def test_gateway_change_after_known_receipt_keeps_unknown_without_new_post(self):
        self.pending = True
        task = self.submit()
        self.assertEqual(self.run_protocol(task), "retryable")
        self.assertEqual(self.run_protocol(task, gemini_origin="https://hubway.cc"), "stopped")
        self.assertEqual((self.post_count("bfl"), self.post_count("gemini")), (1, 0))
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (84, 16))

    def test_generation_uses_frozen_appearance_after_profile_edit(self):
        endpoint = "/api/v1/products/" + self.product + "/appearances"
        original = {"description": "original label", "colors": ["red"], "materials": ["steel"]}
        created = self.client.post(
            endpoint,
            json={"name": "private profile name", "config": original},
            headers=self.write_headers(),
        )
        self.assertEqual(created.status_code, 201, created.text)
        identity = created.json()["id"]

        def edit_after_quote(quote, snapshot):
            self.assertEqual(snapshot["_execution"]["appearance"]["config"], original)
            changed = self.client.patch(
                endpoint + "/" + identity,
                json={"name": "edited profile", "config": {"description": "NEW APPEARANCE"}},
                headers={**self.write_headers(), "if-match": "1"},
            )
            self.assertEqual(changed.status_code, 200, changed.text)

        task = self.submit(edit_after_quote, {"appearance_profile_id": identity})
        archived = self.client.delete(
            endpoint + "/" + identity,
            headers={**self.write_headers(), "if-match": "2"},
        )
        self.assertEqual(archived.status_code, 204, archived.text)
        self.assertEqual(self.run_protocol(task), "succeeded")
        frozen = json.loads(self.sent_prompt.split("instructions):\n", 1)[1])
        self.assertEqual(frozen, original)
        self.assertNotIn(identity, self.sent_prompt)
        self.assertNotIn("private profile name", self.sent_prompt)
        self.assertNotIn("NEW APPEARANCE", self.sent_prompt)
        self.assertEqual(self.post_count("bfl"), 1)

    def test_full_protocol_worker_final_png_cost_observations_and_single_charge(self):
        task = self.submit()
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual(self.run_protocol(task), "not_claimed")
        self.assertEqual(self.post_count("bfl"), 1)
        self.assertEqual(self.post_count("gemini"), 1)
        self.assertEqual(self.sent_source, self.source)
        final = httpx.get(
            self.client.get("/api/v1/tasks/" + task + "/download", follow_redirects=False).headers[
                "location"
            ]
        ).content
        self.assertEqual(self.detected, final)
        with Image.open(BytesIO(final)) as image, Image.open(BytesIO(self.source)) as source:
            self.assertEqual(
                image.crop((8, 7, 13, 13)).tobytes(), source.crop((0, 0, 5, 6)).tobytes()
            )
        report = httpx.get(
            self.client.get(
                "/api/v1/tasks/" + task + "/report/download", follow_redirects=False
            ).headers["location"]
        ).json()
        self.assertEqual(report["provider_profile"], "offline_protocol_mock")
        self.assertEqual(report["final_sha256"], hashlib.sha256(final).hexdigest())
        self.assertTrue(report["pixel_protection"]["pixel_exact_passed"])
        self.assertEqual(report["auto_overall"], "manual_review")
        with psycopg.connect(self.owner) as c:
            rows = c.execute(
                "SELECT provider,cost_record_kind,cost_status,amount,currency,usage FROM app.task_cost_records WHERE task_id=%s",
                (task,),
            ).fetchall()
            self.assertEqual(len(rows), 4)
            self.assertTrue(
                all(row[2] == "unknown" and row[3] is None and row[4] is None for row in rows)
            )
            self.assertEqual({row[0] for row in rows}, {"bfl_mock", "gemini_mock"})
            bfl = next(
                row for row in rows if row[0] == "bfl_mock" and row[1] == "response_observation"
            )
            self.assertEqual(bfl[5]["provider_cost"]["quantity"], "0.06")
            gemini = next(
                row for row in rows if row[0] == "gemini_mock" and row[1] == "response_observation"
            )
            self.assertEqual(gemini[5]["usage"]["total_token_count"], 30)
            self.assertEqual(
                c.execute(
                    "SELECT request_hash FROM app.provider_attempts WHERE task_id=%s AND step='generate'",
                    (task,),
                ).fetchone()[0],
                self.request_digest,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            pg_version = c.execute("SHOW server_version").fetchone()[0]
        from importlib.metadata import version

        evidence = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "profile": "offline_protocol_mock",
            "suppliers": "httpx.MockTransport; official Gemini SDK",
            "postgres": pg_version,
            "sdk_version": version("google-genai"),
            "storage": "native private S3-compatible service",
            "paid_calls": 0,
            "source_sha256": hashlib.sha256(self.sent_source).hexdigest(),
            "final_sha256": hashlib.sha256(final).hexdigest(),
            "bfl_posts": self.post_count("bfl"),
            "gemini_posts": self.post_count("gemini"),
            "gemini_received_exact_final_png": self.detected == final,
            "protected_rgb_exact": True,
            "report_profile": report["provider_profile"],
            "report_sha_matches_final": report["final_sha256"] == hashlib.sha256(final).hexdigest(),
            "cost_observation_records": len(rows),
            "money_amount_and_currency": None,
            "user_credit_charges": 1,
            "request_digest_matches_db": True,
            "result": "PASS",
        }
        Path("docs/evidence/protocol-pipeline.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )

    def test_pending_known_receipt_and_private_metadata_loss_only_query_again(self):
        self.pending = True
        task = self.submit()
        self.assertEqual(self.run_protocol(task), "retryable")
        self.assertEqual(self.post_count("bfl"), 1)
        self.assertEqual(self.post_count("gemini"), 0)
        self.pending = False
        # Durable DB receipt remains authoritative for query; local BFL metadata is optional.
        for path in (Path(self.folder.name) / "bfl").glob("*.receipt.json"):
            path.unlink()
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual(self.post_count("bfl"), 1)
        self.assertEqual(self.post_count("gemini"), 1)

    def test_generate_response_loss_keeps_frozen_and_never_reposts(self):
        self.assert_response_loss("generate")

    def test_detection_response_loss_keeps_frozen_and_never_reposts(self):
        self.assert_response_loss("detect")

    def test_terminal_poll_failure_refunds_without_detection(self):
        self.failure = True
        task = self.submit()
        self.assertEqual(self.run_protocol(task), "stopped")
        self.assertEqual(self.run_protocol(task), "not_claimed")
        self.assertEqual(self.post_count("gemini"), 0)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='refund'",
                    (task,),
                ).fetchone()[0],
                1,
            )

    def test_wrong_provider_cannot_run_locked_quote(self):
        task = self.submit()
        before = self.client.get("/api/v1/tasks/" + task).json()
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual(self.run_worker(task), "not_claimed")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json(), before)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json(), balance)
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.provider_attempts WHERE task_id=%s", (task,)
                ).fetchone()[0],
                0,
            )

    def test_archive_failure_restarts_without_generate_resubmit(self):
        class BrokenStorage(S3Storage):
            def put_immutable(self, *args, **kwargs):
                raise StorageError("offline archive failure")

        task = self.submit()
        self.assertEqual(self.run_protocol(task, BrokenStorage), "retryable")
        self.assertEqual(self.post_count("bfl"), 1)
        self.assertEqual(self.post_count("gemini"), 0)
        self.assertEqual(self.run_protocol(task), "succeeded")
        self.assertEqual(self.post_count("bfl"), 1)
        self.assertEqual(self.post_count("gemini"), 1)

    def test_wrong_generated_dimensions_quarantined_before_detection(self):
        stream = BytesIO()
        Image.new("RGB", (128, 128), "blue").save(stream, "JPEG")
        self.jpeg = stream.getvalue()
        task = self.submit()
        self.assertEqual(self.run_protocol(task), "stopped")
        self.assertEqual(self.post_count("gemini"), 0)
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")

    def test_observation_fencing_permissions_and_append_only(self):
        task = self.submit()

        async def exercise():
            pool = await open_pool(self.worker_dsn, "app_worker")
            worker = Worker(pool, None, None, owner="observation-contract")
            try:
                claim = json.loads(
                    await worker.db(
                        "SELECT ops.claim_task($1,$2,'offline_protocol_mock')",
                        UUID(task),
                        worker.owner,
                    )
                )
                fence = claim["fencing_token"]
                attempt = await worker.db(
                    "SELECT app.record_intent($1,$2,$3,$4,$5)",
                    UUID(task),
                    worker.owner,
                    fence,
                    "generate",
                    "a" * 64,
                )
                query = "SELECT app.record_protocol_observation($1,$2,$3,$4,$5,$6::jsonb)"
                args = (UUID(task), worker.owner, fence, attempt, "intent", '{"state":"unknown"}')
                await worker.db(query, *args)
                await worker.db(query, *args)
                with self.assertRaises(asyncpg.RaiseError) as conflict:
                    await worker.db(query, *args[:-1], '{"state":"changed"}')
                self.assertIn("observation_conflict", str(conflict.exception))
                with self.assertRaises(asyncpg.ObjectNotInPrerequisiteStateError):
                    await worker.db(
                        query, UUID(task), worker.owner, fence + 1, attempt, "intent", "{}"
                    )
                with self.assertRaises(asyncpg.RaiseError) as mismatch:
                    await worker.db(
                        query,
                        UUID(task),
                        worker.owner,
                        fence,
                        attempt,
                        "response",
                        '{"response_digest":"wrong"}',
                    )
                self.assertIn("receipt_observation_mismatch", str(mismatch.exception))
                await worker.db(
                    "SELECT app.fail_task($1,$2,$3,$4,true)",
                    UUID(task),
                    worker.owner,
                    fence,
                    "controlled_observation_fixture",
                )
            finally:
                await pool.close()

        asyncio.run(exercise())
        with psycopg.connect(self.settings.api_database_url) as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute(
                    "SELECT app.record_protocol_observation(%s,%s,%s,%s,%s,%s::jsonb)",
                    (task, "denied", 1, uuid4(), "intent", "{}"),
                )
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s", (task,)
                ).fetchone()[0],
                1,
            )
            with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("UPDATE app.task_cost_records SET usage='{}' WHERE task_id=%s", (task,))
