"""Offline human-labelled report evaluation; no inference or release decision."""

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from PIL import Image
from pydantic import Field, model_validator

from visual_platform.domain.facts import Facts
from visual_platform.domain.image_limits import MAX_CANONICAL_PIXELS, MAX_OUTPUT_EDGE
from visual_platform.domain.platforms import Platform
from visual_platform.domain.schemas import StrictModel
from visual_platform.providers.gemini_usage import unique_object

Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
ScopeText = Annotated[str, Field(strict=True, min_length=1, max_length=160)]
FEATURES = (
    "visible_text",
    "on_product_logo",
    "added_watermark",
    "dark_background",
    "category_exception",
)
PREDICTIONS = ("compliant", "non_compliant", "manual_review")
TRUTHS = ("compliant", "non_compliant")
VISUAL_FEATURES = ("visible_text", "on_product_logo", "added_watermark")


class SampleFeatures(StrictModel):
    visible_text: bool = Field(strict=True)
    on_product_logo: bool = Field(strict=True)
    added_watermark: bool = Field(strict=True)
    dark_background: bool = Field(strict=True)
    category_exception: bool = Field(strict=True)


class QualitySample(StrictModel):
    sample_id: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    image_file: ScopeText
    image_sha256: Digest
    report_file: ScopeText
    report_sha256: Digest
    platform: Platform
    marketplace: ScopeText
    category: ScopeText
    image_type: ScopeText
    rule_version: ScopeText
    human_label: Literal["compliant", "non_compliant"]
    annotator_fingerprint: Digest
    features: SampleFeatures


class QualityDataset(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    dataset_kind: Literal["synthetic_fixture", "real_product_set"]
    human_reviewed_attested: bool = Field(strict=True)
    usage_rights_attested: bool = Field(strict=True)
    samples: list[QualitySample] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_scope(self):
        if not self.human_reviewed_attested or not self.usage_rights_attested:
            raise ValueError("quality_dataset_attestations_required")
        ids, scopes = set(), set()
        image_labels: dict[str, tuple[bool, ...]] = {}
        for sample in self.samples:
            scope = (
                sample.image_sha256,
                sample.platform,
                sample.marketplace,
                sample.category,
                sample.image_type,
                sample.rule_version,
            )
            if sample.sample_id in ids or scope in scopes:
                raise ValueError("duplicate_quality_sample_scope")
            ids.add(sample.sample_id)
            scopes.add(scope)
            visual = tuple(
                getattr(sample.features, name) for name in FEATURES if name != "category_exception"
            )
            if sample.image_sha256 in image_labels and image_labels[sample.image_sha256] != visual:
                raise ValueError("inconsistent_human_visual_labels_for_same_image")
            image_labels[sample.image_sha256] = visual
        return self


def strict_json(raw):
    def invalid(value):
        raise ValueError("non_standard_quality_json")

    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid)


def private_read(root, name, limit):
    # All members must remain beneath the manifest directory, including parents.
    if not isinstance(name, str) or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError("unsafe_quality_member")
    parts = name.split("/")
    if any(p in ("", ".", "..") or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError("unsafe_quality_member")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink_quality_member")
    if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("missing_or_oversized_quality_member")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError("empty_or_oversized_quality_member")
    return raw


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def summarize(rows):
    matrix = {truth: {prediction: 0 for prediction in PREDICTIONS} for truth in TRUTHS}
    for truth, prediction in rows:
        matrix[truth][prediction] += 1
    total = len(rows)
    positive = sum(matrix["compliant"].values())
    negative = sum(matrix["non_compliant"].values())
    manual = sum(matrix[t]["manual_review"] for t in TRUTHS)
    accepted_wrong = matrix["non_compliant"]["compliant"]
    rejected_wrong = matrix["compliant"]["non_compliant"]
    return {
        "count": total,
        "human_compliant_count": positive,
        "human_non_compliant_count": negative,
        "confusion_matrix": matrix,
        "false_accept_count": accepted_wrong,
        "false_accept_rate": ratio(accepted_wrong, negative),
        "false_reject_count": rejected_wrong,
        "false_reject_rate": ratio(rejected_wrong, positive),
        "manual_review_count": manual,
        "manual_review_rate": ratio(manual, total),
        "automatic_coverage": ratio(total - manual, total),
        "automatic_accuracy": ratio(
            total - manual - accepted_wrong - rejected_wrong, total - manual
        ),
    }


def visual_predictions(raw, dimensions):
    unknown = dict.fromkeys(VISUAL_FEATURES, "uncertain")
    try:
        facts = Facts.model_validate(raw)
    except ValueError:
        return unknown, False
    if (facts.image_width, facts.image_height) != dimensions:
        return unknown, False
    complete = facts.analysis_complete and not facts.uncertainties

    def prediction(items, origin=None):
        if any(origin is None or mark.origin == origin for mark in items):
            return "present"
        if not complete or any(mark.origin == "unknown" for mark in items):
            return "uncertain"
        return "absent"

    return {
        "visible_text": prediction(facts.text_items),
        "on_product_logo": prediction(facts.logo_items, "on_product"),
        "added_watermark": prediction(facts.watermark_items, "added_overlay"),
    }, True


def summarize_visual(rows):
    matrix = {
        truth: {outcome: 0 for outcome in ("present", "absent", "uncertain")}
        for truth in ("present", "absent")
    }
    for truth, prediction in rows:
        matrix["present" if truth else "absent"][prediction] += 1
    positives = sum(matrix["present"].values())
    negatives = sum(matrix["absent"].values())
    false_negative = matrix["present"]["absent"]
    false_positive = matrix["absent"]["present"]
    uncertain = matrix["present"]["uncertain"] + matrix["absent"]["uncertain"]
    total = len(rows)
    return {
        "count": total,
        "human_present_count": positives,
        "human_absent_count": negatives,
        "confusion_matrix": matrix,
        "false_positive_count": false_positive,
        "false_positive_rate": ratio(false_positive, negatives),
        "false_negative_count": false_negative,
        "false_negative_rate": ratio(false_negative, positives),
        "uncertain_count": uncertain,
        "uncertain_rate": ratio(uncertain, total),
        "assessable_coverage": ratio(total - uncertain, total),
        "assessable_accuracy": ratio(
            total - uncertain - false_negative - false_positive, total - uncertain
        ),
    }


def evaluate_dataset(manifest: Path):
    if (
        manifest.is_symlink()
        or not manifest.is_file()
        or manifest.stat().st_size > 16 * 1024 * 1024
    ):
        raise ValueError("invalid_quality_manifest")
    root = manifest.resolve().parent
    raw = private_read(root, manifest.name, 16 * 1024 * 1024)
    data = QualityDataset.model_validate(strict_json(raw), strict=True)
    rows = []
    feature_rows: dict[str, list[tuple[str, str]]] = {name: [] for name in FEATURES}
    platform_rows: dict[str, list[tuple[str, str]]] = {}
    visual_rows: dict[str, list[tuple[bool, str]]] = {name: [] for name in VISUAL_FEATURES}
    unavailable_facts = 0
    visual_seen: dict[str, tuple[dict[str, str], bool]] = {}
    profiles = set()
    report_profiles = set()
    lineage_complete = True
    for sample in data.samples:
        image = private_read(root, sample.image_file, 100 * 1024 * 1024)
        report_raw = private_read(root, sample.report_file, 8 * 1024 * 1024)
        if (
            hashlib.sha256(image).hexdigest() != sample.image_sha256
            or hashlib.sha256(report_raw).hexdigest() != sample.report_sha256
        ):
            raise ValueError("quality_input_digest_mismatch")
        with Image.open(io.BytesIO(image)) as picture:
            if (
                picture.format != "PNG"
                or getattr(picture, "n_frames", 1) != 1
                or max(picture.size) > MAX_OUTPUT_EDGE
                or picture.width * picture.height > MAX_CANONICAL_PIXELS
            ):
                raise ValueError("quality_final_png_required")
            dimensions = picture.size
            picture.verify()
        report = strict_json(report_raw)
        if (
            type(report) is not dict
            or report.get("final_sha256") != sample.image_sha256
            or type(report.get("per_platform")) is not list
        ):
            raise ValueError("quality_report_image_mismatch")
        matches = [
            entry
            for entry in report["per_platform"]
            if type(entry) is dict
            and (
                entry.get("platform"),
                entry.get("marketplace"),
                entry.get("category"),
                entry.get("image_type"),
                entry.get("rule_version"),
            )
            == (
                sample.platform,
                sample.marketplace,
                sample.category,
                sample.image_type,
                sample.rule_version,
            )
        ]
        if len(matches) != 1 or matches[0].get("status") not in PREDICTIONS:
            raise ValueError("quality_report_scope_mismatch")
        profile = report.get("provider_profile")
        if type(profile) is not str or not re.fullmatch(r"[a-z0-9_]{1,64}", profile):
            raise ValueError("quality_report_profile_required")
        report_profiles.add(profile)
        if profile == "rule_only_recheck":
            source_profile = report.get("source_provider_profile")
            if (
                type(source_profile) is str
                and source_profile != "rule_only_recheck"
                and re.fullmatch(r"[a-z0-9_]{1,64}", source_profile)
            ):
                profile = source_profile
            else:
                profile = "unrecorded"
                lineage_complete = False
        if data.dataset_kind == "synthetic_fixture" and profile not in (
            "offline_fake",
            "offline_protocol_mock",
        ):
            raise ValueError("synthetic_quality_requires_offline_report")
        profiles.add(profile)
        predictions, available = visual_predictions(report.get("facts"), dimensions)
        observation = (predictions, available)
        if sample.image_sha256 in visual_seen:
            if visual_seen[sample.image_sha256] != observation:
                raise ValueError("conflicting_visual_observations_for_same_image")
        else:
            visual_seen[sample.image_sha256] = observation
            unavailable_facts += not available
            for name in VISUAL_FEATURES:
                visual_rows[name].append((getattr(sample.features, name), predictions[name]))
        row = (sample.human_label, matches[0]["status"])
        rows.append(row)
        platform_rows.setdefault(sample.platform, []).append(row)
        for name in FEATURES:
            if getattr(sample.features, name):
                feature_rows[name].append(row)
    return {
        "schema_version": 1,
        "result": "QUALITY_COUNTS_ONLY",
        "dataset_kind": data.dataset_kind,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "human_labels_independently_verified": False,
        "category_scope_independently_verified": False,
        "provider_profiles": sorted(profiles),
        "report_profiles": sorted(report_profiles),
        "source_lineage_complete": lineage_complete,
        "provider_verified": False,
        "release_authorized": False,
        "paid_calls": 0,
        "overall": summarize(rows),
        "by_platform": {name: summarize(values) for name, values in sorted(platform_rows.items())},
        "by_feature_present": {name: summarize(values) for name, values in feature_rows.items()},
        "visual_presence": {name: summarize_visual(values) for name, values in visual_rows.items()},
        "unavailable_or_mismatched_facts_count": unavailable_facts,
        "unique_visual_image_count": len(visual_seen),
        "visual_presence_scope": "presence only; not OCR transcripts, bounding-box accuracy, dark-background recognition or policy exceptions",
        "missing_feature_coverage": [name for name, values in feature_rows.items() if not values],
        "sampling_representativeness_verified": False,
        "model_confidence_used_as_accuracy": False,
    }
