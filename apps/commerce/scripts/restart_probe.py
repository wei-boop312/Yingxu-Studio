"""Private local drill fixture; no supplier calls or business task writes."""

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import aio_pika
import httpx
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.storage.s3 import S3Storage, StorageError

p = argparse.ArgumentParser()
p.add_argument("phase", choices=["prepare", "verify", "cleanup"])
p.add_argument("--state", type=Path, required=True)
args = p.parse_args()
profile = json.loads((ROOT / ".local/runtime.json").read_text())
for key in ("AMQP_URL", "S3_ENDPOINT"):
    if urlsplit(profile[key]).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only isolated loopback services allowed")
for name in ("aio_pika", "aiormq"):
    logging.getLogger(name).setLevel(logging.CRITICAL)
state = (
    {"queue": "restart.drill." + uuid4().hex, "object": "restart-drill/" + uuid4().hex}
    if args.phase == "prepare"
    else json.loads(args.state.read_text())
)
if args.phase == "prepare":
    # Persist identities before any remote mutation so partial setup is cleanable.
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state))
payload = b"visual-platform-native-restart-v1\x00" + state["queue"].encode()
storage = S3Storage(
    SimpleNamespace(**{k.lower(): v for k, v in profile.items() if k.startswith("S3_")})
)


async def run():
    connection = await aio_pika.connect(profile["AMQP_URL"], timeout=5)
    try:
        channel = await connection.channel(publisher_confirms=True)
        queue = await channel.declare_queue(
            state["queue"], durable=True, arguments={"x-queue-type": "quorum"}
        )
        if args.phase == "prepare":
            state["version"] = storage.put_immutable(
                state["object"], payload, "application/octet-stream"
            )
            state["sha256"] = hashlib.sha256(payload).hexdigest()
            await channel.default_exchange.publish(
                aio_pika.Message(payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                routing_key=queue.name,
                mandatory=True,
            )
            message = await queue.get(timeout=5)
            if message.body != payload:
                raise RuntimeError("prepared payload differs")
            # Deliberately leave delivery unacknowledged across connection closure.
            args.state.parent.mkdir(parents=True, exist_ok=True)
            args.state.write_text(json.dumps(state))
            return {"prepared": True, "persistent_quorum_delivery": True, "unacknowledged": True}
        if args.phase == "verify":
            message = await queue.get(timeout=5)
            if message.body != payload or not message.redelivered:
                raise RuntimeError("durable redelivery missing")
            if message.delivery_mode != aio_pika.DeliveryMode.PERSISTENT:
                raise RuntimeError("delivery persistence lost")
            await message.ack()
            empty = False
            try:
                await queue.get(timeout=2)
            except aio_pika.exceptions.QueueEmpty:
                empty = True
            if not empty:
                raise RuntimeError("unexpected duplicate fixture delivery")
            # HeadBucket may recover before the Seaweed master elects and locates volumes.
            # Probe actual object reads with a bounded recovery window, retaining attempts.
            started = time.monotonic()
            attempts = 0
            while True:
                attempts += 1
                try:
                    data, _, _ = storage.get(
                        state["object"], version=state["version"], short_probe=True
                    )
                    break
                except (BotoCoreError, ClientError):
                    if time.monotonic() - started >= 90:
                        raise
                    await asyncio.sleep(1)
            if hashlib.sha256(data).hexdigest() != state["sha256"]:
                raise RuntimeError("object changed after restart")
            try:
                storage.put_immutable(
                    state["object"], b"X" * len(payload), "application/octet-stream"
                )
            except StorageError:
                pass
            else:
                raise RuntimeError("immutable overwrite was accepted")
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.get(storage.download_url(state["object"], state["version"]))
                if response.status_code != 200 or response.content != data:
                    raise RuntimeError("private signed download failed")
                anonymous = await client.get(
                    profile["S3_ENDPOINT"].rstrip("/")
                    + "/"
                    + storage.bucket
                    + "/"
                    + state["object"]
                )
                if anonymous.status_code not in (401, 403):
                    raise RuntimeError("object anonymous access not denied")
            return {
                "mq_persistent_redelivery": True,
                "mq_fixture_queue_empty_after_ack": True,
                "s3_sha256_preserved": True,
                "s3_object_verification_seconds": round(time.monotonic() - started, 3),
                "s3_get_calls": attempts,
                "s3_immutable_conflict_rejected": True,
                "signed_download": True,
                "anonymous_denied": True,
            }
        await queue.delete(if_unused=False, if_empty=False)
        params = {"Bucket": storage.bucket, "Key": state["object"]}
        if state.get("version"):
            params["VersionId"] = state["version"]
        storage.client.delete_object(**params)
        return {"fixture_cleanup": True}
    finally:
        await connection.close()


try:
    print(json.dumps(asyncio.run(run())))
finally:
    storage.close()
