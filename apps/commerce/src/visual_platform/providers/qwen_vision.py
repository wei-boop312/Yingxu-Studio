"""Optional standalone Qwen visual facts protocol. Explicit MockTransport only."""

import base64
import hashlib
import hmac
import os
import re
from pathlib import Path
from uuid import UUID

import httpx

from visual_platform.domain.facts import Facts
from visual_platform.providers.contract import ProviderFailure, ProviderUncertain, Receipt
from visual_platform.providers.gemini import SYSTEM, encoded, fallback
from visual_platform.providers.qwen_image import strict_document, workspace_origin
from visual_platform.providers.qwen_vision_usage import vision_usage
from visual_platform.services.upload import decode

MODEL = "qwen3.8-flash"
PATH = "/compatible-mode/v1/chat/completions"
MAX_BODY = 1024 * 1024
MAX_ARCHIVE = 4 * 1024 * 1024
FORMAT = "json_object_with_local_facts_validation"
VISION_SYSTEM = (
    SYSTEM
    + "\nJSON facts schema (validated locally):\n"
    + encoded(Facts.model_json_schema()).decode()
)


LIVE_VISION_SYSTEM = (
    VISION_SYSTEM
    + "\n必须输出一个完整JSON对象，禁止输出数组、空数组或Markdown。逐项填写schema_version、image_width、image_height、analysis_complete、background、text_items、logo_items、watermark_items、product_extent、uncertainties。品牌文字应描述为商品本体上的内容；无法识别时明确标为unknown并加入uncertainties，不要编造。"
)


def parse_visual_response(raw, width, height):
    try:
        document = strict_document(raw)
    except ValueError:
        return (
            fallback(width, height, "qwen_ambiguous_wire_response"),
            {
                "usage": None,
                "quantities": None,
                "billing_usage_integrity": "invalid",
                "reported_model": None,
            },
            None,
            "invalid",
        )
    metering = vision_usage(document)
    reported_model = document.get("model")
    if (
        type(reported_model) is not str
        or re.fullmatch(r"qwen[A-Za-z0-9_.-]{1,100}", reported_model) is None
    ):
        reported_model = None
    metering["reported_model"] = reported_model
    if reported_model != MODEL:
        metering.update(quantities=None, billing_usage_integrity="reported_model_unverified")
    response_id = document.get("id")
    if (
        type(response_id) is not str
        or re.fullmatch(r"chatcmpl-[A-Za-z0-9_-]{1,192}", response_id) is None
    ):
        response_id = None
    try:
        choices = document["choices"]
        if (
            document.get("error") is not None
            or document.get("model") != MODEL
            or response_id is None
            or type(choices) is not list
            or len(choices) != 1
            or type(choices[0]) is not dict
            or choices[0].get("finish_reason") != "stop"
        ):
            raise ValueError("incomplete_response")
        message = choices[0]["message"]
        if (
            message.get("role") != "assistant"
            or message.get("refusal") not in (None, "")
            or message.get("tool_calls") not in (None, [])
            or message.get("reasoning_content") not in (None, "")
            or type(message.get("content")) is not str
        ):
            raise ValueError("non_text_response")
        text = message["content"]
        if len(text.encode()) > MAX_BODY:
            raise ValueError("facts_too_large")
        facts = Facts.model_validate_json(encoded(strict_document(text)))
        if (facts.image_width, facts.image_height) != (width, height):
            raise ValueError("facts_dimensions_mismatch")
    except (KeyError, TypeError, ValueError, AttributeError):
        return (
            fallback(width, height, "qwen_facts_schema_or_response_unverified"),
            metering,
            response_id,
            "valid",
        )
    confidence = (
        [facts.background.confidence]
        + [
            mark.confidence
            for group in (facts.text_items, facts.logo_items, facts.watermark_items)
            for mark in group
        ]
        + ([facts.product_extent.confidence] if facts.product_extent else [])
    )
    if any(value < 0.75 for value in confidence):
        facts = facts.model_copy(
            update={
                "analysis_complete": False,
                "uncertainties": list(dict.fromkeys([*facts.uncertainties, "qwen_low_confidence"]))[
                    :100
                ],
            }
        )
    return facts, metering, response_id, "valid"


def validate_vision_archive(record):
    fields = {
        "archive_version",
        "provider",
        "model",
        "endpoint_origin",
        "provider_id",
        "provider_response_id",
        "response_digest",
        "response_body_base64",
        "wire_response_sha256",
        "final_sha256",
        "width",
        "height",
        "facts",
        "facts_schema_sha256",
        "system_prompt_sha256",
        "usage",
        "billing_usage_integrity",
        "quantities",
        "response_structure_integrity",
        "manual_review_required",
        "cost",
        "response_format",
        "transport",
        "reported_model",
        "request_sha256",
    }
    if (
        type(record) is not dict
        or set(record) != fields
        or type(record.get("archive_version")) is not int
        or record["archive_version"] != 1
    ):
        raise ValueError("invalid_qwen_vision_archive")
    workspace_origin(record["endpoint_origin"])
    if (
        record["provider"] != "aliyun_bailian_vision"
        or record["model"] != MODEL
        or record["response_format"] != FORMAT
        or record["transport"] not in ("offline_mock", "bailian_live")
    ):
        raise ValueError("qwen_vision_archive_identity_mismatch")
    provider_id = record["provider_id"]
    if (
        type(provider_id) is not str
        or not provider_id.startswith("qwen-vision-")
        or str(UUID(provider_id[12:])) != provider_id[12:]
    ):
        raise ValueError("invalid_qwen_vision_archive_id")
    for key in (
        "final_sha256",
        "response_digest",
        "wire_response_sha256",
        "facts_schema_sha256",
        "system_prompt_sha256",
        "request_sha256",
    ):
        if type(record[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", record[key]):
            raise ValueError("invalid_qwen_vision_archive_hash")
    width, height = record["width"], record["height"]
    if (
        type(width) is not int
        or type(height) is not int
        or min(width, height) < 1
        or width * height > 4_000_000
    ):
        raise ValueError("invalid_qwen_vision_archive_dimensions")
    if type(record["response_body_base64"]) is not str or len(
        record["response_body_base64"]
    ) > 4 * ((MAX_BODY + 2) // 3):
        raise ValueError("invalid_qwen_vision_archive_body")
    raw = base64.b64decode(record["response_body_base64"], validate=True)
    if (
        not 0 < len(raw) <= MAX_BODY
        or base64.b64encode(raw).decode() != record["response_body_base64"]
    ):
        raise ValueError("invalid_qwen_vision_archive_body")
    if (
        hashlib.sha256(raw).hexdigest() != record["wire_response_sha256"]
        or record["response_digest"] != record["wire_response_sha256"]
    ):
        raise ValueError("qwen_vision_wire_hash_mismatch")
    facts, metering, response_id, integrity = parse_visual_response(raw, width, height)
    expected = {
        "facts": facts.model_dump(mode="json"),
        **metering,
        "provider_response_id": response_id,
        "response_structure_integrity": integrity,
        "manual_review_required": not facts.analysis_complete or bool(facts.uncertainties),
        "facts_schema_sha256": hashlib.sha256(encoded(Facts.model_json_schema())).hexdigest(),
        "system_prompt_sha256": hashlib.sha256(
            (
                LIVE_VISION_SYSTEM if record["transport"] == "bailian_live" else VISION_SYSTEM
            ).encode()
        ).hexdigest(),
        "cost": {"money_amount": None, "currency": None, "actual_spending_verified": False},
    }
    if any(encoded(record[key]) != encoded(value) for key, value in expected.items()):
        raise ValueError("qwen_vision_archive_derived_field_mismatch")
    return record


class QwenVisionProvider:
    model = MODEL
    offline_transport = True

    def __init__(self, key, directory: Path, *, endpoint_origin, transport=None, live=False):
        if type(live) is not bool or (live and transport is not None):
            raise ValueError("explicit_live_transport_cannot_be_mixed")
        if not live and not isinstance(transport, httpx.MockTransport):
            raise ValueError("qwen_vision_requires_explicit_offline_mock_transport")
        self.offline_transport = not live
        self.transport_label = "bailian_live" if live else "offline_mock"
        self.endpoint_origin = workspace_origin(endpoint_origin)
        self.endpoint = self.endpoint_origin + PATH
        if type(key) is not str or not re.fullmatch(r"[\x21-\x7e]{1,4096}", key):
            raise ValueError("invalid_qwen_vision_credential")
        if (
            directory.is_symlink()
            or directory.is_junction()
            or (directory.exists() and not directory.is_dir())
        ):
            raise ValueError("invalid_qwen_vision_private_directory")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory
        self._key = key
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0) if live else transport,
            trust_env=False,
            follow_redirects=False,
            timeout=35,
        )

    async def aclose(self):
        await self.client.aclose()

    def _path(self, intent, suffix):
        return self.directory / (str(UUID(str(intent))) + suffix)

    @staticmethod
    def _write(path, raw):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    async def submit(self, intent_id, final_png, *, expected_sha256, width, height):
        intent = UUID(str(intent_id))
        if (
            type(final_png) is not bytes
            or not 0 < len(final_png) <= 10_000_000
            or type(expected_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or not hmac.compare_digest(hashlib.sha256(final_png).hexdigest(), expected_sha256)
            or type(width) is not int
            or type(height) is not int
            or min(width, height) < 1
            or width * height > 4_000_000
        ):
            raise ValueError("invalid_final_qwen_vision_input")
        canonical, meta = await decode(final_png, "image/png")
        if (meta["width"], meta["height"]) != (width, height) or hashlib.sha256(
            canonical
        ).hexdigest() != expected_sha256:
            raise ValueError("qwen_vision_requires_exact_canonical_final")
        prompt = LIVE_VISION_SYSTEM if not self.offline_transport else VISION_SYSTEM
        payload = {
            "model": MODEL,
            "stream": False,
            "enable_thinking": False,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(final_png).decode()
                            },
                        },
                        {
                            "type": "text",
                            "text": f"Inspect this final image, {width} by {height} pixels. Return JSON visual facts only.",
                        },
                    ],
                },
            ],
        }
        provenance = {
            "final_sha256": expected_sha256,
            "width": width,
            "height": height,
            "model": MODEL,
            "endpoint_origin": self.endpoint_origin,
            "request_sha256": hashlib.sha256(encoded(payload)).hexdigest(),
        }
        try:
            self._write(self._path(intent, ".intent"), encoded(provenance))
        except FileExistsError:
            raise ProviderUncertain(
                "Qwen vision intent already attempted; do not resubmit"
            ) from None
        try:
            async with self.client.stream(
                "POST",
                self.endpoint,
                json=payload,
                headers={
                    "Authorization": "Bearer " + self._key,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ProviderUncertain("Qwen vision compressed response rejected")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise ProviderUncertain("Qwen vision response exceeds limit")
                raw = bytes(body)
                self._write(self._path(intent, ".response.body"), raw)
                if response.status_code in (400, 401, 403, 422, 429):
                    raise ProviderFailure("Qwen vision explicitly rejected submission")
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != "application/json"
                ):
                    raise ProviderUncertain("Qwen vision response unavailable")
            facts, metering, response_id, integrity = parse_visual_response(raw, width, height)
            digest = hashlib.sha256(raw).hexdigest()
            record = {
                "archive_version": 1,
                "provider": "aliyun_bailian_vision",
                "model": MODEL,
                "endpoint_origin": self.endpoint_origin,
                "provider_id": "qwen-vision-" + str(intent),
                "provider_response_id": response_id,
                "response_digest": digest,
                "response_body_base64": base64.b64encode(raw).decode(),
                "wire_response_sha256": digest,
                "final_sha256": expected_sha256,
                "width": width,
                "height": height,
                "facts": facts.model_dump(mode="json"),
                "facts_schema_sha256": hashlib.sha256(
                    encoded(Facts.model_json_schema())
                ).hexdigest(),
                "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "request_sha256": provenance["request_sha256"],
                **metering,
                "response_structure_integrity": integrity,
                "manual_review_required": not facts.analysis_complete or bool(facts.uncertainties),
                "cost": {"money_amount": None, "currency": None, "actual_spending_verified": False},
                "response_format": FORMAT,
                "transport": self.transport_label,
            }
            validate_vision_archive(record)
            serialized = encoded(record)
            if len(serialized) > MAX_ARCHIVE:
                raise ValueError("qwen_vision_archive_exceeds_limit")
            self._write(self._path(intent, ".facts.json"), serialized)
            return Receipt(record["provider_id"], digest)
        except (httpx.HTTPError, OSError, ValueError):
            raise ProviderUncertain("Qwen vision outcome unavailable; do not resubmit") from None

    def fetch_facts(self, provider_id):
        try:
            if type(provider_id) is not str or not provider_id.startswith("qwen-vision-"):
                raise ValueError("invalid_qwen_vision_id")
            intent = UUID(provider_id[12:])
            path = self._path(intent, ".facts.json")
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
                raise ValueError("invalid_qwen_vision_archive_file")
            with path.open("rb") as stream:
                raw = stream.read(MAX_ARCHIVE + 1)
            if len(raw) > MAX_ARCHIVE:
                raise ValueError("oversized_qwen_vision_archive")
            record = validate_vision_archive(strict_document(raw))
            intent_path = self._path(intent, ".intent")
            if (
                intent_path.is_symlink()
                or not intent_path.is_file()
                or intent_path.stat().st_size > 8192
            ):
                raise ValueError("invalid_qwen_vision_intent_file")
            with intent_path.open("rb") as stream:
                intent_raw = stream.read(8193)
            if len(intent_raw) > 8192:
                raise ValueError("oversized_qwen_vision_intent")
            provenance = strict_document(intent_raw)
            if (
                record["transport"] != self.transport_label
                or record["provider_id"] != provider_id
                or record["endpoint_origin"] != self.endpoint_origin
                or any(
                    record[field] != provenance[field]
                    for field in (
                        "final_sha256",
                        "width",
                        "height",
                        "model",
                        "endpoint_origin",
                        "request_sha256",
                    )
                )
            ):
                raise ValueError("qwen_vision_local_binding_mismatch")
            return record
        except (OSError, ValueError, KeyError, TypeError):
            raise ProviderUncertain("Qwen vision archive unavailable; do not resubmit") from None
