"""Private archived supplier pricing review and exact estimates; never execute a model."""

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
from visual_platform.domain.provider_pricing import SupplierMeteringContext, SupplierPriceVersion
from visual_platform.domain.supplier_billing import load_bill_review, review_supplier_bill
from visual_platform.domain.visual_price_review import VisualPriceReviewScope
from visual_platform.providers.qwen_image import strict_document
from visual_platform.services.provider_pricing import (
    archive_supplier_bill,
    estimate_reviewed_price,
    estimate_reviewed_visual_archive,
    publish_supplier_price,
    supplier_bill_review,
    supplier_billing_context,
    supplier_price_context,
    supplier_task_costs,
)
from visual_platform.storage.s3 import S3Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    publish = modes.add_parser("publish")
    publish.add_argument("--document", type=Path, required=True)
    publish.add_argument("--evidence-file", type=Path, required=True)
    publish.add_argument("--attest-reviewed", action="store_true", required=True)
    publish.add_argument("--operation-id", type=UUID, required=True)
    publish.add_argument("--reason", required=True)
    inspect = modes.add_parser("inspect")
    inspect.add_argument("--price", type=UUID, required=True)
    estimate = modes.add_parser("estimate")
    estimate.add_argument("--price", type=UUID, required=True)
    estimate.add_argument("--metering-context", type=Path, required=True)
    visual = modes.add_parser("estimate-vision-archive")
    visual.add_argument("--price", type=UUID, required=True)
    visual.add_argument("--archive", type=Path, required=True)
    visual.add_argument("--scope", type=Path, required=True)
    task = modes.add_parser("task-costs")
    task.add_argument("--task", type=UUID, required=True)
    billing = modes.add_parser("billing-context")
    billing.add_argument("--task", type=UUID, required=True)
    billing.add_argument("--attempt", type=UUID, required=True)
    bill = modes.add_parser("review-bill")
    bill.add_argument("--document", type=Path, required=True)
    bill.add_argument("--evidence-file", type=Path, required=True)
    bill.add_argument("--attest-reviewed", action="store_true", required=True)
    archive = modes.add_parser("archive-bill")
    archive.add_argument("--document", type=Path, required=True)
    archive.add_argument("--evidence-file", type=Path, required=True)
    archive.add_argument("--attest-reviewed", action="store_true", required=True)
    archive.add_argument("--operation-id", type=UUID, required=True)
    archive.add_argument("--reason", required=True)
    bill_inspect = modes.add_parser("bill-inspect")
    bill_inspect.add_argument("--review", type=UUID, required=True)
    args = parser.parse_args()
    storage = None
    dsn = os.environ.get("ADMIN_DATABASE_URL")
    if not dsn:
        print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
        return 3
    try:
        if args.mode in ("publish", "archive-bill"):
            if (
                args.document.stat().st_size > (16384 if args.mode == "publish" else 65536)
                or not 0 < args.evidence_file.stat().st_size <= 20 * 1024 * 1024
            ):
                raise ValueError("bounded pricing evidence required")
            if args.mode == "publish":
                document = SupplierPriceVersion.model_validate_json(
                    args.document.read_text(encoding="utf-8-sig")
                )
            else:
                if args.evidence_file.is_symlink() or not args.evidence_file.is_file():
                    raise ValueError("regular bill evidence file required")
                bill_review = load_bill_review(args.document)
            endpoint = urlsplit(os.environ["S3_ENDPOINT"])
            if (
                endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or not endpoint.hostname
                or endpoint.scheme not in ("http", "https")
                or (
                    endpoint.scheme == "http"
                    and endpoint.hostname not in ("localhost", "127.0.0.1")
                )
            ):
                raise ValueError("explicit secure storage endpoint required")
            storage = S3Storage(
                SimpleNamespace(
                    s3_endpoint=os.environ["S3_ENDPOINT"],
                    s3_access_key=os.environ["S3_ACCESS_KEY"],
                    s3_secret_key=os.environ["S3_SECRET_KEY"],
                    s3_bucket=os.environ["S3_BUCKET"],
                    s3_region=os.environ.get("S3_REGION", "us-east-1"),
                )
            )
            if args.mode == "publish":
                identity = publish_supplier_price(
                    dsn,
                    storage,
                    document,
                    args.evidence_file.read_bytes(),
                    args.operation_id,
                    args.reason,
                    evidence_reviewed=args.attest_reviewed,
                )
                output = supplier_price_context(dsn, UUID(identity))
            else:
                identity = archive_supplier_bill(
                    dsn,
                    storage,
                    bill_review,
                    args.evidence_file.read_bytes(),
                    args.operation_id,
                    args.reason,
                    evidence_reviewed=args.attest_reviewed,
                )
                output = supplier_bill_review(dsn, UUID(identity))
        elif args.mode == "inspect":
            output = supplier_price_context(dsn, args.price)
        elif args.mode == "bill-inspect":
            output = supplier_bill_review(dsn, args.review)
        elif args.mode == "billing-context":
            output = supplier_billing_context(dsn, args.task, args.attempt)
        elif args.mode == "review-bill":
            review = load_bill_review(args.document)
            if (
                args.evidence_file.is_symlink()
                or not args.evidence_file.is_file()
                or not 0 < args.evidence_file.stat().st_size <= 20 * 1024 * 1024
            ):
                raise ValueError("bounded regular bill evidence required")
            context = supplier_billing_context(dsn, review.task_id, review.attempt_id)
            output = review_supplier_bill(
                review,
                context,
                args.evidence_file.read_bytes(),
                evidence_reviewed=args.attest_reviewed,
            )
        elif args.mode == "task-costs":
            output = supplier_task_costs(dsn, args.task)
        elif args.mode == "estimate-vision-archive":
            documents = []
            for path, limit in ((args.archive, 4 * 1024 * 1024), (args.scope, 16384)):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
                    raise ValueError("bounded_regular_visual_review_file_required")
                with path.open("rb") as stream:
                    raw = stream.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError("visual_review_file_grew_beyond_limit")
                documents.append(strict_document(raw))
            scope = VisualPriceReviewScope.model_validate_json(json.dumps(documents[1]))
            output = estimate_reviewed_visual_archive(dsn, args.price, documents[0], scope)
        else:
            if args.metering_context.stat().st_size > 65536:
                raise ValueError("bounded usage context required")
            context = SupplierMeteringContext.model_validate_json(
                args.metering_context.read_text(encoding="utf-8-sig")
            )
            values = context.model_dump()
            quantities = values.pop("quantities")
            output = estimate_reviewed_price(dsn, args.price, quantities, **values)
        print(
            json.dumps(
                {"result": "SUPPLIER_PRICE_REVIEW", "paid_calls": 0, "review": output},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps({"result": "REJECTED", "paid_calls": 0, "error_type": type(error).__name__})
        )
        return 1
    finally:
        if storage is not None:
            storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
