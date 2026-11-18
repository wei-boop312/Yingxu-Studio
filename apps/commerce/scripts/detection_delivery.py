"""Restricted operator review for local delivery after unusable final detection."""

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.detection_delivery import (
    approve_detection_delivery,
    detection_delivery_context,
    export_detection_review,
)
from visual_platform.storage.s3 import S3Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    inspect = modes.add_parser("inspect")
    inspect.add_argument("--task", type=UUID, required=True)
    approve = modes.add_parser("approve")
    approve.add_argument("--task", type=UUID, required=True)
    approve.add_argument("--attempt", type=UUID, required=True)
    approve.add_argument("--final-sha256", required=True)
    approve.add_argument("--operation-id", type=UUID, required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--evidence-digest", required=True)
    export = modes.add_parser("export")
    export.add_argument("--task", type=UUID, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dsn = os.environ.get("ADMIN_DATABASE_URL")
    if not dsn:
        print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
        return 3
    storage = None
    try:
        if args.mode == "export":
            endpoint = urlsplit(os.environ["S3_ENDPOINT"])
            if (
                endpoint.username
                or endpoint.password
                or not endpoint.hostname
                or endpoint.scheme not in ("http", "https")
                or (
                    endpoint.scheme == "http"
                    and endpoint.hostname not in ("localhost", "127.0.0.1")
                )
            ):
                raise ValueError("invalid_storage_configuration")
            settings = SimpleNamespace(
                s3_endpoint=os.environ["S3_ENDPOINT"],
                s3_access_key=os.environ["S3_ACCESS_KEY"],
                s3_secret_key=os.environ["S3_SECRET_KEY"],
                s3_bucket=os.environ["S3_BUCKET"],
                s3_region=os.environ.get("S3_REGION", "us-east-1"),
            )
            output = args.output.absolute()
            roots = [ROOT / ".local", ROOT / "work"]
            private = next(
                (
                    root
                    for root in roots
                    if root.exists() and output.parent.resolve().is_relative_to(root.resolve())
                ),
                None,
            )
            if private is None:
                raise ValueError("private_review_output_required")
            storage = S3Storage(settings)
            exported = export_detection_review(dsn, storage, args.task, output, private)
            print(json.dumps({"result": "PRIVATE_REVIEW_EXPORTED", **exported}))
            return 0
        decision = None
        if args.mode == "approve":
            decision = approve_detection_delivery(
                dsn,
                args.task,
                args.attempt,
                args.final_sha256,
                args.operation_id,
                args.reason,
                args.evidence_digest,
            )
        report = detection_delivery_context(dsn, args.task)
    except Exception as error:
        safe = str(error).split("\n", 1)[0]
        known = {
            "invalid_detection_delivery_review",
            "operation_conflict",
            "task_not_found",
            "detection_delivery_requires_unresolved_final",
            "detection_delivery_already_reviewed",
            "unresolved_detection_required",
            "known_generation_required",
            "final_hash_mismatch",
            "manual_detection_scope_mismatch",
            "invalid_manual_detection_report",
        }
        print(
            json.dumps(
                {
                    "result": "REJECTED",
                    "paid_calls": 0,
                    "error_type": type(error).__name__,
                    "code": safe if safe in known else "operation_rejected",
                }
            )
        )
        return 1
    finally:
        if storage is not None:
            storage.close()
    print(
        json.dumps(
            {
                "result": "LOCAL_DELIVERY_APPROVED" if decision else "DETECTION_DELIVERY_REVIEW",
                "paid_calls": 0,
                "decision_id": decision,
                "context": report,
            },
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
