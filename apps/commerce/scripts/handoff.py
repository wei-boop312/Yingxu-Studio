"""Create/verify a local offline handoff from current CI evidence; no publishing."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.handoff import create_handoff, verify_handoff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify_handoff(args.verify)
        else:
            from verify_delivery import verify_run, verify_static

            verify_static()
            verify_run(ROOT / "work/ci-public")
            result = create_handoff(ROOT)
    except Exception as error:
        print(
            json.dumps(
                {"result": "HANDOFF_REJECTED", "paid_calls": 0, "error_type": type(error).__name__}
            )
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
