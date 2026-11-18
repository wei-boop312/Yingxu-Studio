"""Run only the explicitly authorized one-round local experiment; default performs no IO."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.core.credentials import private_credential
from visual_platform.providers.bailian_connection import load_connection
from visual_platform.services.paid_bailian_smoke import (
    DetectionSupplementApproval,
    SingleRound,
    bounded_file,
    load_paid_scope,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--scope", type=Path)
    parser.add_argument("--connection", type=Path)
    parser.add_argument("--supplement-approval", type=Path)
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"result": "PAID_SMOKE_NOT_REQUESTED", "paid_posts_attempted": 0}))
        return 3
    if args.scope is None or args.connection is None:
        parser.error("--run requires a private --scope and --connection")
    try:
        scope = load_paid_scope(args.scope)
        expected = (
            ROOT / ".local" / "paid-authorizations" / str(scope.authorization_id) / "scope.json"
        )
        if args.scope.absolute() != expected:
            raise ValueError("bound_private_scope_path_required")
        connection = load_connection(args.connection)
        key = private_credential("DASHSCOPE_API_KEY")
        if not key:
            print(json.dumps({"result": "WAITING_PRIVATE_CREDENTIAL", "paid_posts_attempted": 0}))
            return 3
        runner = SingleRound(scope, connection, key, expected.parent, live=True, resume=args.resume)
        if args.supplement_approval is not None:
            if (
                args.supplement_approval.absolute()
                != expected.parent / "detection-supplement-approval.json"
            ):
                raise ValueError("bound_supplement_approval_path_required")
            approval = DetectionSupplementApproval.model_validate_json(
                bounded_file(args.supplement_approval, 16384)
            )
            report = runner.run_supplement(approval)
        else:
            report = runner.run()
        print(json.dumps(report))
        return 0 if report["result"] == "SINGLE_ROUND_COMPLETED" else 1
    except Exception as error:
        print(
            json.dumps({"result": "PAID_SMOKE_SETUP_REJECTED", "error_type": type(error).__name__})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
