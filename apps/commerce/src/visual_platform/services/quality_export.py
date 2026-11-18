"""Export immutable private samples with blank human labels, never an accuracy claim."""

import hashlib
import io
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import psycopg
from PIL import Image

from visual_platform.db.result import required_row
from visual_platform.domain.image_limits import MAX_CANONICAL_PIXELS, MAX_OUTPUT_EDGE
from visual_platform.services.quality_annotation import annotation_document
from visual_platform.services.quality_evaluation import (
    FEATURES,
    PREDICTIONS,
    QualitySample,
    strict_json,
)
from visual_platform.services.rule_operations import assert_operator


def quality_export_context(dsn, user, task, check):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return required_row(
            connection.execute("SELECT ops.quality_export_context(%s,%s,%s)", (user, task, check))
        )[0]


def private_destination(output: Path, private_root: Path):
    if private_root.is_symlink() or private_root.is_junction():
        raise ValueError("private_quality_root_required")
    root = private_root.resolve(strict=True)
    if not root.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", output.name):
        raise ValueError("private_quality_output_required")
    parent = output.absolute().parent
    if not parent.is_relative_to(root):
        raise ValueError("private_quality_output_required")
    path = root
    for part in parent.relative_to(root).parts:
        path = path / part
        if path.is_symlink() or path.is_junction() or not path.is_dir():
            raise ValueError("private_quality_output_required")
    if parent.resolve(strict=True) != parent or output.exists() or output.is_symlink():
        raise ValueError("private_quality_output_required")
    return parent / output.name


def prepare_sample(context, storage, dataset_kind):
    if dataset_kind not in ("synthetic_fixture", "real_product_set"):
        raise ValueError("invalid_quality_dataset_kind")
    final, pointer = context["final"], context["report"]
    if final["bucket"] != storage.bucket or pointer["bucket"] != storage.bucket:
        raise ValueError("quality_storage_mismatch")
    image, _, image_version = storage.get(
        final["object_key"], version=final["storage_version_id"], limit=100 * 1024 * 1024
    )
    raw, _, report_version = storage.get(
        pointer["object_key"], version=pointer["storage_version_id"], limit=8 * 1024 * 1024
    )
    if (
        image_version != final["storage_version_id"]
        or report_version != pointer["storage_version_id"]
        or hashlib.sha256(image).hexdigest() != final["sha256"]
        or len(image) != final["byte_size"]
        or hashlib.sha256(raw).hexdigest() != pointer["sha256"]
    ):
        raise ValueError("quality_source_digest_mismatch")
    with Image.open(io.BytesIO(image)) as picture:
        if (
            picture.format != "PNG"
            or picture.mode != "RGB"
            or getattr(picture, "n_frames", 1) != 1
            or picture.size != (final["width"], final["height"])
            or max(picture.size) > MAX_OUTPUT_EDGE
            or picture.width * picture.height > MAX_CANONICAL_PIXELS
        ):
            raise ValueError("quality_final_png_required")
        picture.verify()
    report = strict_json(raw)
    if (
        type(report) is not dict
        or report.get("final_sha256") != final["sha256"]
        or report.get("facts") != pointer["facts"]
        or report.get("per_platform") != pointer["per_platform"]
    ):
        raise ValueError("quality_report_binding_mismatch")
    profile = report.get("provider_profile")
    if profile == "rule_only_recheck":
        profile = report.get("source_provider_profile")
    if type(profile) is not str or not re.fullmatch(r"[a-z0-9_]{1,64}", profile):
        raise ValueError("quality_source_profile_required")
    offline = profile in ("offline_fake", "offline_protocol_mock")
    if (dataset_kind == "synthetic_fixture") != offline:
        raise ValueError("quality_dataset_source_mismatch")
    entries = report["per_platform"]
    if type(entries) is not list or not 1 <= len(entries) <= 10:
        raise ValueError("quality_report_scope_mismatch")
    samples, seen = [], set()
    for entry in entries:
        if type(entry) is not dict or entry.get("status") not in PREDICTIONS:
            raise ValueError("quality_report_scope_mismatch")
        candidate = {
            "sample_id": uuid4().hex,
            "image_file": "final.png",
            "image_sha256": final["sha256"],
            "report_file": "report.json",
            "report_sha256": pointer["sha256"],
            **{
                name: entry.get(name)
                for name in ("platform", "marketplace", "category", "image_type", "rule_version")
            },
            "human_label": "compliant",
            "annotator_fingerprint": "0" * 64,
            "features": dict.fromkeys(FEATURES, False),
        }
        # Validate scope using the evaluator's schema; placeholder labels are never exported.
        validated = QualitySample.model_validate(candidate, strict=True).model_dump(mode="json")
        scope = tuple(
            validated[key]
            for key in ("platform", "marketplace", "category", "image_type", "rule_version")
        )
        if scope in seen:
            raise ValueError("quality_report_scope_mismatch")
        seen.add(scope)
        validated.update(
            human_label=None, annotator_fingerprint=None, features=dict.fromkeys(FEATURES)
        )
        samples.append(validated)
    return image, raw, samples, profile


def export_quality_pack(dsn, storage, user, task, check, output, private_root, dataset_kind):
    target = private_destination(output, private_root)
    # The PG transaction ends before storage IO or local writes.
    context = quality_export_context(dsn, user, task, check)
    image, report, samples, profile = prepare_sample(context, storage, dataset_kind)
    manifest = {
        "schema_version": 1,
        "dataset_kind": dataset_kind,
        "human_reviewed_attested": False,
        "usage_rights_attested": False,
        "samples": samples,
    }
    provenance = {
        "schema_version": 1,
        "state": "AWAITING_HUMAN_LABELS",
        "user_id": str(user),
        "task_id": str(task),
        "check_id": str(check),
        "source_provider_profile": profile,
        "final_sha256": samples[0]["image_sha256"],
        "report_sha256": samples[0]["report_sha256"],
        "provider_verified": False,
        "paid_calls": 0,
    }
    target.mkdir(mode=0o700)

    def write(name, data):
        fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)

    write("final.png", image)
    write("report.json", report)
    write("annotate.html", annotation_document(manifest).encode())
    write("provenance.json", (json.dumps(provenance, indent=2) + "\n").encode())
    write(
        "README.txt",
        (
            "Private annotation pack. Do not publish these files.\n"
            "Human reviewers must label each platform/site/category/image type/rule version independently.\n"
            "Fill human_label, annotator_fingerprint (SHA256), and every feature in manifest.json.\n"
            "Do not copy automatic predictions as human truth; keep final.png/report.json unchanged.\n"
            "Set both attestations only after reviewing the samples and their usage rights.\n"
            "Then run scripts/evaluate_quality.py against this manifest.\n"
            "Blank templates cannot be evaluated. Counts never authorize release.\n"
        ).encode(),
    )
    # Written last: a failed export leaves no apparently complete evaluation manifest.
    write("manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    return {
        "result": "PRIVATE_QUALITY_PACK_EXPORTED",
        "samples": len(samples),
        "human_labels_complete": False,
        "provider_verified": False,
        "paid_calls": 0,
    }
