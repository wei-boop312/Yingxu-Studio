"""Original async submission receipts; raw wire proof is distinct from later result usage."""

import base64
import hashlib
import re

from visual_platform.providers.bfl import checked_url, reported_bfl_cost, strict_bfl_response
from visual_platform.providers.qwen_image import workspace_origin

MAX_WIRE = 1024 * 1024
FIELDS = {
    "archive_version",
    "provider",
    "model",
    "endpoint_origin",
    "provider_id",
    "polling_url",
    "response_digest",
    "wire_response_sha256",
    "response_body_base64",
}


def validate_generation_archive(record):
    try:
        if type(record) is not dict or set(record) != FIELDS:
            raise ValueError("invalid_fields")
        if type(record["archive_version"]) is not int or record["archive_version"] != 1:
            raise ValueError("invalid_version")
        for field in FIELDS - {"archive_version"}:
            if type(record[field]) is not str:
                raise ValueError("invalid_field_type")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", record["provider_id"]):
            raise ValueError("invalid_identity")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", record["response_digest"])
            or record["response_digest"] != record["wire_response_sha256"]
            or not 4 <= len(record["response_body_base64"]) <= 1398104
        ):
            raise ValueError("invalid_wire_digest")
        raw = base64.b64decode(record["response_body_base64"], validate=True)
        if (
            not 1 <= len(raw) <= MAX_WIRE
            or base64.b64encode(raw).decode() != record["response_body_base64"]
            or hashlib.sha256(raw).hexdigest() != record["response_digest"]
        ):
            raise ValueError("wire_mismatch")
        response = strict_bfl_response(raw)
        if record["provider"] == "bfl":
            if (
                record["model"] != "flux-3-image"
                or record["endpoint_origin"] != "https://api.bfl.ai"
            ):
                raise ValueError("unreviewed_bfl_identity")
            if (
                response["id"] != record["provider_id"]
                or response["polling_url"] != record["polling_url"]
            ):
                raise ValueError("response_identity_mismatch")
            checked_url(record["polling_url"], "poll", record["provider_id"])
        elif record["provider"] == "aliyun_bailian":
            workspace_origin(record["endpoint_origin"])
            if record["model"] != "qwen-image-3.0":
                raise ValueError("unreviewed_qwen_identity")
            output = response["output"]
            if (
                response.get("code")
                or output["task_id"] != record["provider_id"]
                or output["task_status"] not in ("PENDING", "RUNNING", "SUCCEEDED")
                or record["polling_url"]
                != record["endpoint_origin"] + "/api/v1/tasks/" + record["provider_id"]
            ):
                raise ValueError("response_identity_mismatch")
        else:
            raise ValueError("unreviewed_provider")
        return record
    except (ValueError, KeyError, TypeError, ArithmeticError, RecursionError):
        raise ValueError("invalid_generation_wire_archive") from None


def generation_observation(record):
    record = validate_generation_archive(record)
    raw = base64.b64decode(record["response_body_base64"], validate=True)
    return {
        "transport": "offline_mock",
        "response_digest": record["response_digest"],
        "wire_response_sha256": record["wire_response_sha256"],
        "generation_archive_version": 1,
        "provider_cost": (
            reported_bfl_cost(strict_bfl_response(raw), record["response_digest"])
            if record["provider"] == "bfl"
            else None
        ),
        "source": "pg_original_async_submit_response",
    }
