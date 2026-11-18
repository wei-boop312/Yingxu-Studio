"""Explicit Ops-only temporary upload cleanup. Defaults to a read-only plan."""

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.upload_cleanup import list_expired_uploads, reclaim_upload
from visual_platform.storage.s3 import S3Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=UUID, required=True)
    parser.add_argument("--intent", type=UUID)
    parser.add_argument("--operation-id", type=UUID)
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and (args.intent is None or args.operation_id is None or not args.reason):
        parser.error("--apply requires --intent, --operation-id and --reason")
    try:
        dsn = os.environ["OPS_ADMIN_DATABASE_URL"]
        if not args.apply:
            print(
                json.dumps(
                    {
                        "result": "UPLOAD_TEMP_CLEANUP_PLAN",
                        "items": list_expired_uploads(dsn, args.user),
                        "paid_calls": 0,
                    }
                )
            )
            return 0
        settings = SimpleNamespace(
            s3_endpoint=os.environ["S3_ENDPOINT"],
            s3_access_key=os.environ["S3_ACCESS_KEY"],
            s3_secret_key=os.environ["S3_SECRET_KEY"],
            s3_bucket=os.environ["S3_BUCKET"],
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
        )
        storage = S3Storage(settings)
        try:
            result = reclaim_upload(
                dsn, storage, args.user, args.intent, args.operation_id, args.reason
            )
        finally:
            storage.close()
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "UPLOAD_TEMP_CLEANUP_BLOCKED",
                    "error_type": type(error).__name__,
                    "paid_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
