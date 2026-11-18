import argparse
import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from visual_platform.core.termination import run_service
from visual_platform.db.pool import open_pool
from visual_platform.providers.fake import DurableFakeProvider
from visual_platform.providers.live_bailian import LiveRoutingWorker
from visual_platform.services.heartbeat import service_heartbeat
from visual_platform.storage.s3 import S3Storage
from visual_platform.worker.runner import Worker


async def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task", type=UUID)
    mode.add_argument("--consume", action="store_true")
    parser.add_argument(
        "--fake-mode",
        choices=["success", "failed", "uncertain", "late", "detect_uncertain"],
        default="success",
    )
    args = parser.parse_args()
    profile = os.environ.get("PROVIDER_PROFILE", "offline")
    if profile not in ("offline", "bailian_live"):
        raise SystemExit("Unsupported provider profile")
    settings = SimpleNamespace(
        **{
            name.lower(): os.environ[name]
            for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
        },
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
    )
    pool = await open_pool(os.environ["WORKER_DATABASE_URL"], "app_worker")
    storage = S3Storage(settings)
    directory = Path(
        os.environ.get(
            "FAKE_PROVIDER_DIR", str(Path(__file__).resolve().parents[3] / "work" / "fake-provider")
        )
    )
    stack = AsyncExitStack()
    try:
        worker: Worker | LiveRoutingWorker = Worker(
            pool, storage, DurableFakeProvider(directory, mode=args.fake_mode)
        )
        if profile == "bailian_live":
            from visual_platform.core.credentials import private_credential
            from visual_platform.providers.live_bailian import live_bailian_runtime
            from visual_platform.providers.runtime import load_runtime

            runtime = load_runtime(Path(os.environ["LIVE_PROVIDER_RUNTIME_FILE"]))
            provider = await stack.enter_async_context(
                live_bailian_runtime(
                    runtime,
                    Path(os.environ["LIVE_PROVIDER_PRIVATE_DIR"]),
                    key=private_credential("DASHSCOPE_API_KEY"),
                )
            )
            worker = LiveRoutingWorker(pool, storage, provider, directory)
        if args.consume:
            from visual_platform.events.broker import Broker
            from visual_platform.worker.consumer import Consumer

            broker = await Broker.connect(
                os.environ["AMQP_URL"], os.environ.get("QUEUE_PREFIX", "visual.v1")
            )
            try:
                async with service_heartbeat(pool, "worker"):
                    await Consumer(worker, broker).run()
            finally:
                await broker.close()
        else:
            result = await worker.run(args.task)
            print(json.dumps({"task_id": str(args.task), "result": result}))
    finally:
        await stack.aclose()
        await pool.close()
        storage.close()


if __name__ == "__main__":
    asyncio.run(run_service(main()))
