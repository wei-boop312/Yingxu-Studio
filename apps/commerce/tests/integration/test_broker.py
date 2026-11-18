import asyncio
import os
import secrets
import unittest
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import aio_pika
import psycopg
from psycopg import sql
from test_offline_pipeline import PipelineFixture

from visual_platform.db.pool import open_pool
from visual_platform.events.broker import Broker, Delivery
from visual_platform.events.publisher import Publisher
from visual_platform.storage.s3 import S3Storage
from visual_platform.worker.consumer import Consumer
from visual_platform.worker.runner import Worker


@unittest.skipUnless(os.environ.get("TEST_AMQP_URL"), "native RabbitMQ not configured")
class BrokerTests(PipelineFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dispatch_role = "dispatch_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        with psycopg.connect(cls.owner) as c:
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(cls.dispatch_role), sql.Literal(password))
            )
            c.execute(
                sql.SQL("GRANT ops_dispatcher TO {}").format(sql.Identifier(cls.dispatch_role))
            )
        u = urlsplit(cls.owner)
        cls.dispatch_dsn = urlunsplit(
            (
                u.scheme,
                quote(cls.dispatch_role)
                + ":"
                + quote(password)
                + "@"
                + u.hostname
                + ":"
                + str(u.port or 5432),
                u.path,
                u.query,
                u.fragment,
            )
        )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.owner) as c:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.dispatch_role)))
        super().tearDownClass()

    async def wait_message(self, queue):
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            try:
                return await queue.get(timeout=2)
            except aio_pika.exceptions.QueueEmpty:
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(0.05)

    def mq(self, body):
        async def run():
            dp = await open_pool(self.dispatch_dsn, "ops_dispatcher")
            wp = await open_pool(self.worker_dsn, "app_worker")
            broker = await Broker.connect(os.environ["TEST_AMQP_URL"], "test." + uuid4().hex)
            storage = S3Storage(self.settings)
            try:
                worker = Worker(wp, storage, self.provider)
                publisher = Publisher(dp, broker)
                consumer = Consumer(worker, broker)
                return await body(broker, publisher, consumer)
            finally:
                await broker.queue.delete(if_unused=False, if_empty=False)
                await broker.manual.delete(if_unused=False, if_empty=False)
                await broker.exchange.delete(if_unused=False)
                await broker.dead.delete(if_unused=False)
                await broker.close()
                await dp.close()
                await wp.close()
                storage.close()

        return asyncio.run(run())

    def test_confirmed_persistent_delivery_runs_full_pipeline(self):
        task = UUID(self.submit())

        async def body(b, p, c):
            counts = await asyncio.gather(
                p.publish_once(task_id=task), p.publish_once(task_id=task)
            )
            self.assertEqual(sum(counts), 1)
            message = await b.queue.get(timeout=5)
            self.assertEqual(message.delivery_mode, aio_pika.DeliveryMode.PERSISTENT)
            self.assertEqual(await c.handle(message), "succeeded")

        self.mq(body)
        self.assertEqual(
            self.client.get("/api/v1/tasks/" + str(task)).json()["status"], "succeeded"
        )

    def test_lost_confirmation_duplicate_does_not_regenerate(self):
        task = UUID(self.submit())

        async def body(b, p, c):
            class LoseConfirmation:
                async def publish(self, *args, **kwargs):
                    await b.publish(*args, **kwargs)
                    raise RuntimeError("injected lost confirm")

            lossy = Publisher(p.pool, LoseConfirmation())
            self.assertEqual(await lossy.publish_once(task_id=task), 0)
            with psycopg.connect(self.owner) as db:
                self.assertIsNone(
                    db.execute(
                        "SELECT published_at FROM ops.outbox WHERE task_id=%s", (task,)
                    ).fetchone()[0]
                )
                db.execute("UPDATE ops.outbox SET next_attempt_at=now() WHERE task_id=%s", (task,))
            self.assertEqual(await p.publish_once(task_id=task), 1)
            self.assertEqual(await c.handle(await b.queue.get(timeout=5)), "succeeded")
            self.assertEqual(await c.handle(await b.queue.get(timeout=5)), "not_claimed")

        self.mq(body)
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["spent_total"], 16)

    def test_invalid_body_is_dead_lettered(self):
        async def body(b, p, c):
            await b.exchange.publish(
                aio_pika.Message(
                    b'{"schema_version":1,"user_id":"untrusted"}',
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key="generate",
                mandatory=True,
            )
            self.assertEqual(await c.handle(await b.queue.get(timeout=5)), "invalid")
            dead = await self.wait_message(b.manual)
            self.assertIn(b"untrusted", dead.body)
            await dead.ack()

        self.mq(body)

    def test_unroutable_publish_keeps_outbox_pending(self):
        task = UUID(self.submit())

        async def body(b, p, c):
            await b.queue.unbind(b.exchange, routing_key="generate")
            self.assertEqual(await p.publish_once(task_id=task), 0)
            with psycopg.connect(self.owner) as db:
                self.assertIsNone(
                    db.execute(
                        "SELECT published_at FROM ops.outbox WHERE task_id=%s", (task,)
                    ).fetchone()[0]
                )

        self.mq(body)

    def test_recovery_limit_persists_manual_queue_message(self):
        task = UUID(self.submit())

        async def body(b, p, c):
            self.assertEqual(await p.publish_once(task_id=task), 1)
            for _ in range(6):
                with psycopg.connect(self.owner) as db:
                    db.execute(
                        "UPDATE ops.task_dispatch SET next_recovery_at=now()-interval '1 second' WHERE task_id=%s",
                        (task,),
                    )
                await p.recover_once(limit=100)
                await p.publish_once(task_id=task)
            dead = await self.wait_message(b.manual)
            self.assertEqual(Delivery.model_validate_json(dead.body).task_id, task)
            await dead.ack()
            with psycopg.connect(self.owner) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT state,recovery_attempts FROM ops.task_dispatch WHERE task_id=%s",
                        (task,),
                    ).fetchone(),
                    ("manual", 6),
                )

        self.mq(body)

    def test_commit_before_ack_redelivery_does_not_repeat_charge(self):
        task = UUID(self.submit())

        async def body(b, p, c):
            self.assertEqual(await p.publish_once(task_id=task), 1)
            channel = await b.connection.channel()
            queue = await channel.declare_queue(b.queue.name, passive=True)
            first = await self.wait_message(queue)
            delivery = Delivery.model_validate_json(first.body)
            self.assertEqual(await c.worker.run(delivery.task_id), "succeeded")
            # Closing the channel before ACK models death after durable DB completion.
            await channel.close()
            second = await self.wait_message(b.queue)
            self.assertTrue(second.redelivered)
            self.assertEqual(await c.handle(second), "not_claimed")

        self.mq(body)
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)
        self.assertEqual(self.client.get("/api/v1/credits/balance").json()["spent_total"], 16)

    def test_ack_after_recoverable_archive_failure_uses_db_scanner(self):
        task = UUID(self.submit())

        async def body(b, p, c):
            self.assertEqual(await p.publish_once(task_id=task), 1)
            original = c.worker.storage.put_immutable

            def broken(*args, **kwargs):
                from visual_platform.storage.s3 import StorageError

                raise StorageError("injected archive failure")

            c.worker.storage.put_immutable = broken
            self.assertEqual(await c.handle(await self.wait_message(b.queue)), "retryable")
            c.worker.storage.put_immutable = original
            with psycopg.connect(self.owner) as db:
                db.execute(
                    "UPDATE ops.task_dispatch SET next_recovery_at=now()-interval '1 second' WHERE task_id=%s",
                    (task,),
                )
            await p.recover_once(limit=100)
            self.assertEqual(await p.publish_once(task_id=task), 1)
            self.assertEqual(await c.handle(await self.wait_message(b.queue)), "succeeded")

        self.mq(body)
        self.assertEqual(len(list(Path(self.folder.name).glob("generate-*.intent"))), 1)
