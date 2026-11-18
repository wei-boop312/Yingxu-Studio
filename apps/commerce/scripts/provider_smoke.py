"""Review minimum paid-smoke scope. No live execution is enabled by this command."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.providers.authorization import (
    BailianSmokeAuthorization,
    DualBailianSmokeAuthorization,
    UnitSmokeAuthorization,
    load_authorization,
    verify_sample,
)
from visual_platform.providers.bailian_connection import load_connection
from visual_platform.providers.runtime import credential_status, load_runtime
from visual_platform.services.provider_preflight import (
    load_bound_review,
    review_bound_scope,
    verify_price_evidence,
)
from visual_platform.storage.s3 import S3Storage


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--bound-review", action="store_true")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--bailian-connection", type=Path)
    parser.add_argument("--quote", type=UUID)
    parser.add_argument("--check-credentials", action="store_true")
    parser.add_argument("--offline-fixtures", action="store_true")
    parser.add_argument("--check-price-evidence", action="store_true")
    args = parser.parse_args()
    bound_options = (
        args.runtime,
        args.bailian_connection,
        args.quote,
        args.check_credentials,
        args.offline_fixtures,
        args.check_price_evidence,
    )
    if (
        args.bound_review
        and (not args.preflight or any(value is None for value in bound_options[:3]))
    ) or (not args.bound_review and any(bound_options)):
        parser.error(
            "bound review requires --preflight, --runtime, --bailian-connection and --quote"
        )
    if not args.preflight:
        print(
            json.dumps(
                {
                    "result": "WAITING_EXTERNAL",
                    "paid_calls": 0,
                    "reason": "live execution disabled; use --preflight for offline review",
                }
            )
        )
        return 3
    try:
        scope, digest = load_authorization(args.config)
        sample = await verify_sample(scope, args.config)
        if args.bound_review:
            if isinstance(scope, DualBailianSmokeAuthorization):
                print(
                    json.dumps(
                        {
                            "result": "DUAL_BAILIAN_BOUND_REVIEW_NOT_IMPLEMENTED",
                            "reason": "dual_model_quote_and_atomic_budget_not_enabled",
                            "execution_enabled": False,
                            "paid_calls": 0,
                        }
                    )
                )
                return 3
            if not isinstance(scope, BailianSmokeAuthorization):
                raise ValueError("bailian_scope_required")
            dsn = os.environ.get("ADMIN_DATABASE_URL")
            if not dsn:
                print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
                return 3
            runtime = load_runtime(args.runtime)
            local_connection = load_connection(args.bailian_connection)
            bundle = load_bound_review(
                dsn, scope.user_id, args.quote, runtime.digest(), scope.authorization_id
            )
            report = review_bound_scope(
                scope,
                runtime,
                local_connection,
                bundle,
                credentials=credential_status(runtime) if args.check_credentials else None,
                allow_offline_fixture=args.offline_fixtures,
            )
            report["sample"] = sample
            report["price_evidence_check_performed"] = False
            if args.check_price_evidence and report["scope_checks_passed"]:
                storage = S3Storage(
                    SimpleNamespace(
                        s3_endpoint=os.environ["S3_ENDPOINT"],
                        s3_bucket=os.environ["S3_BUCKET"],
                        s3_access_key=os.environ["S3_ACCESS_KEY"],
                        s3_secret_key=os.environ["S3_SECRET_KEY"],
                        s3_region=os.environ.get("S3_REGION", "us-east-1"),
                    )
                )
                try:
                    evidence = verify_price_evidence(storage, bundle)
                finally:
                    storage.close()
                report["price_evidence_check_performed"] = True
                report["checks"]["price_evidence_readback"] = all(evidence.values())
                report["price_evidence_verified"] = evidence
                if not all(evidence.values()):
                    report["failures"].append("price_evidence_readback")
                    report["scope_checks_passed"] = False
                    report["result"] = "BOUND_SCOPE_REJECTED"
                else:
                    report["not_verified"].remove("current S3 price evidence availability")
            print(json.dumps(report))
            return 0 if report["scope_checks_passed"] else 1
    except Exception as error:
        # Do not echo Pydantic inputs, paths or config contents into shared diagnostics.
        print(
            json.dumps(
                {
                    "result": "PREFLIGHT_REJECTED",
                    "paid_calls": 0,
                    "error_type": type(error).__name__,
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "result": "DRAFT_SCOPE_REVIEW_READY"
                if isinstance(scope, DualBailianSmokeAuthorization)
                else "SCOPE_REVIEW_READY",
                "paid_calls": 0,
                "configuration_sha256": digest,
                "authorization_id": str(scope.authorization_id),
                "sample": sample,
                "models": (
                    {"generate": scope.generation_model, "detect": scope.detection_model}
                    if isinstance(scope, (BailianSmokeAuthorization, DualBailianSmokeAuthorization))
                    else {"generate": scope.bfl_model, "detect": scope.gemini_model}
                ),
                "call_limits": {
                    "generate": scope.max_generate_calls,
                    "detect": scope.max_detect_calls,
                },
                "cost_ceiling": (
                    {
                        "step_budgets": scope.step_budgets.model_dump(mode="json"),
                        "currency_totals": [
                            row.model_dump(mode="json") for row in scope.currency_totals
                        ],
                    }
                    if isinstance(
                        scope,
                        (
                            UnitSmokeAuthorization,
                            BailianSmokeAuthorization,
                            DualBailianSmokeAuthorization,
                        ),
                    )
                    else {
                        "currency": scope.currency,
                        "generate": scope.generate_ceiling,
                        "detect": scope.detect_ceiling,
                        "total": scope.total_ceiling,
                    }
                ),
                "expires_at": scope.expires_at.isoformat(),
                "budget_registration_implemented": not isinstance(
                    scope, DualBailianSmokeAuthorization
                ),
                "execution_enabled": False,
                "not_verified": [
                    "operator spending authorization",
                    "atomic monetary budget",
                    "live Worker routing",
                    "supplier pricing and actual billing",
                    "real archived image and final report",
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
