"""Explicit local ComfyUI transport. No retries, redirects, discovery or global cancellation."""

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "::1")
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("explicit_loopback_comfy_url_required")
    return value.rstrip("/")


def digest(document):
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class ComfyUnavailable(RuntimeError):
    pass


class ComfyUncertain(RuntimeError):
    """A mutation may have happened. Query the preallocated ID, never submit again."""


@dataclass(frozen=True)
class SubmissionReceipt:
    prompt_id: str
    raw: bytes


class LocalComfy:
    def __init__(self, url: str, transport=None):
        self.url = local_url(url)
        self.client = httpx.AsyncClient(
            base_url=self.url,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(15, connect=4),
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, *, limit=16 * 1024 * 1024, **kwargs):
        try:
            async with self.client.stream(method, path, **kwargs) as response:
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ComfyUnavailable("comfy_response_too_large")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)
        except httpx.HTTPError as error:
            raise ComfyUnavailable("comfy_transport_unavailable") from error

    async def read_json(self, path):
        status, raw = await self.request("GET", path)
        if status != 200:
            raise ComfyUnavailable("comfy_read_rejected")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("object required")
            return value
        except (ValueError, UnicodeError) as error:
            raise ComfyUnavailable("comfy_invalid_response") from error

    async def diagnose(self):
        stats = await self.read_json("/system_stats")
        nodes = await self.read_json("/object_info")
        system = stats.get("system", {})
        required = ("LoadImage", "ImageScale", "SaveImage")
        summary = {
            "server_version": system.get("comfyui_version", "unknown"),
            "frontend_version": system.get("required_frontend_version", "unknown"),
            "python_version": str(system.get("python_version", "unknown")).split()[0],
            "pytorch_version": system.get("pytorch_version", "unknown"),
            "node_count": len(nodes),
            "object_info_sha256": digest(nodes),
            "missing_processing_nodes": [name for name in required if name not in nodes],
            "generation_template_verified": False,
            "model_byte_identity": "unknown",
            "real_model_execution": False,
            "scoped_running_cancel_verified": False,
        }
        return summary, nodes

    async def upload_exact(self, raw: bytes, sha256: str, intent_id: UUID):
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError("source_hash_mismatch")
        filename = sha256 + ".png"
        subfolder = "yingxu/" + str(intent_id)
        status, response = await self.request(
            "POST",
            "/upload/image",
            files={"image": (filename, raw, "image/png")},
            data={"type": "input", "subfolder": subfolder, "overwrite": "false"},
        )
        if status != 200:
            raise ComfyUnavailable("comfy_upload_rejected")
        try:
            record = json.loads(response)
            if (
                record["name"] != filename
                or record["subfolder"] != subfolder
                or record["type"] != "input"
            ):
                raise ValueError("upload mapping mismatch")
        except (ValueError, KeyError, TypeError) as error:
            raise ComfyUnavailable("comfy_upload_mapping_mismatch") from error
        status, returned = await self.request(
            "GET",
            "/view",
            limit=80 * 1024 * 1024,
            params={"filename": filename, "subfolder": subfolder, "type": "input"},
        )
        if status != 200 or hashlib.sha256(returned).hexdigest() != sha256:
            raise ComfyUnavailable("comfy_input_integrity_mismatch")
        return subfolder + "/" + filename

    async def submit_once(self, prompt, prompt_id: UUID, intent_id: UUID):
        """Caller must durably record its intent and submission-start marker first."""
        payload = {
            "prompt": prompt,
            "prompt_id": str(prompt_id),
            "client_id": str(intent_id),
            "extra_data": {"yingxu_intent_id": str(intent_id), "workflow_sha256": digest(prompt)},
        }
        try:
            status, raw = await self.request("POST", "/prompt", json=payload)
        except ComfyUnavailable as error:
            raise ComfyUncertain("comfy_submission_outcome_unknown") from error
        if status == 400:
            raise ComfyUnavailable("comfy_workflow_rejected")
        if status != 200:
            raise ComfyUncertain("comfy_submission_outcome_unknown")
        try:
            record = json.loads(raw)
            if record["prompt_id"] != str(prompt_id):
                raise ValueError("prompt identity mismatch")
        except (ValueError, KeyError, TypeError) as error:
            raise ComfyUncertain("comfy_submission_identity_unknown") from error
        return SubmissionReceipt(str(prompt_id), raw)

    async def history(self, prompt_id: UUID):
        return await self.read_json("/history/" + str(prompt_id))

    async def cancel_pending(self, prompt_id: UUID):
        """Only this persisted prompt may be removed. Running jobs are left untouched."""
        queue = await self.read_json("/queue")
        if any(row[1] == str(prompt_id) for row in queue.get("queue_running", [])):
            return {"cancelled": False, "reason": "running_scoped_cancel_not_verified"}
        if not any(row[1] == str(prompt_id) for row in queue.get("queue_pending", [])):
            return {"cancelled": False, "reason": "not_pending"}
        status, _ = await self.request("POST", "/queue", json={"delete": [str(prompt_id)]})
        if status != 200:
            raise ComfyUncertain("comfy_cancel_outcome_unknown")
        return {"cancelled": True, "reason": "scoped_queue_delete_requested"}
