"""Exercise a local canonical final PNG with fixed MockTransport, never real inference."""

import argparse
import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.providers.gemini import fallback
from visual_platform.providers.qwen_vision import MODEL, PATH, QwenVisionProvider
from visual_platform.services.quality_export import private_destination
from visual_platform.services.upload import decode


async def verify(source, output):
    root = next(
        (
            root
            for root in (ROOT / ".local", ROOT / "work")
            if output.absolute().is_relative_to(root)
        ),
        None,
    )
    if root is None:
        raise ValueError("private_sample_proof_destination_required")
    destination = private_destination(output.absolute(), root)
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= 10_000_000:
        raise ValueError("bounded_regular_final_png_required")
    with source.open("rb") as stream:
        raw = stream.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError("sample_grew_beyond_limit")
    digest = hashlib.sha256(raw).hexdigest()
    canonical, meta = await decode(raw, "image/png")
    width, height = meta["width"], meta["height"]
    if hashlib.sha256(canonical).hexdigest() != digest or width * height > 4_000_000:
        raise ValueError("canonical_final_png_of_at_most_four_megapixels_required")
    calls = []

    def route(request):
        document = json.loads(request.content)
        image = document["messages"][1]["content"][0]["image_url"]["url"]
        prefix = "data:image/png;base64,"
        if (
            request.method != "POST"
            or request.url.path != PATH
            or document["model"] != MODEL
            or not image.startswith(prefix)
            or base64.b64decode(image[len(prefix) :], validate=True) != raw
        ):
            raise ValueError("offline_sample_request_binding_mismatch")
        calls.append("explicit_mock_post")
        facts = fallback(width, height, "offline_fixture_not_real_model_analysis")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-offline-sample-only",
                "model": MODEL,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": facts.model_dump_json()},
                    }
                ],
            },
        )

    destination.mkdir(mode=0o700)
    provider = QwenVisionProvider(
        "synthetic-offline-sample-only",
        destination / "archive",
        endpoint_origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
        transport=httpx.MockTransport(route),
    )
    try:
        receipt = await provider.submit(
            uuid4(), raw, expected_sha256=digest, width=width, height=height
        )
        record = provider.fetch_facts(receipt.provider_id)
        if (
            record["facts"]["analysis_complete"]
            or not record["manual_review_required"]
            or record["quantities"] is not None
            or calls != ["explicit_mock_post"]
        ):
            raise ValueError("offline_sample_must_remain_manual_and_unpriced")
        if source.is_symlink() or not source.is_file():
            raise ValueError("source_changed_during_offline_sample_review")
        with source.open("rb") as stream:
            after = stream.read(10_000_001)
        report = {
            "result": "OFFLINE_FINAL_SAMPLE_PROTOCOL_VERIFIED",
            "width": width,
            "height": height,
            "final_sha256": digest,
            "request_sha256": record["request_sha256"],
            "wire_response_sha256": record["wire_response_sha256"],
            "explicit_mock_posts": len(calls),
            "remote_requests": 0,
            "paid_calls": 0,
            "real_model_analysis": False,
            "manual_review_required": True,
            "billing_usage_integrity": record["billing_usage_integrity"],
            "source_unchanged": hashlib.sha256(after).hexdigest() == digest,
            "execution_enabled": False,
        }
        if not report["source_unchanged"]:
            raise ValueError("source_changed_during_offline_sample_review")
        provider._write(destination / "proof.json", (json.dumps(report, indent=2) + "\n").encode())
        return report
    finally:
        await provider.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not args.run:
        print(
            json.dumps(
                {"result": "OFFLINE_SAMPLE_NOT_REQUESTED", "remote_requests": 0, "paid_calls": 0}
            )
        )
        return 3
    if args.source is None or args.output_dir is None:
        parser.error("--run requires --source and a new private --output-dir")
    try:
        print(json.dumps(asyncio.run(verify(args.source, args.output_dir))))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "OFFLINE_SAMPLE_REJECTED",
                    "error_type": type(error).__name__,
                    "remote_requests": 0,
                    "paid_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
