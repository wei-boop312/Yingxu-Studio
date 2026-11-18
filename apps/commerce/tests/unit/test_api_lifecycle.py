import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from visual_platform.api.main import create_app


class ApiLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, failure=None, enabled=True):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "shutdown.json"
            marker.write_text("stale")
            settings = SimpleNamespace(
                s3_endpoint="configured", api_database_url="api", auth_database_url="auth"
            )
            api = SimpleNamespace(close=AsyncMock(side_effect=failure))
            auth = SimpleNamespace(close=AsyncMock())
            storage = SimpleNamespace(close=Mock())
            readiness = SimpleNamespace(close=AsyncMock())
            with (
                patch.dict("os.environ", {"CONTAINER_LIFECYCLE": "1" if enabled else "0"}),
                patch("visual_platform.core.lifecycle.API_SHUTDOWN_MARKER", marker),
                patch("visual_platform.api.main.open_pool", AsyncMock(side_effect=[api, auth])),
                patch("visual_platform.api.main.S3Storage", return_value=storage),
                patch("visual_platform.api.main.Readiness", return_value=readiness),
            ):
                app = create_app(settings)

                async def lifecycle():
                    async with app.router.lifespan_context(app):
                        if enabled:
                            self.assertFalse(marker.exists())
                        api.close.assert_not_awaited()
                        auth.close.assert_not_awaited()

                if failure:
                    with self.assertRaisesRegex(RuntimeError, "close failed"):
                        await lifecycle()
                    self.assertFalse(marker.exists())
                else:
                    await lifecycle()
                    if enabled:
                        self.assertTrue(
                            json.loads(marker.read_text())["lifespan_shutdown_completed"]
                        )
                    else:
                        self.assertEqual(marker.read_text(), "stale")
                readiness.close.assert_awaited_once()
                api.close.assert_awaited_once()
                auth.close.assert_awaited_once()
                storage.close.assert_called_once()

    async def test_marker_requires_completed_lifespan_resource_cleanup(self):
        await self.exercise()

    async def test_cleanup_failure_does_not_leave_success_marker_and_other_resources_close(self):
        await self.exercise(failure=RuntimeError("close failed"))

    async def test_marker_is_disabled_outside_explicit_container_profile(self):
        await self.exercise(enabled=False)
