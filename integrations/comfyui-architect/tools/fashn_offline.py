"""Dedicated local-path FASHN research loader, CPU pre-processing to save VRAM."""
import os
from pathlib import Path
import sys
import json
import time

HOME=Path("E:/AI/FASHN-Research")
ARCHIVE=Path(__file__).resolve().parents[2]/"research/comfyui-commerce-2026-10-14"
if Path(sys.prefix).resolve()!=(HOME/"venv").resolve():
    raise RuntimeError("Dedicated FASHN environment required")
os.environ["HF_HOME"]=str(HOME/"cache")
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["TRANSFORMERS_OFFLINE"]="1"
# Import the independently verified fixed source; no editable build or implicit install.
sys.path.insert(0,str(HOME/"source/src"))

import torch
import onnxruntime as ort
from safetensors.torch import load_file
from fashn_human_parser import FashnHumanParser
from fashn_vton import TryOnPipeline
from fashn_vton.tryon_mmdit import TryOnModel
from fashn_vton.dwpose import DWposeDetector


class OfflineFashn(TryOnPipeline):
    def _setup_tryon_model(self):
        # Avoid simultaneously constructing FP32 weights and a checkpoint on GPU.
        self.tryon_model=TryOnModel().to(dtype=self.inference_dtype)
        state=load_file(str(HOME/"weights/model.safetensors"),device="cpu")
        self.tryon_model.load_state_dict(state,strict=True)
        self.loaded_tensors=len(state)
        del state
        self.tryon_model.to(self.device).eval()

    def _setup_pose_model(self):
        # Intentional CPU pose inference, not a silent failed GPU provider fallback.
        self.pose_model=DWposeDetector(checkpoints_dir=str(HOME/"weights/dwpose"),device="cpu")

    def _setup_hp_model(self):
        self.hp_model=FashnHumanParser(model_id=str(HOME/"parser"),device="cpu")


def main():
    started=time.monotonic()
    pipeline=OfflineFashn(weights_dir=str(HOME/"weights"),device="cpu")
    result={"status":"offline-cpu-components-loaded", "tryon_tensors_strictly_loaded":pipeline.loaded_tensors,
            "input_shape":list(pipeline.tryon_model.input_shape),"pose_providers":pipeline.pose_model.pose_estimation.session_pose.get_providers(),
            "parser_device":str(pipeline.hp_model.device), "ort_available_providers":ort.get_available_providers(),
            "torch_version":torch.__version__,"cuda_available":torch.cuda.is_available(),
            "elapsed_seconds":round(time.monotonic()-started,2),"inference_executed":False,
            "low_waist_quality_verified":False,"commercial_license_stack_cleared":False}
    if torch.cuda.is_available():
        value=torch.ones((2,2),device="cuda",dtype=torch.bfloat16)
        if (value@value).float().cpu().tolist()!=[[2.,2.],[2.,2.]]:
            raise RuntimeError("Tiny CUDA arithmetic mismatch")
        del value
        result["tiny_bf16_cuda_passed"]=True
    (ARCHIVE/"fashn-offline-load.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result),flush=True)


if __name__=="__main__":
    main()
