"""Verify isolated imports and tiny CUDA arithmetic, not model/quality acceptance."""
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT.parent / "research/comfyui-commerce-2026-10-14"
EXPECTED = Path("E:/AI/CatVTON-Research/venv").resolve()


def main():
    if Path(sys.prefix).resolve() != EXPECTED or sys.version_info[:2] != (3, 10):
        raise RuntimeError("Use dedicated CatVTON Python3.10 environment only")
    import torch
    import torchvision
    import diffusers
    import transformers
    import accelerate
    sys.path.insert(0, str(ARCHIVE / "catvton-upstream"))
    from model.pipeline import CatVTONPipeline
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    value = torch.ones((2, 2), device="cuda", dtype=torch.bfloat16)
    result = (value @ value).float().cpu().tolist()
    if result != [[2.0, 2.0], [2.0, 2.0]]:
        raise RuntimeError("CUDA arithmetic mismatch")
    del value
    receipt = {
        "status": "isolated-import-and-tiny-cuda-passed",
        "python": sys.version, "prefix": sys.prefix,
        "versions": {name: importlib.metadata.version(name) for name in
                     ["torch", "torchvision", "diffusers", "transformers", "accelerate", "huggingface-hub"]},
        "device": torch.cuda.get_device_name(), "bf16_supported": torch.cuda.is_bf16_supported(),
        "pipeline_import": CatVTONPipeline.__name__, "models_loaded": False,
        "inference_executed": False, "quality_verified": False,
    }
    (ARCHIVE / "catvton-environment-verified.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
