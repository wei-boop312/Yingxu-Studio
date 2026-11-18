import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from visual_platform.db.result import required_row

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description="Restricted operations CLI; no paid provider submission")
sub = p.add_subparsers(dest="command", required=True)
review = sub.add_parser("review")
review.add_argument("--task", type=UUID, required=True)
review.add_argument("--check", type=UUID, required=True)
review.add_argument("--reviewer", type=UUID, required=True)
review.add_argument("--decision", choices=["approved", "rejected", "needs_review"], required=True)
review.add_argument("--reason", required=True)
review.add_argument("--evidence-json", type=Path, required=True)
review.add_argument("--operation-id", type=UUID, required=True)
reconcile = sub.add_parser("reconcile")
reconcile.add_argument("--task", type=UUID, required=True)
reconcile.add_argument("--action", choices=["resume", "fail"], required=True)
reconcile.add_argument("--receipt", type=UUID)
reconcile.add_argument("--reason", required=True)
reconcile.add_argument("--evidence-digest", required=True)
reconcile.add_argument("--operation-id", type=UUID, required=True)
understanding = sub.add_parser("close-understanding")
understanding.add_argument("--user", type=UUID, required=True)
understanding.add_argument("--understanding", type=UUID, required=True)
understanding.add_argument("--reason", required=True)
understanding.add_argument("--evidence-digest", required=True)
understanding.add_argument("--operation-id", type=UUID, required=True)
inspect = sub.add_parser("inspect")
inspect.add_argument("--task", type=UUID, required=True)
receipt = sub.add_parser("accept-receipt")
receipt.add_argument("--task", type=UUID, required=True)
receipt.add_argument("--attempt", type=UUID, required=True)
receipt.add_argument("--provider-id", required=True)
receipt.add_argument("--response-digest", required=True)
receipt.add_argument("--polling-url")
receipt.add_argument("--reason", required=True)
receipt.add_argument("--evidence-digest", required=True)
receipt.add_argument("--operation-id", type=UUID, required=True)
args = p.parse_args()
dsn = os.environ.get("ADMIN_DATABASE_URL")
if not dsn:
    p.error("ADMIN_DATABASE_URL required; use a dedicated ops_admin LOGIN")
try:
    with psycopg.connect(dsn) as c:
        role = c.execute(
            "SELECT rolsuper,rolbypassrls,pg_has_role(current_user,'ops_admin','MEMBER') FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not role or role[0] or role[1] or not role[2]:
            p.error("a non-owner ops_admin LOGIN is required")
        if args.command == "inspect":
            result = required_row(c.execute("SELECT ops.task_details(%s)", (args.task,)))[0]
        elif args.command == "review":
            refs = json.loads(args.evidence_json.read_text(encoding="utf-8"))
            if (
                not isinstance(refs, list)
                or len(refs) > 100
                or any(not isinstance(x, str) or len(x) > 2048 for x in refs)
            ):
                p.error("evidence JSON must be a bounded string array")
            identity = required_row(
                c.execute(
                    "SELECT app.review_check(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        args.task,
                        args.check,
                        args.operation_id,
                        args.reviewer,
                        args.decision,
                        args.reason,
                        Jsonb(refs),
                    ),
                )
            )[0]
            result = {"review_id": str(identity)}
        elif args.command == "close-understanding":
            c.execute(
                "SELECT ops.close_appearance_understanding(%s,%s,%s,%s,%s)",
                (
                    args.user,
                    args.understanding,
                    args.operation_id,
                    args.reason,
                    args.evidence_digest,
                ),
            )
            result = {
                "operation_id": str(args.operation_id),
                "action": "close_understanding_delivery",
                "supplier_charge": "unknown",
            }
        elif args.command == "reconcile":
            if args.action == "resume" and not args.receipt:
                p.error("resume requires an existing receipt")
            c.execute(
                "SELECT ops.reconcile_task(%s,%s,%s,%s,%s,%s)",
                (
                    args.task,
                    args.operation_id,
                    args.action,
                    args.receipt,
                    args.reason,
                    args.evidence_digest,
                ),
            )
            result = {"operation_id": str(args.operation_id), "action": args.action}
        else:
            identity = required_row(
                c.execute(
                    "SELECT ops.accept_receipt(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        args.task,
                        args.attempt,
                        args.provider_id,
                        args.response_digest,
                        args.polling_url,
                        args.operation_id,
                        args.reason,
                        args.evidence_digest,
                    ),
                )
            )[0]
            result = {"receipt_id": str(identity)}
    print(json.dumps(result, ensure_ascii=False, default=str))
except psycopg.Error as exc:
    # SQL details and connection strings are intentionally not echoed.
    safe = str(exc).split("\n")[0]
    known = {
        "task_not_found",
        "task_not_uncertain",
        "receipt_required",
        "receipt_conflict",
        "receipt_not_unresolved",
        "check_not_found",
        "invalid_review",
        "invalid_reconciliation",
        "operation_conflict",
        "evidence_required",
    }
    raise SystemExit(
        safe
        if safe in known
        else "Operation failed; inspect role permissions and supplied evidence."
    )
