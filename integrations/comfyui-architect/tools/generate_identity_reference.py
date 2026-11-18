"""One authorized local SDXL synthetic adult portrait; no private image input."""
import json
import shutil
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow_architect.service import ArchitectService
from workflow_architect.schemas import TaskSpec, InputRequirements, PromptRequirements, OutputRequirements

COMFY = Path("E:/AI/ComfyUI_windows_portable/ComfyUI")
ARCHIVE = ROOT.parent / "research/comfyui-commerce-2026-10-14"


def request(route, payload=None, limit=16 * 1024 * 1024):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request("http://127.0.0.1:8188" + route, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise RuntimeError("Response limit exceeded")
    return json.loads(raw)


def main():
    run_id = "owned_identity_" + uuid.uuid4().hex
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Queue busy; no submission")
    service = ArchitectService()
    service.refresh(request("/object_info"))
    spec = TaskSpec(task="text_to_image", garment_category="not_applicable",
                    inputs=InputRequirements(person_image=False, garment_image=False),
                    prompt=PromptRequirements(positive="a realistic studio photograph of a fictional adult male fashion model, head and shoulders portrait, short brown hair, neutral expression, plain gray t-shirt, looking directly at camera, light gray background, soft studio lighting, natural facial detail",
                                              negative="child, celebrity, text, watermark, blurry, deformed face, duplicate person", seed=628913, steps=16, cfg=5.0),
                    output=OutputRequirements(width=512, height=512))
    result = service.compile({"spec": spec.to_dict(), "model_overrides": {"checkpoint": "sd_xl_base_1.0.safetensors"}, "allow_draft": False})
    compiled = result["compiled"]
    if not compiled or not result["validation"]["valid"] or compiled["draft"]:
        raise RuntimeError("Synthetic reference compile failed")
    prompt = compiled["api_prompt"]
    if {node["class_type"] for node in prompt.values()} != {"CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"}:
        raise RuntimeError("Unexpected node; no submission")
    save_id = next(key for key, node in prompt.items() if node["class_type"] == "SaveImage")
    prompt[save_id]["inputs"]["filename_prefix"] = "workflow_architect_owned_tests/" + run_id + "/reference"
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Queue changed; no submission")
    # Exactly one POST. Unknown response is never retried.
    submitted = request("/prompt", {"prompt": prompt, "client_id": run_id})
    prompt_id = submitted["prompt_id"]
    receipt_path = ARCHIVE / (run_id + ".json")
    receipt = {"run_id": run_id, "prompt_id": prompt_id, "status": "submitted", "purpose": "authorized-synthetic-adult-reference-only-not-PhotoMaker-test", "private_input_used": False}
    print(json.dumps(receipt), flush=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        history = request("/history/" + prompt_id).get(prompt_id)
        if history:
            status = history.get("status", {})
            if status.get("status_str") == "error":
                receipt["status"] = "failed"
                receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                raise RuntimeError("Owned prompt failed; preserved prompt ID, no retry")
            if status.get("completed"):
                outputs = history.get("outputs", {}).get(save_id, {}).get("images", [])
                if len(outputs) != 1 or outputs[0].get("type") != "output":
                    raise RuntimeError("Unexpected output")
                output = (COMFY / "output" / outputs[0]["subfolder"] / outputs[0]["filename"]).resolve()
                allowed = (COMFY / "output/workflow_architect_owned_tests" / run_id).resolve()
                if not output.is_relative_to(allowed):
                    raise RuntimeError("Output outside owned scope")
                with Image.open(output) as image:
                    image.load()
                    if image.size != (512, 512) or image.format != "PNG":
                        raise RuntimeError("Unexpected synthetic image dimensions")
                target = COMFY / "input" / (run_id + ".png")
                with output.open("rb") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                receipt.update(status="success", output=str(output), reference_input=str(target), dimensions=[512, 512], photomaker_verified=False)
                receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(receipt, ensure_ascii=False), flush=True)
                return
        time.sleep(1)
    raise RuntimeError("Owned task still unresolved; receipt preserved, no retry or global interrupt")


if __name__ == "__main__":
    main()
