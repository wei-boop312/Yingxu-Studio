import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import asyncpg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

from visual_platform.db.transactions import tenant_transaction
from visual_platform.services.quotes import db_conflict
from visual_platform.storage.s3 import StorageError

ROOT = Path(__file__).resolve().parents[3]


async def decode(data, mime):
    work = ROOT / "work" / "decode"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as directory:
        original = Path(directory) / "source"
        output = Path(directory) / "canonical.png"
        original.write_bytes(data)
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG"}
        }
        env["PYTHONPATH"] = str(ROOT / "src")
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "visual_platform.imaging.worker",
            str(original),
            str(output),
            mime,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(child.communicate(), timeout=12)
        except BaseException:
            if child.returncode is None:
                child.kill()
                await child.wait()
            raise
        if child.returncode == 2:
            raise ValueError("invalid_image")
        if child.returncode != 0:
            raise RuntimeError("decoder_failed")
        return output.read_bytes(), json.loads(stdout)


async def complete_upload(app, uid, intent_id):
    if app.state.storage is None:
        raise HTTPException(503, "storage_not_configured")
    try:
        await asyncio.wait_for(app.state.decode_capacity.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(429, "decoder_capacity")
    owner = uuid4()
    try:
        async with tenant_transaction(app.state.api_pool, uid) as c:
            try:
                intent = json.loads(
                    await c.fetchval("SELECT app.claim_upload($1,$2)", intent_id, owner)
                )
            except asyncpg.RaiseError as exc:
                db_conflict(exc)
        if intent["status"] == "ready":
            return UUID(intent["completed_version_id"])
        try:
            async with asyncio.timeout(20):
                raw, content_type, raw_storage = await asyncio.to_thread(
                    app.state.storage.get, intent["temp_key"]
                )
                if len(raw) != intent["expected_size"] or content_type != intent["expected_mime"]:
                    raise ValueError("upload_metadata_mismatch")
                canonical, meta = await decode(raw, intent["expected_mime"])
                raw_hash = hashlib.sha256(raw).hexdigest()
                canonical_hash = hashlib.sha256(canonical).hexdigest()
                original_id = uuid5(intent_id, "original:" + raw_hash)
                version_id = uuid5(intent_id, "canonical:" + canonical_hash)
                original_key = "private/" + str(uid) + "/" + str(original_id)
                canonical_key = "private/" + str(uid) + "/" + str(version_id)
                original_storage = await asyncio.to_thread(
                    app.state.storage.put_immutable, original_key, raw, intent["expected_mime"]
                )
                canonical_storage = await asyncio.to_thread(
                    app.state.storage.put_immutable, canonical_key, canonical, "image/png"
                )
                async with tenant_transaction(app.state.api_pool, uid) as c:
                    result = await c.fetchval(
                        "SELECT app.finish_upload($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)",
                        intent_id,
                        owner,
                        original_id,
                        version_id,
                        app.state.storage.bucket,
                        original_key,
                        original_storage,
                        raw_hash,
                        intent["expected_mime"],
                        meta["original_width"],
                        meta["original_height"],
                        len(raw),
                        canonical_key,
                        canonical_storage,
                        canonical_hash,
                        meta["width"],
                        meta["height"],
                        len(canonical),
                    )
                return result
        except ValueError:
            async with tenant_transaction(app.state.api_pool, uid) as c:
                await c.execute(
                    "SELECT app.release_upload($1,$2,$3,true)", intent_id, owner, "invalid_image"
                )
            raise HTTPException(422, "invalid_image")
        except (TimeoutError, RuntimeError, BotoCoreError, ClientError, StorageError):
            async with tenant_transaction(app.state.api_pool, uid) as c:
                await c.execute(
                    "SELECT app.release_upload($1,$2,$3,false)",
                    intent_id,
                    owner,
                    "validation_temporarily_unavailable",
                )
            raise HTTPException(503, "validation_temporarily_unavailable")
    finally:
        app.state.decode_capacity.release()
