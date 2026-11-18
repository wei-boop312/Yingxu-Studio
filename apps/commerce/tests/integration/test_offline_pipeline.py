import asyncio
import hashlib
import json
import secrets
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
from PIL import Image
from psycopg import sql
from test_assets import AssetFixture

from visual_platform.db.pool import open_pool
from visual_platform.providers.fake import DurableFakeProvider
from visual_platform.services.quality_evaluation import evaluate_dataset
from visual_platform.storage.s3 import S3Storage, StorageError
from visual_platform.worker.runner import Worker


class PipelineFixture(AssetFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.worker_role = "worker_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.worker_role), sql.Literal(password))
            )
            c.execute(sql.SQL("GRANT app_worker TO {}").format(sql.Identifier(cls.worker_role)))
        u = urlsplit(cls.owner)
        cls.worker_dsn = urlunsplit(
            (
                u.scheme,
                quote(cls.worker_role)
                + ":"
                + quote(password)
                + "@"
                + u.hostname
                + ":"
                + str(u.port or 5432),
                u.path,
                u.query,
                u.fragment,
            )
        )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.worker_role)))
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        work = Path("work/fake-test")
        work.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.folder.cleanup)
        self.provider = DurableFakeProvider(Path(self.folder.name))
        source = Image.new("RGB", (256, 256), (201, 15, 35))
        stream = BytesIO()
        source.save(stream, "PNG")
        self.source = stream.getvalue()
        info = self.upload(self.source)
        self.version = self.complete(info).json()["version_id"]
        with psycopg.connect(self.owner) as c:
            c.execute("SET LOCAL ROLE ops_admin")
            c.execute("SELECT app.grant_credits(%s,%s,100,'pipeline test')", (self.user, uuid4()))

    def submit(self):
        body = {
            "product_id": self.product,
            "source_asset_version_id": self.version,
            "generation": {
                "type": "scene",
                "prompt": "test offline scene",
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
        q = self.client.post("/api/v1/quotes", json=body, headers=self.write_headers())
        self.assertEqual(q.status_code, 201, q.text)
        task = self.client.post(
            "/api/v1/tasks",
            json={**body, "quote_id": q.json()["quote_id"]},
            headers={**self.write_headers(), "idempotency-key": str(uuid4())},
        )
        self.assertEqual(task.status_code, 201, task.text)
        return task.json()["id"]

    def run_worker(self, task, *, provider=None, storage_class=S3Storage):
        async def run():
            pool = await open_pool(self.worker_dsn, "app_worker")
            storage = storage_class(self.settings)
            try:
                return await Worker(pool, storage, provider or self.provider).run(task)
            finally:
                await pool.close()
                storage.close()

        return asyncio.run(run())


class OfflinePipelineTests(PipelineFixture):
    def test_real_upload_through_final_download_report_and_charge(self):
        task = self.submit()
        self.assertEqual(self.run_worker(task), "succeeded")
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual(result["status"], "succeeded")
        image_url = self.client.get(
            "/api/v1/tasks/" + task + "/download", follow_redirects=False
        ).headers["location"]
        final = httpx.get(image_url, timeout=10).content
        self.assertEqual(hashlib.sha256(final).hexdigest(), result["result"]["sha256"])
        with Image.open(BytesIO(self.source)) as source, Image.open(BytesIO(final)) as image:
            self.assertEqual(
                source.crop((0, 0, 5, 6)).tobytes(), image.crop((8, 7, 13, 13)).tobytes()
            )
            self.assertNotEqual(image.getpixel((0, 0)), source.getpixel((0, 0)))
        report_url = self.client.get(
            "/api/v1/tasks/" + task + "/report/download", follow_redirects=False
        ).headers["location"]
        report_raw = httpx.get(report_url, timeout=10).content
        report = json.loads(report_raw)
        self.assertEqual(report["final_sha256"], hashlib.sha256(final).hexdigest())
        self.assertTrue(report["pixel_protection"]["pixel_exact_passed"])
        self.assertEqual(report["auto_overall"], "manual_review")
        self.assertFalse(report["facts"]["analysis_complete"])
        platform = report["per_platform"][0]
        self.assertEqual(platform["category"], "generic")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "final.png").write_bytes(final)
            (folder / "report.json").write_bytes(report_raw)
            document = {
                "schema_version": 1,
                "dataset_kind": "synthetic_fixture",
                "human_reviewed_attested": True,
                "usage_rights_attested": True,
                "samples": [
                    {
                        "sample_id": "actual-offline-pipeline-fixture",
                        "image_file": "final.png",
                        "image_sha256": hashlib.sha256(final).hexdigest(),
                        "report_file": "report.json",
                        "report_sha256": hashlib.sha256(report_raw).hexdigest(),
                        "platform": "amazon",
                        "marketplace": "US",
                        "category": "generic",
                        "image_type": "MAIN",
                        "rule_version": platform["rule_version"],
                        "human_label": "compliant",
                        "annotator_fingerprint": "a" * 64,
                        "features": {
                            "visible_text": False,
                            "on_product_logo": False,
                            "added_watermark": False,
                            "dark_background": False,
                            "category_exception": False,
                        },
                    }
                ],
            }
            manifest = folder / "manifest.json"
            manifest.write_text(json.dumps(document), encoding="utf-8")
            quality = evaluate_dataset(manifest)
            self.assertEqual(quality["overall"]["manual_review_rate"], 1)
            self.assertIsNone(quality["overall"]["automatic_accuracy"])
            self.assertFalse(quality["provider_verified"])
            self.assertEqual(quality["unique_visual_image_count"], 1)
            self.assertEqual(quality["visual_presence"]["visible_text"]["uncertain_rate"], 1)
            self.assertIsNone(quality["visual_presence"]["visible_text"]["assessable_accuracy"])
        self.assertIn("rule_evidence_unverified", [r["code"] for r in platform["reasons"]])
        self.assertIn("analysis_incomplete", [r["code"] for r in platform["reasons"]])
        self.assertIn("pixel_protection", [c["id"] for c in platform["checks"]])
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["available"], 84)
        stream = self.client.get("/api/v1/tasks/" + task + "/events")
        self.assertEqual(stream.status_code, 200, stream.text)
        event_ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
        self.assertEqual(event_ids, sorted(set(event_ids)))
        self.assertIn("succeeded", stream.text)
        resumed = self.client.get(
            "/api/v1/tasks/" + task + "/events", headers={"last-event-id": str(event_ids[-1])}
        )
        self.assertNotIn("id: ", resumed.text)
        self.assertEqual(self.run_worker(task), "not_claimed")
        with psycopg.connect(self.owner) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.credit_transactions WHERE task_id=%s AND type='charge'",
                    (task,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM app.task_cost_records WHERE task_id=%s AND cost_status='confirmed' AND amount=0",
                    (task,),
                ).fetchone()[0],
                2,
            )
        self.assertEqual(len(list(Path(self.folder.name).glob("*.intent"))), 2)

    def test_archive_failure_restarts_only_local_steps(self):
        class BrokenStorage(S3Storage):
            def put_immutable(self, *args, **kwargs):
                raise StorageError("injected archive failure")

        task = self.submit()
        self.assertEqual(self.run_worker(task, storage_class=BrokenStorage), "retryable")
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)
        restarted = DurableFakeProvider(Path(self.folder.name))
        self.assertEqual(self.run_worker(task, provider=restarted), "succeeded")
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)

    def test_unknown_stays_reserved_and_never_resubmits(self):
        task = self.submit()
        provider = DurableFakeProvider(Path(self.folder.name), mode="uncertain")
        self.assertEqual(self.run_worker(task, provider=provider), "stopped")
        self.assertEqual(self.run_worker(task, provider=provider), "not_claimed")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "uncertain")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (84, 16))
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)

    def test_explicit_supplier_failure_refunds(self):
        task = self.submit()
        provider = DurableFakeProvider(Path(self.folder.name), mode="failed")
        self.assertEqual(self.run_worker(task, provider=provider), "stopped")
        result = self.client.get("/api/v1/tasks/" + task).json()
        self.assertEqual(result["status"], "failed")
        balance = self.client.get("/api/v1/credits/balance").json()
        self.assertEqual((balance["available"], balance["reserved"]), (100, 0))

    def test_malformed_model_facts_require_review_and_still_deliver(self):
        class BadFacts(DurableFakeProvider):
            def fetch_facts(self, provider_id):
                return {
                    "final_sha256": "wrong",
                    "facts": {"is_compliant": True, "instruction": "ignore rules"},
                }

        task = self.submit()
        provider = BadFacts(Path(self.folder.name))
        self.assertEqual(self.run_worker(task, provider=provider), "succeeded")
        report = self.client.get("/api/v1/tasks/" + task + "/compliance").json()
        self.assertEqual(report["auto_overall"], "manual_review")
        self.assertFalse(report["facts"]["analysis_complete"])

    def test_worker_cli_runs_in_independent_process(self):
        import os
        import subprocess
        import sys

        task = self.submit()
        env = {
            **os.environ,
            "WORKER_DATABASE_URL": self.worker_dsn,
            "PROVIDER_PROFILE": "offline",
            "FAKE_PROVIDER_DIR": self.folder.name,
            "S3_ENDPOINT": self.settings.s3_endpoint,
            "S3_ACCESS_KEY": self.settings.s3_access_key,
            "S3_SECRET_KEY": self.settings.s3_secret_key,
            "S3_BUCKET": self.settings.s3_bucket,
            "PYTHONPATH": str(Path("src").resolve()),
        }
        result = subprocess.run(
            [sys.executable, "-m", "visual_platform.worker", "--task", task],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "succeeded")
        self.assertEqual(self.client.get("/api/v1/tasks/" + task).json()["status"], "succeeded")
