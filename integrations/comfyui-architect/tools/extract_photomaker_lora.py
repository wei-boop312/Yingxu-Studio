"""Extract official v1 LoRA tensors locally without changing source weights."""
import hashlib
import json
import uuid
from pathlib import Path
import torch
from safetensors.torch import save_file, load_file

ROOT = Path("E:/AI/ComfyUI_windows_portable/ComfyUI/models")
SOURCE = ROOT / "photomaker/photomaker-v1.bin"
TARGET = ROOT / "loras/workflow_architect_photomaker_v1_lora.safetensors"
SOURCE_SHA = "529d503fa378bfb3a74e3384ab2064d7269d59f0638324555d22067c31e275bc"


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    if TARGET.exists():
        raise RuntimeError("Derived target exists; preserve it")
    if sha(SOURCE) != SOURCE_SHA or not TARGET.parent.is_dir():
        raise RuntimeError("Source hash or explicit target directory mismatch")
    data = torch.load(SOURCE, map_location="cpu", weights_only=True)
    tensors = data.get("lora_weights")
    if not isinstance(tensors, dict) or len(tensors) != 1120 or any(not isinstance(value, torch.Tensor) for value in tensors.values()):
        raise RuntimeError("Unexpected official v1 LoRA section")
    temporary = TARGET.parent / (".workflow_architect_" + uuid.uuid4().hex + ".part")
    save_file({key: value.contiguous() for key, value in tensors.items()}, str(temporary), metadata={"origin_sha256": SOURCE_SHA, "origin_section": "lora_weights", "notice": "Locally extracted, not a separately downloaded official safetensors file"})
    restored = load_file(str(temporary), device="cpu")
    if set(restored) != set(tensors) or any(not torch.equal(restored[key], value) for key, value in tensors.items()):
        raise RuntimeError("Extracted tensors differ; temporary preserved, not installed")
    if TARGET.exists():
        raise RuntimeError("Destination appeared; do not overwrite")
    temporary.rename(TARGET)
    receipt = {"source": str(SOURCE), "source_sha256": SOURCE_SHA, "target": str(TARGET), "sha256": sha(TARGET), "bytes": TARGET.stat().st_size,
               "tensor_count": len(tensors), "all_tensors_equal": True, "downloaded_bytes": 0, "runtime_verified": False}
    output = Path(__file__).resolve().parents[2] / "research/comfyui-commerce-2026-10-14/photomaker-lora-extraction.json"
    if output.exists():
        raise RuntimeError("Receipt exists; preserve prior evidence")
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
