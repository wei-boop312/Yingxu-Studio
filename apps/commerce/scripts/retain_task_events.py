"""Read-only event retention plan by default; explicit --apply prunes one bounded batch."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.event_retention import event_retention


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=UUID, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--operation-id", type=UUID)
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and (args.operation_id is None or not args.reason):
        parser.error("--apply requires --operation-id and --reason")
    if not args.apply and (args.operation_id is not None or args.reason is not None):
        parser.error("operation details require --apply")
    try:
        result = event_retention(
            os.environ["OPS_ADMIN_DATABASE_URL"],
            args.user,
            days=args.days,
            limit=args.limit,
            operation=args.operation_id,
            reason=args.reason,
        )
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "TASK_EVENT_RETENTION_BLOCKED",
                    "error_type": type(error).__name__,
                    "paid_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
