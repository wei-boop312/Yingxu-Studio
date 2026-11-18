"""Explicit fixed-plan downloads, no hub token/cache, retry, or TLS weakening."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT.parent / "research/comfyui-commerce-2026-10-14"
MODELS = Path("E:/AI/CatVTON-Research/models")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    plan = json.loads((ARCHIVE / "catvton-install-plan.json").read_text(encoding="utf-8"))
    receipt = {"status": "started", "files": [], "inference_executed": False}
    receipt_path = ARCHIVE / "catvton-model-download.json"
    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    for item in plan["selected_weight_files"]:
        repo = item["repo"]
        base = "attention" if repo == "zhengchong/CatVTON" else "vae" if repo == "stabilityai/sd-vae-ft-mse" else "base"
        target = MODELS / base / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {**item, "target": str(target)}
        receipt["files"].append(entry)
        if target.exists():
            if target.stat().st_size != item["bytes"] or digest(target) != item["sha256"]:
                raise RuntimeError("Existing file mismatch; preserve and stop")
            entry["status"] = "existing-verified"
            save()
            continue
        partial = target.with_name(target.name + ".partial")
        if partial.exists():
            raise RuntimeError("Partial exists; explicit review required before resume")
        url = f"https://huggingface.co/{repo}/resolve/{item['revision']}/{item['path']}"
        entry.update(status="downloading", url=url)
        save()
        start = time.monotonic()
        result = subprocess.run(["curl.exe", "--fail", "--location", "--connect-timeout", "20", "--max-time", "180",
                                 "--speed-limit", "1024", "--speed-time", "30", "--output", str(partial), url])
        entry.update(elapsed_seconds=round(time.monotonic()-start, 2), curl_exit=result.returncode,
                     received_bytes=partial.stat().st_size if partial.exists() else 0)
        if result.returncode or not partial.exists() or partial.stat().st_size != item["bytes"]:
            entry["status"] = "download-incomplete-preserved"
            receipt["status"] = "stopped-download-failed"
            save()
            raise RuntimeError("Download incomplete; no retry or fallback")
        if digest(partial) != item["sha256"]:
            entry["status"] = "sha-mismatch-preserved"
            receipt["status"] = "stopped-sha-mismatch"
            save()
            raise RuntimeError("SHA mismatch; no load")
        partial.rename(target)
        entry["status"] = "sha-verified"
        save()
    receipt["status"] = "weights-verified-configs-still-required"
    save()
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
