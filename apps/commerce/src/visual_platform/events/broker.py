from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import aio_pika
from pamqp.commands import Basic
from pydantic import Field

from visual_platform.domain.schemas import StrictModel


class Delivery(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    outbox_id: UUID
    task_id: UUID


class Broker:
    def __init__(self, connection, channel, exchange, dead, queue, manual):
        self.connection = connection
        self.channel = channel
        self.exchange = exchange
        self.dead = dead
        self.queue = queue
        self.manual = manual

    @classmethod
    async def connect(cls, url, prefix="visual.v1"):
        u = urlsplit(url)
        if u.scheme not in ("amqp", "amqps"):
            raise ValueError("invalid broker URL")
        if u.scheme == "amqp" and u.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("remote RabbitMQ requires TLS")
        if (
            not prefix
            or len(prefix) > 100
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for c in prefix
            )
        ):
            raise ValueError("invalid queue prefix")
        connection = await aio_pika.connect_robust(url, timeout=10)
        try:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            await channel.set_qos(prefetch_count=2)
            exchange = await channel.declare_exchange(
                prefix + ".tasks", aio_pika.ExchangeType.DIRECT, durable=True
            )
            dead = await channel.declare_exchange(
                prefix + ".dead", aio_pika.ExchangeType.DIRECT, durable=True
            )
            manual = await channel.declare_queue(
                prefix + ".manual", durable=True, arguments={"x-queue-type": "quorum"}
            )
            await manual.bind(dead, routing_key="manual")
            queue = await channel.declare_queue(
                prefix + ".generate",
                durable=True,
                arguments={
                    "x-queue-type": "quorum",
                    "x-dead-letter-exchange": dead.name,
                    "x-dead-letter-routing-key": "manual",
                },
            )
            await queue.bind(exchange, routing_key="generate")
            return cls(connection, channel, exchange, dead, queue, manual)
        except BaseException:
            await connection.close()
            raise

    async def publish(self, delivery: Delivery, *, manual=False):
        message = aio_pika.Message(
            delivery.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(delivery.outbox_id),
        )
        confirmation = await (self.dead if manual else self.exchange).publish(
            message, routing_key="manual" if manual else "generate", mandatory=True, timeout=10
        )
        if not isinstance(confirmation, Basic.Ack):
            raise RuntimeError("publisher confirmation missing")

    async def close(self):
        await self.connection.close()
