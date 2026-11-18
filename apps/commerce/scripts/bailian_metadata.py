"""Explicit read-only catalogue authentication probe; no generation, detection or account writes."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.core.credentials import private_credential
from visual_platform.providers.bailian_connection import load_connection
from visual_platform.providers.bailian_metadata import probe_metadata
from visual_platform.services.quality_export import private_destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--kind", choices=("catalogue", "permissions"), default="catalogue")
    parser.add_argument("--model", choices=("qwen-image-3.0", "qwen3.8-flash"))
    args = parser.parse_args()
    if not args.probe:
        print(
            json.dumps(
                {
                    "result": "METADATA_PROBE_NOT_REQUESTED",
                    "paid_model_calls": 0,
                    "metadata_get_requests": 0,
                }
            )
        )
        return 3
    if args.config is None or args.output_dir is None:
        parser.error("--probe requires --config and a new private --output-dir")
    try:
        output = args.output_dir.absolute()
        root = next(
            (root for root in (ROOT / ".local", ROOT / "work") if output.is_relative_to(root)), None
        )
        if root is None:
            raise ValueError("private_metadata_output_required")
        destination = private_destination(output, root)
        configuration = load_connection(args.config)
        key = private_credential("DASHSCOPE_API_KEY")
        if not key:
            print(
                json.dumps(
                    {
                        "result": "WAITING_PRIVATE_CREDENTIAL",
                        "paid_model_calls": 0,
                        "metadata_get_requests": 0,
                    }
                )
            )
            return 3
        report, body = probe_metadata(configuration, key, kind=args.kind, model=args.model)
        destination.mkdir(mode=0o700)
        for name, data in (
            ("response.body", body),
            ("review.json", (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()),
        ):
            fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        print(json.dumps(report))
        return (
            0
            if report["result"]
            in ("REMOTE_MODEL_METADATA_READ", "WORKSPACE_PERMISSION_METADATA_READ")
            else 1
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "METADATA_PROBE_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_model_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
