import asyncio
import threading
import time
import unittest

from visual_platform.services.readiness import Readiness


class Connection:
    async def fetch(self, *args):
        await asyncio.sleep(0)
        return []

    async def fetchval(self, *args):
        return True


class Acquisition:
    async def __aenter__(self):
        return Connection()

    async def __aexit__(self, *args):
        pass


class Pool:
    def __init__(self):
        self.calls = 0

    def acquire(self, **kwargs):
        self.calls += 1
        return Acquisition()


class Storage:
    def __init__(self):
        self.calls = 0

    def probe(self):
        self.calls += 1
        time.sleep(0.01)


class BlockingStorage(Storage):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def probe(self):
        self.calls += 1
        self.started.set()
        self.release.wait(2)


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_never_ready(self):
        result = await Readiness(None, None, None).check()
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(set(result["checks"].values()), {"not_configured"})

    async def test_concurrent_probes_and_cache_are_single_flight(self):
        api, auth, storage = Pool(), Pool(), Storage()
        probe = Readiness(api, auth, storage)
        results = await asyncio.gather(*(probe.check() for _ in range(40)))
        self.assertTrue(all(r["status"] == "ready" for r in results))
        self.assertEqual((api.calls, auth.calls, storage.calls), (1, 1, 1))
        self.assertEqual(await probe.check(), results[0])
        self.assertEqual(storage.calls, 1)

    async def test_timed_out_native_probe_is_reused_until_it_finishes(self):
        storage = BlockingStorage()
        probe = Readiness(Pool(), Pool(), storage, timeout=0.1, ttl=0)
        try:
            for _ in range(4):
                self.assertEqual((await probe.check())["checks"]["storage"], "unavailable")
            self.assertEqual(storage.calls, 1)
            storage.release.set()
            self.assertEqual((await probe.check())["status"], "ready")
        finally:
            storage.release.set()

    async def test_canceled_request_does_not_duplicate_native_probe(self):
        storage = BlockingStorage()
        probe = Readiness(Pool(), Pool(), storage, timeout=0.1, ttl=0)
        try:
            task = asyncio.create_task(probe.check())
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(storage.started.wait), 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((await probe.check())["checks"]["storage"], "unavailable")
            self.assertEqual(storage.calls, 1)
            storage.release.set()
            self.assertEqual((await probe.check())["status"], "ready")
        finally:
            storage.release.set()


class StartupCleanupTests(unittest.TestCase):
    def test_auth_pool_startup_failure_closes_api_pool_and_storage(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from fastapi.testclient import TestClient

        from visual_platform.api.main import create_app
        from visual_platform.core.settings import Settings

        settings = Settings(
            "postgresql://unused/api",
            "postgresql://unused/auth",
            "unused",
            "unused",
            "http://localhost",
            False,
            "http://127.0.0.1:1",
            "test",
            "test",
            "test",
        )
        pool = MagicMock()
        pool.close = AsyncMock()
        storage = MagicMock()
        with (
            patch(
                "visual_platform.api.main.open_pool",
                new=AsyncMock(
                    side_effect=[pool, RuntimeError("injected auth pool startup failure")]
                ),
            ),
            patch("visual_platform.api.main.S3Storage", return_value=storage),
        ):
            with self.assertRaises(RuntimeError):
                with TestClient(create_app(settings)):
                    pass
        pool.close.assert_awaited_once()
        storage.close.assert_called_once()
