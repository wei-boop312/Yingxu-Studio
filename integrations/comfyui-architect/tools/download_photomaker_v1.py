"""User-authorized single pinned weight download, bounded and SHA-verified."""
import hashlib
import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path

URL = "https://huggingface.co/TencentARC/PhotoMaker/resolve/42a0f2dece1836c370906d0ec5143fabb92cbf42/photomaker-v1.bin"
SHA = "529d503fa378bfb3a74e3384ab2064d7269d59f0638324555d22067c31e275bc"
TARGET = Path("E:/AI/ComfyUI_windows_portable/ComfyUI/models/photomaker/photomaker-v1.bin")
ARCHIVE = Path(__file__).resolve().parents[2] / "research/comfyui-commerce-2026-10-14"


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", action="store_true", help="Public mirror transport only; original official SHA remains mandatory")
    args = parser.parse_args()
    transport_url = URL.replace("https://huggingface.co/", "https://hf-mirror.com/") if args.mirror else URL
    if not TARGET.parent.is_dir():
        raise RuntimeError("Expected explicit model directory is missing")
    if TARGET.exists():
        if sha_file(TARGET) != SHA:
            raise RuntimeError("Existing model differs; do not overwrite")
        print("Existing target has expected SHA; no download", flush=True)
        return
    temporary = TARGET.parent / (".workflow_architect_" + uuid.uuid4().hex + ".part")
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + 1200
    last_report = 0
    print("Downloading one pinned official weight; no retries or model execution", flush=True)
    try:
        with urllib.request.urlopen(transport_url, timeout=30) as response, temporary.open("xb") as output:
            declared = response.headers.get("Content-Length")
            expected_length = int(declared) if declared is not None else None
            if expected_length is not None and not 1 <= expected_length <= 1024 * 1024 * 1024:
                raise RuntimeError("Weight length outside authorized bound")
            while True:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Download deadline exceeded")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 1024 * 1024 * 1024:
                    raise RuntimeError("Weight exceeds authorized bound")
                output.write(chunk)
                digest.update(chunk)
                if total - last_report >= 64 * 1024 * 1024:
                    print(f"Received {total // (1024 * 1024)} MiB", flush=True)
                    last_report = total
        if expected_length is not None and total != expected_length:
            raise RuntimeError("Incomplete weight")
        if digest.hexdigest() != SHA or sha_file(temporary) != SHA:
            raise RuntimeError("Weight SHA mismatch; not installed")
        # Windows rename does not overwrite an existing target; partial bytes
        # never acquire the model's .bin extension before complete verification.
        if TARGET.exists():
            raise RuntimeError("Destination appeared; preserve it and verified temporary")
        temporary.rename(TARGET)
        receipt = {"source": URL, "transport_url": transport_url, "third_party_mirror": args.mirror, "target": str(TARGET), "bytes": total, "sha256": SHA,
                   "installed": True, "loaded": False, "runtime_verified": False}
        receipt_path = ARCHIVE / ("photomaker-download-" + uuid.uuid4().hex + ".json")
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
    except Exception as error:
        print(json.dumps({"installed": False, "error_type": type(error).__name__, "preserved_temporary": str(temporary) if temporary.exists() else None, "received_bytes": total}), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
