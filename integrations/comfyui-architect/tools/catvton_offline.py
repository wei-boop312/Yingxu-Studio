"""Local-only research loader; preserves upstream inference, no implicit download."""
import os
from pathlib import Path
import sys

HOME = Path("E:/AI/CatVTON-Research")
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT.parent / "research/comfyui-commerce-2026-10-14"
# Set before third-party imports. No credentials or private images are read.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HOME"] = str(HOME / "cache")
sys.path.insert(0, str(ARCHIVE / "catvton-upstream"))

import torch
from diffusers import AutoencoderKL, DDIMScheduler, UNet2DConditionModel
from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker
from safetensors.torch import load_file
from transformers import CLIPImageProcessor
from model.pipeline import CatVTONPipeline
from model.attn_processor import SkipAttnProcessor
from model.utils import get_trainable_module, init_adapter


class OfflineCatVTON(CatVTONPipeline):
    def __init__(self, device="cpu", dtype=torch.float32):
        if Path(sys.prefix).resolve() != (HOME / "venv").resolve():
            raise RuntimeError("Dedicated environment required")
        if device not in ("cpu", "cuda"):
            raise ValueError("Explicit CPU/CUDA device required")
        self.device = device
        self.weight_dtype = dtype
        self.skip_safety_check = False
        base = HOME / "models/base"
        self.noise_scheduler = DDIMScheduler.from_pretrained(str(base / "scheduler"), local_files_only=True)
        self.vae = AutoencoderKL.from_pretrained(str(HOME / "models/vae"), local_files_only=True,
                                               use_safetensors=True, torch_dtype=dtype).to(device)
        self.feature_extractor = CLIPImageProcessor.from_pretrained(str(base / "feature_extractor"), local_files_only=True)
        self.safety_checker = StableDiffusionSafetyChecker.from_pretrained(str(base / "safety_checker"),
            local_files_only=True, use_safetensors=True, variant="fp16", torch_dtype=dtype).to(device)
        self.unet = UNet2DConditionModel.from_pretrained(str(base / "unet"), local_files_only=True,
            use_safetensors=True, variant="fp16", torch_dtype=dtype).to(device)
        init_adapter(self.unet, cross_attn_cls=SkipAttnProcessor)
        self.attn_modules = get_trainable_module(self.unet, "attention")
        state = load_file(str(HOME / "models/attention/mix-48k-1024/attention/model.safetensors"), device="cpu")
        # Upstream ModuleList contains parent attention modules AND their children.
        # Checkpoint stores only parent paths. Accept a missing path solely when its
        # actual Parameter object is already represented by a supplied checkpoint key.
        parameters = dict(self.attn_modules.named_parameters(remove_duplicate=False))
        buffers = dict(self.attn_modules.named_buffers(remove_duplicate=False))
        actual = {**parameters, **buffers}
        supplied_ids = set()
        for key, value in state.items():
            if key not in actual or actual[key].shape != value.shape:
                raise RuntimeError("Unexpected attention key/shape: " + key)
            supplied_ids.add(id(actual[key]))
        if any(id(value) not in supplied_ids for value in actual.values()):
            raise RuntimeError("Attention checkpoint leaves an independent tensor uninitialized")
        result = self.attn_modules.load_state_dict(state, strict=False)
        if result.unexpected_keys or any(key not in actual or id(actual[key]) not in supplied_ids for key in result.missing_keys):
            raise RuntimeError("Attention alias mapping mismatch")
        for key, value in state.items():
            if not torch.equal(actual[key].detach().cpu(), value.to(dtype=actual[key].dtype)):
                raise RuntimeError("Attention loaded tensor mismatch: " + key)
        self.attention_alias_paths = len(result.missing_keys)
        self.loaded_attention_tensors = len(state)
        del state


def verify_cpu():
    import json
    import time
    start = time.monotonic()
    pipeline = OfflineCatVTON()
    receipt = {"status": "cpu-offline-components-loaded", "safety_checker_enabled": not pipeline.skip_safety_check,
               "attention_tensors_strictly_loaded": pipeline.loaded_attention_tensors,
               "duplicate_parameter_alias_paths_verified": pipeline.attention_alias_paths,
               "unet_in_channels": pipeline.unet.config.in_channels,
               "elapsed_seconds": round(time.monotonic() - start, 2), "inference_executed": False,
               "quality_verified": False, "commercial_permission_verified": False}
    (ARCHIVE / "catvton-offline-load.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    verify_cpu()
