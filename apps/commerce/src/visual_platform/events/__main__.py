import argparse
import asyncio
import json
import os

from visual_platform.core.termination import run_service
from visual_platform.db.pool import open_pool
from visual_platform.events.broker import Broker
from visual_platform.events.publisher import Publisher
from visual_platform.services.heartbeat import service_heartbeat


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    pool = await open_pool(os.environ["DISPATCH_DATABASE_URL"], "ops_dispatcher")
    broker = await Broker.connect(
        os.environ["AMQP_URL"], os.environ.get("QUEUE_PREFIX", "visual.v1")
    )
    try:
        publisher = Publisher(pool, broker)
        if args.once:
            recovered = await publisher.recover_once()
            sent = await publisher.publish_once()
            print(json.dumps({"recovery_enqueued": recovered, "published": sent}))
        else:
            async with service_heartbeat(pool, "publisher"):
                await publisher.run()
    finally:
        await broker.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run_service(main()))
