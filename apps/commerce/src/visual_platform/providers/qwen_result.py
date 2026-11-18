"""Terminal query evidence: image meters are separate from submission and fiat billing."""

import base64
import hashlib
import re

from visual_platform.providers.qwen_image import (
    checked_dimensions,
    image_url,
    strict_document,
    workspace_origin,
)
from visual_platform.providers.qwen_usage import image_usage

FIELDS = {
    "archive_version",
    "model",
    "endpoint_origin",
    "provider_id",
    "submission_response_sha256",
    "response_body_base64",
    "wire_response_sha256",
    "downloaded_sha256",
    "generated_sha256",
    "width",
    "height",
    "input_image_count",
    "billing_usage_integrity",
    "quantities",
}


def validate_qwen_result(record):
    try:
        if type(record) is not dict or set(record) != FIELDS:
            raise ValueError("invalid_fields")
        if type(record["archive_version"]) is not int or record["archive_version"] != 1:
            raise ValueError("invalid_version")
        if record["model"] != "qwen-image-3.0":
            raise ValueError("unreviewed_model")
        workspace_origin(record["endpoint_origin"])
        if type(record["provider_id"]) is not str or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,200}", record["provider_id"]
        ):
            raise ValueError("invalid_task_identity")
        for field in (
            "submission_response_sha256",
            "wire_response_sha256",
            "downloaded_sha256",
            "generated_sha256",
        ):
            if type(record[field]) is not str or not re.fullmatch(r"[0-9a-f]{64}", record[field]):
                raise ValueError("invalid_sha256")
        checked_dimensions(record["width"], record["height"])
        if type(record["input_image_count"]) is not int or record["input_image_count"] != 1:
            raise ValueError("project_uses_one_canonical_source")
        encoded = record["response_body_base64"]
        if type(encoded) is not str or not 4 <= len(encoded) <= 1398104:
            raise ValueError("invalid_wire_length")
        raw = base64.b64decode(encoded, validate=True)
        if (
            not 1 <= len(raw) <= 1024 * 1024
            or base64.b64encode(raw).decode() != encoded
            or hashlib.sha256(raw).hexdigest() != record["wire_response_sha256"]
        ):
            raise ValueError("wire_mismatch")
        body = strict_document(raw)
        output = body["output"]
        if (
            output["task_id"] != record["provider_id"]
            or output["task_status"] != "SUCCEEDED"
            or body.get("code") is not None
        ):
            raise ValueError("result_identity_mismatch")
        choices = output["choices"]
        if type(choices) is not list or len(choices) != 1:
            raise ValueError("one_result_required")
        choice = choices[0]
        content = choice["message"]["content"]
        if (
            choice["finish_reason"] != "stop"
            or choice["message"]["role"] != "assistant"
            or type(content) is not list
            or len(content) != 1
        ):
            raise ValueError("incomplete_result")
        image_url(content[0]["image"])
        observed = image_usage(
            body.get("usage"), input_count=1, width=record["width"], height=record["height"]
        )
        if (
            record["billing_usage_integrity"] != observed["billing_usage_integrity"]
            or record["quantities"] != observed["quantities"]
        ):
            raise ValueError("result_usage_mismatch")
        if record["quantities"] is not None and any(
            type(value) is not int for value in record["quantities"].values()
        ):
            raise ValueError("invalid_meter_type")
        return record
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        raise ValueError("invalid_qwen_result_archive") from None


def build_qwen_result(attempt, artifact, canonical, meta, endpoint_origin):
    """meta/canonical must come from the existing isolated image decoder, before protection."""
    if hashlib.sha256(artifact.data).hexdigest() != artifact.sha256:
        raise ValueError("downloaded_bytes_mismatch")
    if (
        type(artifact.result_wire_body_base64) is not str
        or not 4 <= len(artifact.result_wire_body_base64) <= 1398104
    ):
        raise ValueError("invalid_qwen_result_wire_length")
    raw = base64.b64decode(artifact.result_wire_body_base64, validate=True)
    usage = image_usage(
        strict_document(raw).get("usage"), input_count=1, width=meta["width"], height=meta["height"]
    )
    return validate_qwen_result(
        {
            "archive_version": 1,
            "model": "qwen-image-3.0",
            "endpoint_origin": endpoint_origin,
            "provider_id": attempt["provider_id"],
            "submission_response_sha256": attempt["response_digest"],
            "response_body_base64": artifact.result_wire_body_base64,
            "wire_response_sha256": artifact.result_response_sha256,
            "downloaded_sha256": artifact.sha256,
            "generated_sha256": hashlib.sha256(canonical).hexdigest(),
            "width": meta["width"],
            "height": meta["height"],
            "input_image_count": 1,
            **usage,
        }
    )


def qwen_result_observation(record):
    record = validate_qwen_result(record)
    return {
        "transport": "offline_mock",
        "model_context": "frozen_request_not_provider_reported_model",
        "submission_response_sha256": record["submission_response_sha256"],
        "wire_response_sha256": record["wire_response_sha256"],
        "generated_sha256": record["generated_sha256"],
        "billing_usage_integrity": record["billing_usage_integrity"],
        "quantities": record["quantities"],
        "actual_spending_verified": False,
    }
