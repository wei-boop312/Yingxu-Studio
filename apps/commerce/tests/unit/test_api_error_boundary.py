import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from visual_platform.api.main import create_app


class ApiErrorBoundaryTests(unittest.TestCase):
    def test_unconfigured_early_responses_have_matching_support_ids_and_no_store(self):
        with (
            patch.dict("os.environ", {"API_DATABASE_URL": ""}),
            self.assertLogs("visual_platform.telemetry", level="INFO") as logged,
            TestClient(create_app()) as client,
        ):
            responses = [
                client.get("/api/v1/products", headers={"X-Request-ID": "untrusted-client-value"}),
                client.post("/api/v1/auth/login", json={}),
            ]
        ids = set()
        for response in responses:
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "service_not_configured")
            identity = response.headers["x-request-id"]
            self.assertEqual(str(UUID(identity)), identity)
            self.assertEqual(response.json()["error"]["request_id"], identity)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            ids.add(identity)
        self.assertEqual(len(ids), 2)
        observations = [json.loads(item.getMessage()) for item in logged.records]
        self.assertEqual({item["request_id"] for item in observations}, ids)
        self.assertTrue(all("path" not in item and "headers" not in item for item in observations))
