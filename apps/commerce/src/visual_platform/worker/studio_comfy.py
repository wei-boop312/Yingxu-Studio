"""Fenced extension for local Studio intents, sharing existing identity and asset storage."""

import argparse
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import asyncpg

from visual_platform.core.termination import run_service
from visual_platform.db.pool import open_pool
from visual_platform.providers.comfyui import ComfyUnavailable, ComfyUncertain, LocalComfy, digest
from visual_platform.services import studio_files
from visual_platform.storage.s3 import S3Storage


class StudioComfyWorker:
    def __init__(self, pool, storage, client, owner=None):
        self.pool = pool
        self.storage = storage
        self.client = client
        self.owner = owner or str(uuid4())

    async def db(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def advance(self, job, action, payload=None):
        await self.db(
            "SELECT ops.advance_studio_intent($1,$2,$3,$4,$5::jsonb)",
            UUID(job["id"]),
            self.owner,
            job["fencing_token"],
            action,
            json.dumps(payload or {}),
        )

    async def preserve(self, job, kind, data, mime="application/json"):
        sha = hashlib.sha256(data).hexdigest()
        key = f"studio-execution/{job['user_id']}/{job['id']}/{kind}/{sha}"
        version = await asyncio.to_thread(self.storage.put_immutable, key, data, mime)
        await self.db(
            "SELECT ops.append_studio_receipt($1,$2,$3,$4,$5,$6)",
            UUID(job["id"]),
            kind,
            self.storage.bucket,
            key,
            version,
            sha,
        )
        return {
            "sha256": sha,
            "bucket": self.storage.bucket,
            "object_key": key,
            "storage_version_id": version,
        }

    async def source(self, job):
        source = job["source"]
        if (
            source["bucket"] != self.storage.bucket
            or source["mime"] != "image/png"
            or not source["canonical"]
        ):
            raise ValueError("canonical_source_storage_required")
        data, _, _ = await asyncio.to_thread(
            self.storage.get,
            source["object_key"],
            version=source["storage_version_id"],
            limit=20 * 1024 * 1024,
        )
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError("source_hash_mismatch")
        return data

    async def prepare(self, job):
        from workflow_architect.studio_compile import compile_connection_test

        if not job["basis_current"]:
            raise ValueError("queued_plan_basis_changed")

        summary, _ = await self.client.diagnose()
        if summary["server_version"] != "0.43.0":
            raise ValueError("unverified_comfy_version")
        raw = await self.source(job)
        input_name = await self.client.upload_exact(raw, job["source"]["sha256"], UUID(job["id"]))
        summary, nodes = await self.client.diagnose()
        if summary["server_version"] != "0.43.0":
            raise ValueError("comfy_version_changed")
        compiled = compile_connection_test(job["plan"], nodes, input_name, UUID(job["id"]))
        environment = await self.preserve(
            job,
            "environment",
            json.dumps({"summary": summary, "object_info": nodes}, sort_keys=True).encode(),
        )
        snapshot = {
            **compiled,
            "environment": environment,
            "source_asset_version_id": job["source_asset_version_id"],
            "input_sha256": job["source"]["sha256"],
            "input_name": input_name,
            "source_storage_version_id": job["source"]["storage_version_id"],
            "target_url": job["target_url"],
            "server_version": summary["server_version"],
            "model_byte_identity": "not_applicable_no_model_used",
        }
        await self.advance(job, "freeze", snapshot)
        job["snapshot"] = snapshot
        # The durable marker commits before the only submission call. A crashed
        # submitter can subsequently query the preallocated prompt ID, never repost.
        await self.advance(job, "start_submission")
        job["submission_started_at"] = "recorded"
        try:
            receipt = await self.client.submit_once(
                compiled["api_prompt"], UUID(job["prompt_id"]), UUID(job["id"])
            )
        except ComfyUncertain:
            await self.advance(
                job,
                "uncertain",
                {"reason": "submission_response_unknown", "supplier_cost": "unknown"},
            )
            return
        except ComfyUnavailable:
            await self.advance(
                job, "failed", {"reason": "target_workflow_rejected", "definitive": True}
            )
            return
        saved = await self.preserve(job, "submission", receipt.raw)
        await self.advance(
            job,
            "submitted",
            {"submission_receipt": saved, "supplier_cost": "not_applicable_local_connection_test"},
        )

    def verify_history(self, job, record):
        prompt = record.get("prompt", [])
        if (
            len(prompt) < 4
            or prompt[1] != job["prompt_id"]
            or digest(prompt[2]) != job["snapshot"]["workflow_sha256"]
            or prompt[3].get("yingxu_intent_id") != job["id"]
        ):
            raise ValueError("host_history_identity_mismatch")

    async def finish(self, job, record):
        self.verify_history(job, record)
        await self.preserve(job, "history", json.dumps(record, sort_keys=True).encode())
        status = record.get("status", {})
        if status.get("status_str") == "error":
            await self.advance(
                job, "failed", {"reason": "host_execution_failed", "definitive": True}
            )
            return
        if status.get("status_str") != "success" or status.get("completed") is not True:
            await self.advance(job, "pending", {"reason": "host_execution_pending"})
            return
        images = record.get("outputs", {}).get("2", {}).get("images", [])
        if len(images) != 1:
            raise ValueError("unexpected_host_output_count")
        image = images[0]
        name = image.get("filename", "")
        if (
            image.get("type") != "output"
            or image.get("subfolder") != "yingxu"
            or not name.startswith(job["id"] + "_")
            or not name.endswith(".png")
            or any(part in name for part in ("/", "\\", ".."))
        ):
            raise ValueError("host_output_path_mismatch")
        status_code, raw = await self.client.request(
            "GET", "/view", params=image, limit=40 * 1024 * 1024
        )
        if status_code != 200:
            raise ComfyUnavailable("host_output_not_available")
        host_output = await self.preserve(job, "output", raw, "image/png")
        data, mime = await asyncio.to_thread(
            studio_files.candidate, raw, host_output["sha256"], job["plan"]["output_spec"]
        )
        identity = UUID(job["id"])
        version, asset, output = (
            uuid5(identity, "candidate"),
            uuid5(identity, "asset"),
            uuid5(identity, "output"),
        )
        key = f"studio/{job['user_id']}/{job['project_id']}/{version}"
        storage_version = await asyncio.to_thread(self.storage.put_immutable, key, data, mime)
        provenance = {
            "execution_intent_id": job["id"],
            "prompt_id": job["prompt_id"],
            "execution_kind": "connection_test",
            "real_model_execution": False,
            "source_note": "Actual ComfyUI LoadImage→SaveImage connection test; no image generation or redraw.",
            "input_sha256": job["snapshot"]["input_sha256"],
            "workflow_sha256": job["snapshot"]["workflow_sha256"],
            "host_output": host_output,
            "host_filename": name,
            "source_files": [],
        }
        await self.db(
            "SELECT app.complete_studio_intent($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)",
            identity,
            self.owner,
            job["fencing_token"],
            output,
            version,
            asset,
            self.storage.bucket,
            key,
            storage_version,
            hashlib.sha256(data).hexdigest(),
            len(data),
            mime,
            json.dumps(provenance),
        )

    async def recover(self, job):
        history = await self.client.history(UUID(job["prompt_id"]))
        if job["prompt_id"] in history:
            await self.finish(job, history[job["prompt_id"]])
            return
        queue = await self.client.read_json("/queue")
        own = []
        for name in ("queue_running", "queue_pending"):
            for row in queue.get(name, []):
                if len(row) >= 4 and row[1] == job["prompt_id"]:
                    if (
                        digest(row[2]) != job["snapshot"]["workflow_sha256"]
                        or row[3].get("yingxu_intent_id") != job["id"]
                    ):
                        raise ValueError("host_queue_identity_mismatch")
                    own.append(row)
        if job["cancel_requested"] and own:
            result = await self.client.cancel_pending(UUID(job["prompt_id"]))
            if result["cancelled"]:
                await self.advance(
                    job, "cancelled", {"reason": result["reason"], "definitive": True}
                )
                return
        await self.advance(
            job,
            "pending" if own else "uncertain",
            {
                "reason": "host_execution_observed"
                if own
                else "host_execution_not_observed_no_resubmission",
                "supplier_cost": "unknown" if not own else "not_applicable_local_connection_test",
            },
        )

    async def run_once(self, identity=None):
        raw = await self.db("SELECT ops.claim_studio_intent($1,$2)", identity, self.owner)
        if not raw:
            return False
        job = json.loads(raw)
        if job["target_url"] != self.client.url:
            await self.advance(
                job,
                "uncertain" if job["submission_started_at"] else "failed",
                {"reason": "configured_target_changed"},
            )
            return True
        try:
            if job["submission_started_at"]:
                await self.recover(job)
            elif job["cancel_requested"]:
                await self.advance(job, "cancelled", {"reason": "cancelled_before_submission"})
            else:
                await self.prepare(job)
        except asyncio.CancelledError:
            raise
        except asyncpg.ObjectNotInPrerequisiteStateError:
            return True
        except Exception:
            # Never close a possibly accepted submission as a safe retry. Raw
            # dependency errors stay out of public state; receipts remain private.
            await self.advance(
                job,
                "uncertain" if job["submission_started_at"] else "failed",
                {
                    "reason": "execution_requires_reconciliation"
                    if job["submission_started_at"]
                    else "preparation_failed"
                },
            )
        return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", type=UUID)
    args = parser.parse_args()
    target = os.environ.get("STUDIO_COMFY_URL")
    if not target:
        raise SystemExit("Explicit local ComfyUI target required")
    settings = SimpleNamespace(
        **{
            key.lower(): os.environ[key]
            for key in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
        },
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
    )
    pool = await open_pool(os.environ["WORKER_DATABASE_URL"], "app_worker")
    storage, client = S3Storage(settings), LocalComfy(target)
    try:
        worker = StudioComfyWorker(pool, storage, client)
        while True:
            await worker.run_once(args.intent)
            if args.intent:
                break
            await asyncio.sleep(2)
    finally:
        await client.close()
        storage.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run_service(main()))
