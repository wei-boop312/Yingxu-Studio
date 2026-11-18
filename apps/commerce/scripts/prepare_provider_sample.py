"""Explicit local sample copy; never modify the original or call a model."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.services.provider_sample import prepare_provider_sample


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-edge", type=int, default=2000)
    parser.add_argument("--allow-resize", action="store_true")
    parser.add_argument(
        "--dataset-kind", choices=("real_product_source", "synthetic_fixture"), required=True
    )
    args = parser.parse_args()
    try:
        output = args.output_dir.absolute()
        private_root = next(
            (root for root in (ROOT / ".local", ROOT / "work") if output.is_relative_to(root)), None
        )
        if private_root is None:
            raise ValueError("private_sample_output_required")
        report = await prepare_provider_sample(
            args.source,
            output,
            private_root,
            edge=args.max_edge,
            allow_resize=args.allow_resize,
            dataset_kind=args.dataset_kind,
        )
        print(
            json.dumps(
                {
                    "result": "PRIVATE_SAMPLE_PREPARED",
                    "width": report["width"],
                    "height": report["height"],
                    "resized": report["resized"],
                    "original_source_written": False,
                    "pixel_protection_verified": False,
                    "quality_evaluation": "NOT_RUN",
                    "paid_model_calls": 0,
                }
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "SAMPLE_PREPARATION_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_model_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
