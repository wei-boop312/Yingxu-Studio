"""Restricted operations; no provider or wallet mutations in these workflows."""

import hashlib
import json
from io import BytesIO
from uuid import uuid5

import psycopg
from PIL import Image
from psycopg.types.json import Jsonb

from visual_platform.db.result import required_row
from visual_platform.domain.compliance import evaluate_rules
from visual_platform.domain.facts import Facts
from visual_platform.domain.rules import RuleDocument


def assert_operator(c):
    row = c.execute(
        "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,pg_has_role(current_user,'ops_admin','MEMBER'),EXISTS(SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname IN ('app','auth','ops') AND t.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if not row or any(row[:4]) or not row[4] or row[5]:
        raise ValueError("a non-owner ops_admin LOGIN is required")


def publish_rule(dsn, storage, document, evidence, operation, reason, *, evidence_reviewed=False):
    document = RuleDocument.model_validate(document).model_dump(mode="json")
    if document["evidence_status"] == "verified" and not evidence_reviewed:
        raise ValueError("explicit operator evidence review attestation required")
    with psycopg.connect(dsn) as c:
        assert_operator(c)
    bucket = key = version = None
    if evidence is not None:
        if not 0 < len(evidence) <= 20 * 1024 * 1024:
            raise ValueError("evidence archive must be 1..20MiB")
        if hashlib.sha256(evidence).hexdigest() != document["evidence_sha256"]:
            raise ValueError("evidence archive hash mismatch")
        bucket = storage.bucket
        key = "private/rule-evidence/" + str(operation)
        version = storage.put_immutable(key, evidence, "application/octet-stream")
    elif document["evidence_status"] == "verified":
        raise ValueError("verified rules require the archived evidence file")
    with psycopg.connect(dsn) as c:
        assert_operator(c)
        identity = required_row(
            c.execute(
                "SELECT ops.publish_rule(%s,%s,%s,%s,%s,%s)",
                (operation, Jsonb(document), bucket, key, version, reason),
            )
        )[0]
    return {
        "rule_id": str(identity),
        "version": document["version"],
        "evidence_status": document["evidence_status"],
    }


def recheck_report(dsn, storage, task, source_check, rule_ids, operation, reason):
    with psycopg.connect(dsn) as c:
        assert_operator(c)
        existing = required_row(
            c.execute(
                "SELECT ops.recheck_operation(%s,%s,%s,%s,%s)",
                (task, source_check, rule_ids, operation, reason),
            )
        )[0]
        ctx = (
            None
            if existing
            else required_row(
                c.execute("SELECT ops.recheck_context(%s,%s,%s)", (task, source_check, rule_ids))
            )[0]
        )
    if existing:
        if existing["report_bucket"] != storage.bucket:
            raise ValueError("storage bucket mismatch")
        raw, _, _ = storage.get(
            existing["report_object_key"],
            version=existing["report_storage_version_id"],
            limit=8 * 1024 * 1024,
        )
        if hashlib.sha256(raw).hexdigest() != existing["report_sha256"]:
            raise ValueError("existing recheck report hash mismatch")
        return {
            "check_id": existing["check_id"],
            "auto_overall": existing["auto_overall"],
            "provider_submissions": 0,
            "credits_charged": 0,
        }
    if ctx is None:
        raise ValueError("recheck_context_unavailable")
    source = ctx["source_check"]
    final = ctx["final"]
    if source["report_bucket"] != storage.bucket or final["bucket"] != storage.bucket:
        raise ValueError("storage bucket mismatch")
    old, _, _ = storage.get(
        source["report_object_key"], version=source["report_storage_version_id"]
    )
    if hashlib.sha256(old).hexdigest() != source["report_sha256"]:
        raise ValueError("source report hash mismatch")
    original = json.loads(old)
    if original["final_sha256"] != final["sha256"] or original["facts"] != source["facts"]:
        raise ValueError("source facts mismatch")
    image, _, _ = storage.get(
        final["object_key"], version=final["storage_version_id"], limit=80 * 1024 * 1024
    )
    if hashlib.sha256(image).hexdigest() != final["sha256"] or len(image) != final["byte_size"]:
        raise ValueError("final image hash mismatch")
    with Image.open(BytesIO(image)) as png:
        if png.format != "PNG" or png.size != (final["width"], final["height"]):
            raise ValueError("final image metadata mismatch")
        png.verify()
    facts = Facts.model_validate(source["facts"]).model_dump(mode="json")
    result = evaluate_rules(
        final["sha256"], ctx["rules"], facts, final, sampling_selected=source["sampling_selected"]
    )
    for platform in result["per_platform"]:
        platform["checks"].extend(
            [
                {"id": "local_decode", "status": "passed"},
                {
                    "id": "pixel_protection",
                    "status": "passed"
                    if original["pixel_protection"]["pixel_exact_passed"]
                    else "not_applicable",
                    "evidence": original["pixel_protection"],
                },
            ]
        )
    report = {
        "schema_version": 1,
        "final_asset_version_id": final["id"],
        "detection_degraded": original.get("detection_degraded", False),
        "final_sha256": final["sha256"],
        "facts": facts,
        "per_platform": result["per_platform"],
        "auto_overall": result["auto_overall"],
        "pixel_protection": original["pixel_protection"],
        "sampling_selected": source["sampling_selected"],
        "rule_snapshots": ctx["rules"],
        "recomputed_from_check_id": str(source_check),
        "operation_id": str(operation),
        "provider_profile": "rule_only_recheck",
        "source_provider_profile": original.get("source_provider_profile")
        or (
            original.get("provider_profile")
            if original.get("provider_profile") != "rule_only_recheck"
            else None
        ),
    }
    data = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    identity = uuid5(operation, "rule-only-recheck")
    key = "private/" + ctx["user_id"] + "/" + str(identity) + ".json"
    version = storage.put_immutable(key, data, "application/json")
    with psycopg.connect(dsn) as c:
        assert_operator(c)
        c.execute(
            "SELECT ops.record_recheck(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                task,
                source_check,
                rule_ids,
                operation,
                identity,
                Jsonb(result["per_platform"]),
                result["auto_overall"],
                hashlib.sha256(data).hexdigest(),
                storage.bucket,
                key,
                version,
                reason,
            ),
        )
    return {
        "check_id": str(identity),
        "auto_overall": result["auto_overall"],
        "provider_submissions": 0,
        "credits_charged": 0,
    }
