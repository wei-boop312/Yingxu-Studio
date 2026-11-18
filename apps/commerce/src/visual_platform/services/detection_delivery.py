"""Approve local manual-review delivery without manufacturing a provider receipt."""

import hashlib
import os
from io import BytesIO
from pathlib import Path
from uuid import UUID

import psycopg
from PIL import Image

from visual_platform.db.result import required_row
from visual_platform.domain.image_limits import MAX_CANONICAL_PIXELS, MAX_OUTPUT_EDGE
from visual_platform.services.rule_operations import assert_operator


def detection_delivery_context(dsn: str, task: UUID):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return required_row(
            connection.execute("SELECT ops.detection_delivery_context(%s)", (task,))
        )[0]


def approve_detection_delivery(
    dsn: str,
    task: UUID,
    attempt: UUID,
    final_sha256: str,
    operation: UUID,
    reason: str,
    evidence: str,
) -> str:
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return str(
            required_row(
                connection.execute(
                    "SELECT ops.approve_detection_manual_delivery(%s,%s,%s,%s,%s,%s)",
                    (task, attempt, final_sha256, operation, reason, evidence),
                )
            )[0]
        )


def export_detection_review(dsn: str, storage, task: UUID, output: Path, private_root: Path):
    """Read one immutable final for private review; never authorize or settle delivery."""
    root = private_root.resolve(strict=True)
    parent = output.parent.resolve(strict=True)
    if not parent.is_relative_to(root) or output.suffix.lower() != ".png" or output.exists():
        raise ValueError("private_review_output_required")
    target = parent / output.name
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        record = required_row(
            connection.execute("SELECT ops.detection_review_artifact(%s)", (task,))
        )[0]
    if record["bucket"] != storage.bucket:
        raise ValueError("review_storage_mismatch")
    data, _, version = storage.get(
        record["object_key"], version=record["storage_version_id"], limit=80 * 1024 * 1024
    )
    if (
        version != record["storage_version_id"]
        or hashlib.sha256(data).hexdigest() != record["sha256"]
    ):
        raise ValueError("review_artifact_mismatch")
    with Image.open(BytesIO(data)) as image:
        if (
            image.format != "PNG"
            or image.mode != "RGB"
            or image.size != (record["width"], record["height"])
            or not all(1 <= edge <= MAX_OUTPUT_EDGE for edge in image.size)
            or image.width * image.height > MAX_CANONICAL_PIXELS
        ):
            raise ValueError("review_artifact_mismatch")
        image.verify()
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)
    return {
        "final_sha256": record["sha256"],
        "width": record["width"],
        "height": record["height"],
        "paid_calls": 0,
        "delivery_approved": False,
    }
