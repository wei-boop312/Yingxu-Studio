"""Evaluate existing private final PNG/report pairs against human labels, offline."""

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.quality_evaluation import evaluate_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = evaluate_dataset(args.manifest)
        result["execution"] = {
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "command": "python scripts/evaluate_quality.py --manifest <private> [--output <private>]",
            "python_version": platform.python_version(),
            "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "engine_sha256": hashlib.sha256(
                (ROOT / "src/visual_platform/services/quality_evaluation.py").read_bytes()
            ).hexdigest(),
            "input_paths_redacted": True,
            "exit_code": 0,
        }
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(raw)
        print(raw, end="")
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "QUALITY_INPUT_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
