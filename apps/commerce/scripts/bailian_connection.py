"""Prepare/review a private local Beijing Qwen connection; never calls supplier HTTP."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.core.credentials import private_credential
from visual_platform.providers.bailian_connection import (
    connection_status,
    load_connection,
    prepare_connection,
)
from visual_platform.services.quality_export import private_destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-local", action="store_true")
    mode.add_argument("--config", type=Path)
    parser.add_argument("--endpoint-origin")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check-credentials", action="store_true")
    args = parser.parse_args()
    try:
        if args.prepare_local:
            if not args.endpoint_origin or args.output_dir is None or args.check_credentials:
                raise ValueError("prepare_requires_origin_and_private_output")
            output = args.output_dir.absolute()
            root = next(
                (p for p in (ROOT / ".local", ROOT / "work") if output.is_relative_to(p)), None
            )
            if root is None:
                raise ValueError("private_connection_output_required")
            private_destination(output, root)
            key = private_credential("DASHSCOPE_API_KEY")
            if not key:
                print(
                    json.dumps(
                        {
                            "result": "WAITING_PRIVATE_CREDENTIAL",
                            "variable": "DASHSCOPE_API_KEY",
                            "paid_model_calls": 0,
                        }
                    )
                )
                return 3
            configuration = prepare_connection(args.endpoint_origin, key, output, root)
            result = connection_status(configuration, key)
        else:
            if args.endpoint_origin or args.output_dir is not None:
                raise ValueError("review_does_not_create_or_replace_configuration")
            configuration = load_connection(args.config)
            key = private_credential("DASHSCOPE_API_KEY") if args.check_credentials else None
            result = connection_status(configuration, key)
            if not args.check_credentials:
                result["credential_matches"] = None
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "CONNECTION_PREPARATION_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_model_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
