"""Best-effort bounded liveness, separate from per-task fencing and leases."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4


@asynccontextmanager
async def service_heartbeat(pool, component, *, interval=5):
    identity = uuid4()

    async def beat():
        while True:
            try:
                async with asyncio.timeout(3):
                    async with pool.acquire(timeout=2) as c:
                        await c.execute(
                            "SELECT ops.touch_service($1,$2)", component, identity, timeout=2
                        )
                    if os.environ.get("CONTAINER_HEARTBEAT") == "1":
                        if component not in ("worker", "publisher"):
                            raise ValueError("unsupported heartbeat component")
                        Path("/tmp/visual-" + component + ".heartbeat").touch()
            except Exception:
                pass
            await asyncio.sleep(interval)

    task = asyncio.create_task(beat())
    try:
        yield identity
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
