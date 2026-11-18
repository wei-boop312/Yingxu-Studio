import json
from pathlib import Path
from unittest.mock import Mock

from fastapi import HTTPException
from test_offline_pipeline import PipelineFixture

from visual_platform.storage.s3 import S3Storage, StorageError


class RequestArchiveMetricsTests(PipelineFixture):
    def test_real_api_success_and_injected_503_are_observed_without_request_details(self):
        @self.client.app.get("/api/v1/telemetry-fixture")
        async def failure():
            raise HTTPException(503, "synthetic_failure")

        with self.assertLogs("visual_platform.telemetry", level="INFO") as logged:
            self.assertEqual(
                self.client.get("/api/v1/products?private_query=do-not-log").status_code, 200
            )
            self.assertEqual(self.client.get("/api/v1/telemetry-fixture").status_code, 503)
        records = [json.loads(record.getMessage()) for record in logged.records]
        api = [record for record in records if record["event"] == "api_request_observed"]
        self.assertEqual([record["status"] for record in api], [200, 503])
        self.assertTrue(
            all(record["outcome"] == "completed" and record["duration_ms"] >= 0 for record in api)
        )
        for private in ("do-not-log", self.email, str(self.user), "visual_access"):
            self.assertNotIn(private, json.dumps(records))

    def test_archive_failure_metric_does_not_change_known_result_retry_or_credits(self):
        class BrokenWrite(S3Storage):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.client.put_object = Mock(side_effect=StorageError("synthetic write outage"))

        task = self.submit()
        with self.assertLogs("visual_platform.telemetry", level="INFO") as logged:
            self.assertEqual(self.run_worker(task, storage_class=BrokenWrite), "retryable")
        records = [json.loads(record.getMessage()) for record in logged.records]
        failures = [
            record
            for record in records
            if record["event"] == "storage_archive_observed" and record["outcome"] == "failure"
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["reserved"], 16)
        self.assertEqual(self.run_worker(task), "succeeded")
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["spent_total"], 16)
