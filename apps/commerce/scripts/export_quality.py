"""Export one private PNG/report annotation template; never infer labels or call models."""

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.quality_export import export_quality_pack
from visual_platform.storage.s3 import S3Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("user", "task", "check"):
        parser.add_argument("--" + name, type=UUID, required=True)
    parser.add_argument(
        "--dataset-kind", choices=("synthetic_fixture", "real_product_set"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    storage = None
    try:
        dsn = os.environ.get("ADMIN_DATABASE_URL")
        if not dsn:
            print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
            return 3
        output = args.output.absolute()
        root = next((p for p in (ROOT / ".local", ROOT / "work") if output.is_relative_to(p)), None)
        if root is None:
            raise ValueError("private_quality_output_required")
        settings = SimpleNamespace(
            **{
                name.lower(): os.environ[name]
                for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
            },
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
        )
        storage = S3Storage(settings)
        result = export_quality_pack(
            dsn, storage, args.user, args.task, args.check, output, root, args.dataset_kind
        )
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "QUALITY_EXPORT_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_calls": 0,
                }
            )
        )
        return 1
    finally:
        if storage:
            storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
