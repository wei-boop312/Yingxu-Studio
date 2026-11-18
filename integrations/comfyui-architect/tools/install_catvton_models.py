"""Verify user-downloaded weights before copying; fetch only bounded pinned JSON."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT.parent / "research/comfyui-commerce-2026-10-14"
HOME = Path("E:/AI/CatVTON-Research")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    plan = json.loads((ARCHIVE / "catvton-install-plan.json").read_text(encoding="utf-8"))
    aliases = ["catvton-mix-model.safetensors", "catvton-unet-fp16.safetensors", "catvton-safety-fp16.safetensors", "catvton-vae.safetensors"]
    selected = []
    # Verify every source before creating model copies.
    for item, alias in zip(plan["selected_weight_files"], aliases):
        source = HOME / "downloads" / alias
        if not source.exists():
            source = HOME / "downloads" / Path(item["path"]).name
        if not source.is_file() or source.stat().st_size != item["bytes"] or sha(source) != item["sha256"]:
            raise RuntimeError("Source size/SHA mismatch: " + source.name)
        folder = "attention" if item["repo"] == "zhengchong/CatVTON" else "vae" if item["repo"] == "stabilityai/sd-vae-ft-mse" else "base"
        selected.append((item, source, HOME / "models" / folder / item["path"]))
    receipt = {"status": "copying-verified-weights", "weights": [], "configs": [], "inference_executed": False}
    report = ARCHIVE / "catvton-model-install.json"
    def save():
        report.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    for item, source, target in selected:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size != item["bytes"] or sha(target) != item["sha256"]:
                raise RuntimeError("Target conflict; preserve and stop")
        else:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if sha(target) != item["sha256"]:
                raise RuntimeError("Copied target mismatch")
        receipt["weights"].append({"source": str(source), "target": str(target), "sha256": item["sha256"]})
        save()
    base = plan["selected_weight_files"][1]
    vae = plan["selected_weight_files"][3]
    for item, paths, folder in [(base, ["unet/config.json", "scheduler/scheduler_config.json", "feature_extractor/preprocessor_config.json", "safety_checker/config.json"], "base"), (vae, ["config.json"], "vae")]:
        for path in paths:
            target = HOME / "models" / folder / path
            url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{path}"
            with urllib.request.urlopen(url, timeout=20) as response:
                raw = response.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024 or not isinstance(json.loads(raw), dict):
                raise RuntimeError("Invalid/oversized configuration")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != raw:
                    raise RuntimeError("Config conflict; preserve and stop")
            else:
                with target.open("xb") as handle:
                    handle.write(raw)
            receipt["configs"].append({"target": str(target), "url": url, "sha256": hashlib.sha256(raw).hexdigest()})
            save()
    receipt["status"] = "weights-and-configs-installed-not-loaded"
    save()
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
