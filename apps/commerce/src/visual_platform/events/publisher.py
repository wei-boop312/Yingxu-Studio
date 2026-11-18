import asyncio
from uuid import uuid4

from visual_platform.events.broker import Delivery


class Publisher:
    def __init__(self, pool, broker):
        self.pool = pool
        self.broker = broker

    async def publish_once(self, limit=20, task_id=None):
        if not 1 <= limit <= 100:
            raise ValueError("invalid limit")
        token = uuid4()
        async with self.pool.acquire() as c:
            async with c.transaction():
                rows = await c.fetch(
                    """WITH candidates AS (
                  SELECT id FROM ops.outbox WHERE published_at IS NULL AND next_attempt_at<=now()
                  AND (locked_until IS NULL OR locked_until<=now()) AND ($2::uuid IS NULL OR task_id=$2)
                  ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT $1)
                  UPDATE ops.outbox o SET publisher_token=$3,locked_until=now()+interval '30 seconds',publish_attempts=publish_attempts+1
                  FROM candidates x WHERE o.id=x.id RETURNING o.id,o.task_id,o.event_type""",
                    limit,
                    task_id,
                    token,
                )
        published = 0
        for row in rows:
            try:
                await self.broker.publish(
                    Delivery(outbox_id=row["id"], task_id=row["task_id"]),
                    manual=row["event_type"] == "task.manual",
                )
            except Exception:
                async with self.pool.acquire() as c:
                    await c.execute(
                        "UPDATE ops.outbox SET locked_until=NULL,publisher_token=NULL,next_attempt_at=now()+make_interval(secs=>LEAST(60,publish_attempts*2)) WHERE id=$1 AND publisher_token=$2",
                        row["id"],
                        token,
                    )
                continue
            async with self.pool.acquire() as c:
                await c.execute(
                    "UPDATE ops.outbox SET published_at=now(),locked_until=NULL,publisher_token=NULL WHERE id=$1 AND publisher_token=$2",
                    row["id"],
                    token,
                )
            published += 1
        return published

    async def recover_once(self, limit=20):
        async with self.pool.acquire() as c:
            return await c.fetchval("SELECT ops.enqueue_recoveries($1)", limit)

    async def run(self):
        while True:
            await self.recover_once()
            count = await self.publish_once()
            if not count:
                await asyncio.sleep(1)
