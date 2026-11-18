"""Beijing Qwen Image async editing protocol; explicit live opt-in required.

POST is attempted once per private intent journal.
The same reviewed workspace origin supplies task GETs; downloads never receive keys.
"""

import base64
import hashlib
import json
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import model_validator

from visual_platform.providers.bfl import BflRequest
from visual_platform.providers.contract import ProviderFailure, ProviderUncertain, Receipt
from visual_platform.providers.gemini_usage import unique_object

MODEL = "qwen-image-3.0"
ASYNC_PATH = "/api/v1/services/aigc/image-generation/generation"
MAX_BODY = 1024 * 1024
# Conservative decimal byte ceiling for the documented 10 MB input limit.
MAX_IMAGE = 10_000_000


def reference_metadata(data):
    """Check the header of already isolated/canonical PNGs; not a general PNG decoder."""
    if (
        not 33 <= len(data) <= MAX_IMAGE
        or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        or zlib.crc32(data[12:29]) != struct.unpack(">I", data[29:33])[0]
    ):
        raise ValueError("qwen_reference_header_or_size_invalid")
    width, height = struct.unpack(">II", data[16:24])
    if (
        not 1 <= width <= 8000
        or not 1 <= height <= 8000
        or max(width, height) > 10 * min(width, height)
    ):
        raise ValueError("qwen_reference_dimensions_out_of_range")
    return {"width": width, "height": height, "byte_size": len(data)}


def checked_dimensions(width, height):
    if (
        type(width) is not int
        or type(height) is not int
        or not 256 <= width <= 6256
        or not 256 <= height <= 6256
        or not 512 * 512 <= width * height <= 4_000_000
        or max(width, height) > 8 * min(width, height)
    ):
        raise ValueError("qwen_output_dimensions_out_of_range")
    return width, height


def workspace_origin(value):
    if type(value) is not str or not re.fullmatch(
        r"https://ws-[a-z0-9]{1,64}\.cn-beijing\.maas\.aliyuncs\.com", value
    ):
        raise ValueError("unreviewed_bailian_workspace_origin")
    return value


def strict_document(raw):
    def invalid(value):
        raise ValueError("nonstandard_json")

    try:
        result = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid)
        if type(result) is not dict:
            raise ValueError("object_required")
        return result
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid_qwen_response") from None


def task_id(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise ValueError("invalid_qwen_task_id")
    return value


def image_url(value):
    if (
        type(value) is not str
        or len(value) > 8192
        or any(c.isspace() or ord(c) < 32 for c in value)
        or "\\" in value
    ):
        raise ValueError("untrusted_qwen_image_url")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
        or not re.fullmatch(
            r"(?:dashscope-result-[a-z0-9-]+\.oss-cn-(?:beijing|shanghai|hangzhou|shenzhen)|dashscope-[a-z0-9]{1,32}\.oss-accelerate)\.aliyuncs\.com",
            parsed.hostname or "",
        )
        or not parsed.path.endswith(".png")
    ):
        raise ValueError("untrusted_qwen_image_url")
    return value


class QwenImageRequest(BflRequest):
    @model_validator(mode="after")
    def qwen_limits(self):
        checked_dimensions(self.width, self.height)
        if len(self.images) > 3:
            raise ValueError("qwen_edit_dimensions_or_references_out_of_range")
        for reference in self.images:
            reference_metadata(base64.b64decode(reference, validate=True))
        return self

    def payload(self):
        # Revalidate even model_copy/model_construct values before making an intent.
        request = QwenImageRequest.model_validate(self.model_dump())
        return {
            "model": MODEL,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": "data:image/png;base64," + image} for image in request.images
                        ]
                        + [{"text": request.prompt}],
                    }
                ]
            },
            "parameters": {
                "n": 1,
                "size": f"{request.width}*{request.height}",
                "prompt_extend": False,
                "watermark": False,
            },
        }


@dataclass(frozen=True)
class QwenImagePoll:
    provider_id: str
    status: str
    sample_url: str | None = None
    usage: dict | None = None
    response_digest: str | None = None
    response_body_base64: str | None = None


@dataclass(frozen=True)
class QwenImageArtifact:
    data: bytes
    mime: str
    sha256: str
    result_wire_body_base64: str | None = None
    result_response_sha256: str | None = None


class QwenImageProvider:
    model = MODEL
    offline_transport = True

    def __init__(self, key, directory: Path, *, endpoint_origin, transport=None, live=False):
        if type(live) is not bool or (live and transport is not None):
            raise ValueError("explicit_live_transport_cannot_be_mixed")
        if not live and not isinstance(transport, httpx.MockTransport):
            raise ValueError("qwen_image_requires_explicit_offline_mock_transport")
        self.offline_transport = not live
        self.transport_label = "bailian_live" if live else "offline_mock"
        self.endpoint_origin = workspace_origin(endpoint_origin)
        self.endpoint = self.endpoint_origin + ASYNC_PATH
        if type(key) is not str or not re.fullmatch(r"[\x21-\x7e]{1,4096}", key):
            raise ValueError("invalid_qwen_credential")
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("invalid_qwen_private_directory")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory
        self._key = key
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(retries=0) if live else transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(120 if live else 30, connect=5),
        )
        self.download_client = httpx.Client(
            transport=httpx.HTTPTransport(retries=0) if live else transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30, connect=5),
        )

    def close(self):
        self.client.close()
        self.download_client.close()

    def _path(self, intent, suffix):
        return self.directory / (str(UUID(str(intent))) + suffix)

    @staticmethod
    def _write(path, raw):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _json_body(self, method, url, payload=None):
        headers = {
            "authorization": "Bearer " + self._key,
            "accept": "application/json",
            "accept-encoding": "identity",
        }
        if method == "POST":
            headers["X-DashScope-Async"] = "enable"
        try:
            with self.client.stream(method, url, json=payload, headers=headers) as response:
                if method == "POST" and response.status_code in (400, 401, 403, 422, 429):
                    raise ProviderFailure("Qwen submission explicitly rejected")
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity").lower() != "identity"
                    or response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != "application/json"
                ):
                    raise ProviderUncertain("Qwen response unavailable")
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    raw.extend(chunk)
                    if len(raw) > MAX_BODY:
                        raise ProviderUncertain("Qwen response exceeds limit")
                return bytes(raw)
        except httpx.ReadTimeout:
            raise ProviderUncertain("qwen_read_timeout") from None
        except httpx.WriteTimeout:
            raise ProviderUncertain("qwen_write_timeout") from None
        except httpx.ConnectTimeout:
            raise ProviderUncertain("qwen_connect_timeout") from None
        except httpx.HTTPError:
            raise ProviderUncertain("Qwen response cannot be confirmed") from None

    def submit(self, intent_id, request):
        if not isinstance(request, QwenImageRequest):
            raise TypeError("validated QwenImageRequest required")
        payload = request.payload()
        intent = UUID(str(intent_id))
        paths = [self._path(intent, suffix) for suffix in (".intent", ".response.body")]
        diagnostic = self._path(intent, ".submission-diagnostic.json")
        if any(path.exists() or path.is_symlink() for path in [*paths, diagnostic]):
            raise ProviderUncertain("Qwen intent already attempted; do not resubmit")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            self._write(paths[0], hashlib.sha256(encoded).hexdigest().encode())
        except FileExistsError:
            raise ProviderUncertain("Qwen intent already attempted; do not resubmit") from None
        started = time.monotonic()
        try:
            raw = self._json_body("POST", self.endpoint, payload)
        except (ProviderFailure, ProviderUncertain) as exc:
            # Only fixed codes, never exception text, URLs, prompts or credentials.
            reason = str(exc)
            if reason not in {"qwen_read_timeout", "qwen_write_timeout", "qwen_connect_timeout"}:
                reason = (
                    "explicit_rejection" if isinstance(exc, ProviderFailure) else "response_unknown"
                )
            self._write(
                self._path(intent, ".submission-diagnostic.json"),
                json.dumps(
                    {
                        "schema_version": 1,
                        "outcome": "rejected" if isinstance(exc, ProviderFailure) else "unknown",
                        "reason": reason,
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                        "automatic_resubmission": False,
                    },
                    sort_keys=True,
                ).encode(),
            )
            raise
        try:
            self._write(paths[1], raw)
            body = strict_document(raw)
            output = body["output"]
            identity = task_id(output["task_id"])
            if body.get("code") or output["task_status"] not in ("PENDING", "RUNNING", "SUCCEEDED"):
                raise ValueError("unconfirmed_qwen_submit_state")
            return Receipt(
                identity,
                hashlib.sha256(raw).hexdigest(),
                self.endpoint_origin + "/api/v1/tasks/" + identity,
            )
        except (KeyError, ValueError, TypeError, OSError):
            raise ProviderUncertain("Qwen receipt requires reconciliation") from None

    def poll(self, receipt):
        identity = task_id(receipt.provider_id)
        expected = self.endpoint_origin + "/api/v1/tasks/" + identity
        if receipt.polling_url != expected:
            raise ProviderUncertain("Qwen receipt workspace mismatch")
        raw = self._json_body("GET", expected)
        try:
            body = strict_document(raw)
            output = body["output"]
            if output["task_id"] != identity or body.get("code"):
                raise ValueError("qwen_result_identity_mismatch")
            status = output["task_status"]
            if status in ("PENDING", "RUNNING"):
                return QwenImagePoll(identity, "Pending")
            if status in ("FAILED", "CANCELED"):
                raise ProviderFailure("Qwen existing task failed")
            if status != "SUCCEEDED":
                raise ValueError("unconfirmed_qwen_task_state")
            choices = output["choices"]
            if type(choices) is not list or len(choices) != 1:
                raise ValueError("qwen_one_output_required")
            choice = choices[0]
            content = choice["message"]["content"]
            if (
                choice["finish_reason"] != "stop"
                or choice["message"]["role"] != "assistant"
                or type(content) is not list
                or len(content) != 1
            ):
                raise ValueError("qwen_incomplete_output")
            url = image_url(content[0]["image"])
            return QwenImagePoll(
                identity,
                "Ready",
                url,
                body.get("usage"),
                hashlib.sha256(raw).hexdigest(),
                base64.b64encode(raw).decode("ascii"),
            )
        except (ValueError, KeyError, TypeError, IndexError):
            raise ProviderUncertain("Qwen existing result requires reconciliation") from None

    def receipt_record(self, intent_id):
        """Rebuild a receipt from original bytes; never authorizes resubmission."""
        try:
            path = self._path(intent_id, ".response.body")
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BODY:
                raise ValueError("qwen_receipt_unavailable")
            with path.open("rb") as stream:
                raw = stream.read(MAX_BODY + 1)
            if not 1 <= len(raw) <= MAX_BODY:
                raise ValueError("qwen_receipt_oversized")
            body = strict_document(raw)
            output = body["output"]
            identity = task_id(output["task_id"])
            if body.get("code") or output["task_status"] not in ("PENDING", "RUNNING", "SUCCEEDED"):
                raise ValueError("qwen_receipt_unconfirmed")
            return {
                "provider_id": identity,
                "polling_url": self.endpoint_origin + "/api/v1/tasks/" + identity,
                "response_digest": hashlib.sha256(raw).hexdigest(),
                "response_body_base64": base64.b64encode(raw).decode("ascii"),
                "provider_cost": None,
                "source": "bailian_async_submit_no_billing_usage",
            }
        except (OSError, ValueError, KeyError, TypeError):
            raise ProviderUncertain("Qwen receipt unavailable; do not resubmit") from None

    def fetch_result(self, poll):
        if not isinstance(poll, QwenImagePoll) or poll.status != "Ready":
            raise ValueError("Ready Qwen result required")
        try:
            url = image_url(poll.sample_url)
            with self.download_client.stream(
                "GET", url, headers={"accept": "image/png", "accept-encoding": "identity"}
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").split(";", 1)[0] != "image/png"
                    or response.headers.get("content-encoding", "identity").lower() != "identity"
                ):
                    raise ValueError("qwen_image_unavailable")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > 20 * 1024 * 1024:
                        raise ValueError("qwen_image_oversized")
            raw = bytes(data)
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("qwen_png_required")
            # Existing isolated canonical decoder still validates actual image pixels.
            return QwenImageArtifact(
                raw,
                "image/png",
                hashlib.sha256(raw).hexdigest(),
                poll.response_body_base64,
                poll.response_digest,
            )
        except (httpx.HTTPError, ValueError):
            raise ProviderUncertain("Qwen image download cannot be confirmed") from None
