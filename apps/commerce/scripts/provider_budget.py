"""Explicit offline protocol budget registration/inspection; zero provider calls."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.providers.authorization import load_authorization, verify_sample
from visual_platform.services.provider_budget import (
    provider_budget_snapshot,
    provider_quote_review,
    qwen_generation_projection,
    register_provider_budget,
    review_blocked_provider_budget,
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    register = modes.add_parser("register")
    register.add_argument("--config", type=Path, required=True)
    register.add_argument("--quote", type=UUID, required=True)
    register.add_argument("--reviewed-sha256", required=True)
    register.add_argument("--operation-id", type=UUID, required=True)
    register.add_argument("--reason", required=True)
    inspect = modes.add_parser("inspect")
    inspect.add_argument("--authorization", type=UUID, required=True)
    blocked = modes.add_parser("review-blocked")
    blocked.add_argument("--authorization", type=UUID, required=True)
    blocked.add_argument("--receipt", type=UUID, required=True)
    blocked.add_argument("--reviewed-sha256", required=True)
    blocked.add_argument("--operation-id", type=UUID, required=True)
    blocked.add_argument("--reason", required=True)
    blocked.add_argument("--evidence-digest", required=True)
    review = modes.add_parser("review-quote")
    review.add_argument("--user", type=UUID, required=True)
    review.add_argument("--quote", type=UUID, required=True)
    projection = modes.add_parser("project-generation")
    projection.add_argument("--user", type=UUID, required=True)
    projection.add_argument("--quote", type=UUID, required=True)
    args = parser.parse_args()
    try:
        dsn = os.environ.get("ADMIN_DATABASE_URL")
        if not dsn:
            print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
            return 3
        if args.mode == "review-quote":
            data = provider_quote_review(dsn, args.user, args.quote)
            print(json.dumps({"result": "OFFLINE_QUOTE_REVIEW", "paid_calls": 0, "quote": data}))
            return 0
        if args.mode == "project-generation":
            data = qwen_generation_projection(dsn, args.user, args.quote)
            print(
                json.dumps(
                    {"result": "REQUEST_PROJECTION_REVIEW", "paid_calls": 0, "projection": data}
                )
            )
            return 0
        if args.mode == "register":
            scope, digest = load_authorization(args.config)
            if digest != args.reviewed_sha256:
                raise ValueError("scope not reviewed")
            await verify_sample(scope, args.config)
            identity = register_provider_budget(
                dsn, args.quote, scope, digest, args.operation_id, args.reason
            )
        elif args.mode == "review-blocked":
            identity = review_blocked_provider_budget(
                dsn,
                args.authorization,
                args.receipt,
                args.reviewed_sha256,
                args.operation_id,
                args.reason,
                args.evidence_digest,
            )
        else:
            identity = str(args.authorization)
        report = provider_budget_snapshot(dsn, UUID(identity))
    except Exception as error:
        print(
            json.dumps({"result": "REJECTED", "paid_calls": 0, "error_type": type(error).__name__})
        )
        return 1
    print(json.dumps({"result": "OFFLINE_BUDGET_RECORDED", "paid_calls": 0, "budget": report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
