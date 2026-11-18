import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from visual_platform.core.termination import run_service
from visual_platform.worker.consumer import Consumer


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def consumer(self, *, timeout=1):
        ready = asyncio.Event()
        cancelled = asyncio.Event()

        async def consume(callback, **kwargs):
            ready.set()
            return "owned"

        async def cancel(tag):
            self.assertEqual(tag, "owned")
            cancelled.set()

        consumer = Consumer(
            None,
            SimpleNamespace(queue=SimpleNamespace(consume=consume, cancel=cancel)),
            drain_seconds=timeout,
        )
        service = asyncio.create_task(consumer.run())
        await ready.wait()
        return consumer, service, cancelled

    async def test_stop_waits_for_existing_delivery_and_requeues_new_delivery(self):
        consumer, service, cancelled = await self.consumer()
        entered = asyncio.Event()
        release = asyncio.Event()
        completed = []

        async def handle(message):
            entered.set()
            await release.wait()
            completed.append("settled")

        consumer.handle = handle
        delivery = asyncio.create_task(consumer.dispatch(None))
        await entered.wait()
        service.cancel()
        await cancelled.wait()
        await asyncio.sleep(0)
        self.assertFalse(service.done())
        requeued = []

        async def nack(**kwargs):
            requeued.append(kwargs)

        self.assertEqual(await consumer.dispatch(SimpleNamespace(nack=nack)), "stopping")
        release.set()
        await delivery
        with self.assertRaises(asyncio.CancelledError):
            await service
        self.assertEqual(completed, ["settled"])
        self.assertEqual(requeued, [{"requeue": True}])
        self.assertFalse(consumer.inflight)

    async def test_drain_timeout_cancels_delivery_cleanup_before_return(self):
        consumer, service, _ = await self.consumer(timeout=0.01)
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def handle(message):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        consumer.handle = handle
        delivery = asyncio.create_task(consumer.dispatch(None))
        await entered.wait()
        service.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await service
        self.assertTrue(cleaned.is_set())
        self.assertTrue(delivery.cancelled())
        self.assertFalse(consumer.inflight)

    async def test_sigterm_wrapper_runs_cleanup_and_returns_success(self):
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        callbacks = []

        async def service():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with (
            patch.object(
                loop,
                "add_signal_handler",
                side_effect=lambda signal, callback: callbacks.append(callback),
            ),
            patch.object(loop, "remove_signal_handler"),
        ):
            task = asyncio.create_task(run_service(service()))
            await entered.wait()
            callbacks[0]()
            self.assertIsNone(await task)
        self.assertTrue(cleaned.is_set())

    async def test_external_cancellation_is_not_swallowed(self):
        entered = asyncio.Event()

        async def service():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(run_service(service()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
