import asyncio

from pydantic import ValidationError

from visual_platform.events.broker import Delivery


class Consumer:
    def __init__(self, worker, broker, *, drain_seconds=20):
        self.worker = worker
        self.broker = broker
        self.drain_seconds = drain_seconds
        self.inflight = set()
        self.closing = False

    async def dispatch(self, message):
        task = asyncio.current_task()
        self.inflight.add(task)
        try:
            if self.closing:
                await message.nack(requeue=True)
                return "stopping"
            return await self.handle(message)
        finally:
            self.inflight.discard(task)

    async def handle(self, message):
        if len(message.body) > 4096:
            await message.reject(requeue=False)
            return "invalid"
        try:
            delivery = Delivery.model_validate_json(message.body)
        except ValidationError:
            await message.reject(requeue=False)
            return "invalid"
        try:
            profile = getattr(self.worker, "validation_profile", "offline")
            valid = await self.worker.db(
                "SELECT ops.validate_delivery($1,$2,$3)"
                if profile != "offline"
                else "SELECT ops.validate_delivery($1,$2)",
                delivery.outbox_id,
                delivery.task_id,
                *([profile] if profile != "offline" else []),
            )
            if not valid:
                await message.reject(requeue=False)
                return "invalid_reference"
            result = await self.worker.run(delivery.task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # No ACK while the DB cannot establish a durable recovery point.
            await message.nack(requeue=True)
            await asyncio.sleep(1)
            return "retry"
        await message.ack()
        return result

    async def run(self):
        tag = await self.broker.queue.consume(self.dispatch, no_ack=False)
        try:
            await asyncio.Event().wait()
        finally:
            self.closing = True
            try:
                await self.broker.queue.cancel(tag)
            finally:
                await self.drain()

    async def drain(self):
        await asyncio.sleep(0)
        pending = [task for task in self.inflight if not task.done()]
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=self.drain_seconds)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
