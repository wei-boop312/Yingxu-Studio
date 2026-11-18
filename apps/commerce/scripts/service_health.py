"""Bounded dependency checks using only the container's own service identity."""

import asyncio
import os
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def check(component):
    if component == "api":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8000/api/v1/health/ready", timeout=3) as response:
            if response.status != 200:
                raise ValueError("API not ready")
        return
    key = "WORKER_DATABASE_URL" if component == "worker" else "DISPATCH_DATABASE_URL"
    if os.environ.get("CONTAINER_HEARTBEAT") == "1":
        age = time.time() - Path("/tmp/visual-" + component + ".heartbeat").stat().st_mtime
        if age < 0 or age > 20:
            raise ValueError("service heartbeat stale")
    with psycopg.connect(
        os.environ[key], connect_timeout=2, options="-c statement_timeout=1500"
    ) as connection:
        connection.execute("SELECT 1")
    import aio_pika

    mq_connection = await aio_pika.connect(os.environ["AMQP_URL"], timeout=2)
    await mq_connection.close()
    if component == "worker":
        from visual_platform.storage.s3 import S3Storage

        storage = S3Storage(
            SimpleNamespace(
                **{
                    k.lower(): os.environ[k]
                    for k in (
                        "S3_ENDPOINT",
                        "S3_ACCESS_KEY",
                        "S3_SECRET_KEY",
                        "S3_BUCKET",
                        "S3_REGION",
                    )
                }
            )
        )
        try:
            storage.probe()
        finally:
            storage.close()


if __name__ == "__main__":
    try:
        component = sys.argv[1]
        if component not in ("api", "worker", "publisher"):
            raise ValueError("invalid service")
        asyncio.run(check(component))
    except Exception as error:
        print("service dependency check failed: " + type(error).__name__)
        raise SystemExit(1)
