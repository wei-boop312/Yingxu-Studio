"""Native PostgreSQL capacity and S3 readiness failure probes."""

import time
from dataclasses import replace

from test_assets import AssetFixture

from visual_platform.services.readiness import Readiness
from visual_platform.storage.s3 import S3Storage


class HealthTests(AssetFixture):
    def test_real_dependencies_ready_without_secret_disclosure(self):
        response = self.client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "scope": "api_dependencies",
                "checks": {"database": "ready", "authentication": "ready", "storage": "ready"},
            },
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        for secret in (
            self.settings.s3_secret_key,
            self.settings.api_database_url,
            self.settings.auth_database_url,
            self.settings.s3_endpoint,
        ):
            self.assertNotIn(secret, response.text)
        self.assertEqual(self.client.get("/api/v1/health/live").json(), {"status": "alive"})

    def test_pool_exhaustion_is_bounded_and_recovers(self):
        app = self.client.app

        async def hold():
            return [
                await app.state.api_pool.acquire() for _ in range(app.state.api_pool.get_max_size())
            ]

        held = self.client.portal.call(hold)
        try:
            start = time.monotonic()
            response = self.client.get("/api/v1/health/ready")
            elapsed = time.monotonic() - start
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["checks"]["database"], "unavailable")
            self.assertLess(elapsed, 2.5)
            self.assertEqual(self.client.get("/api/v1/health/live").status_code, 200)
        finally:

            async def release():
                for conn in held:
                    await app.state.api_pool.release(conn)
                app.state.readiness = Readiness(
                    app.state.api_pool, app.state.auth_pool, app.state.storage
                )

            self.client.portal.call(release)
        self.assertEqual(self.client.get("/api/v1/health/ready").status_code, 200)

    def test_s3_access_denied_marks_only_storage_unavailable(self):
        broken = S3Storage(
            replace(
                self.settings,
                s3_access_key="unknown-health-test-access",
                s3_secret_key="invalid-health-test-secret",
            )
        )
        self.addCleanup(broken.close)
        app = self.client.app

        async def attach():
            app.state.readiness = Readiness(app.state.api_pool, app.state.auth_pool, broken)

        self.client.portal.call(attach)
        response = self.client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json()["checks"],
            {"database": "ready", "authentication": "ready", "storage": "unavailable"},
        )
        self.assertNotIn("invalid-health-test-secret", response.text)
        self.assertEqual(self.client.get("/api/v1/health/live").status_code, 200)
