"""Finite, cached API dependency probes. No model, queue publish or tenant writes."""

import asyncio


class Readiness:
    def __init__(self, api_pool, auth_pool, storage, *, timeout=1.5, ttl=3.0):
        self.api_pool = api_pool
        self.auth_pool = auth_pool
        self.storage = storage
        self.timeout = timeout
        self.ttl = ttl
        self._lock = asyncio.Lock()
        self._result = None
        self._expires = 0.0
        self._storage_task = None

    async def _database(self, pool, authentication=False):
        if pool is None:
            return "not_configured"
        try:
            async with asyncio.timeout(self.timeout):
                async with pool.acquire(timeout=self.timeout) as c:
                    if authentication:
                        await c.fetch("SELECT user_id FROM auth.credentials LIMIT 0")
                        await c.fetch("SELECT id FROM auth.refresh_sessions LIMIT 0")
                    else:
                        await c.fetch("SELECT id FROM app.products LIMIT 0")
                        valid = await c.fetchval(
                            "SELECT has_function_privilege(current_user,'app.submit_quoted_task(uuid,uuid,text,text,text)','EXECUTE') AND to_regclass('app.readonly_products') IS NOT NULL"
                        )
                        if not valid:
                            return "unavailable"
            return "ready"
        except Exception:
            return "unavailable"

    async def _storage(self):
        if self.storage is None:
            return "not_configured"
        # A timed-out caller does not spawn another probe while its native I/O remains alive.
        if self._storage_task is None:
            self._storage_task = asyncio.create_task(asyncio.to_thread(self.storage.probe))
            self._storage_task.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        task = self._storage_task
        try:
            await asyncio.wait_for(asyncio.shield(task), self.timeout)
            return "ready"
        except Exception:
            return "unavailable"
        finally:
            if task.done():
                self._storage_task = None

    async def check(self):
        loop = asyncio.get_running_loop()
        if self._result is not None and loop.time() < self._expires:
            return self._result
        async with self._lock:
            if self._result is not None and loop.time() < self._expires:
                return self._result
            database, authentication, storage = await asyncio.gather(
                self._database(self.api_pool), self._database(self.auth_pool, True), self._storage()
            )
            checks = {"database": database, "authentication": authentication, "storage": storage}
            ready = all(value == "ready" for value in checks.values())
            self._result = {
                "status": "ready" if ready else "not_ready",
                "scope": "api_dependencies",
                "checks": checks,
            }
            self._expires = loop.time() + (self.ttl if ready else min(self.ttl, 0.5))
            return self._result

    async def close(self):
        if self._storage_task:
            try:
                await asyncio.wait_for(asyncio.shield(self._storage_task), self.timeout)
            except (Exception, asyncio.CancelledError):
                pass
