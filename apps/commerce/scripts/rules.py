"""Operations-only policy publishing and report recomputation CLI."""

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from visual_platform.services.rule_operations import publish_rule, recheck_report
from visual_platform.storage.s3 import S3Storage, StorageError

p = argparse.ArgumentParser(description=__doc__)
sub = p.add_subparsers(dest="command", required=True)
publish = sub.add_parser("publish")
publish.add_argument("--document", type=Path, required=True)
publish.add_argument("--evidence-file", type=Path)
publish.add_argument(
    "--attest-evidence-reviewed",
    action="store_true",
    help="Operator has reviewed the archived official evidence, scope and thresholds; required for verified rules",
)
recheck = sub.add_parser("recheck")
recheck.add_argument("--task", type=UUID, required=True)
recheck.add_argument("--source-check", type=UUID, required=True)
recheck.add_argument("--rule-id", type=UUID, action="append", required=True)
for command in (publish, recheck):
    command.add_argument("--operation-id", type=UUID, required=True)
    command.add_argument("--reason", required=True)
args = p.parse_args()
if not os.environ.get("ADMIN_DATABASE_URL"):
    p.error("ADMIN_DATABASE_URL must identify a dedicated ops_admin LOGIN")
for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET"):
    if not os.environ.get(name):
        p.error("explicit S3 configuration required")
settings = SimpleNamespace(
    s3_endpoint=os.environ["S3_ENDPOINT"],
    s3_access_key=os.environ["S3_ACCESS_KEY"],
    s3_secret_key=os.environ["S3_SECRET_KEY"],
    s3_bucket=os.environ["S3_BUCKET"],
    s3_region=os.environ.get("S3_REGION", "us-east-1"),
)
storage = None
try:
    endpoint = urlsplit(settings.s3_endpoint)
    if (
        endpoint.username
        or endpoint.password
        or not endpoint.hostname
        or endpoint.scheme not in ("https", "http")
        or (endpoint.scheme == "http" and endpoint.hostname not in ("127.0.0.1", "localhost"))
    ):
        raise ValueError("remote S3 requires HTTPS without embedded credentials")
    storage = S3Storage(settings)
    if args.command == "publish":
        if args.document.stat().st_size > 1024 * 1024:
            raise ValueError("rule document too large")
        if args.evidence_file and args.evidence_file.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("evidence archive too large")
        result = publish_rule(
            os.environ["ADMIN_DATABASE_URL"],
            storage,
            json.loads(args.document.read_text(encoding="utf-8-sig")),
            args.evidence_file.read_bytes() if args.evidence_file else None,
            args.operation_id,
            args.reason,
            evidence_reviewed=args.attest_evidence_reviewed,
        )
    else:
        result = recheck_report(
            os.environ["ADMIN_DATABASE_URL"],
            storage,
            args.task,
            args.source_check,
            args.rule_id,
            args.operation_id,
            args.reason,
        )
    print(json.dumps(result, ensure_ascii=False))
except (
    psycopg.Error,
    ValueError,
    KeyError,
    TypeError,
    OSError,
    StorageError,
    BotoCoreError,
    ClientError,
) as exc:
    # Validation errors can contain supplied evidence text; never echo raw exceptions.
    safe = str(exc).split("\n")[0]
    known = {
        "operation_conflict",
        "invalid_rule",
        "invalid_evidence",
        "rule_scope_mismatch",
        "task_not_found",
        "recheck_source_unavailable",
        "unverified_cannot_pass",
    }
    raise SystemExit(
        safe
        if safe in known
        else "Operation failed; verify role, rule schema, evidence hash and storage configuration."
    )
finally:
    if storage:
        storage.close()
