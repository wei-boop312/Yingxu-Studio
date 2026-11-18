"""One authorized PhotoMaker run on the previously owned synthetic portrait."""
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from workflow_architect.service import ArchitectService
from workflow_architect.identity import CHECKPOINT, WEIGHT, LORA
from generate_identity_reference import request, COMFY, ARCHIVE


def main():
    weight = COMFY / "models/photomaker" / WEIGHT
    digest = hashlib.sha256()
    with weight.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != "529d503fa378bfb3a74e3384ab2064d7269d59f0638324555d22067c31e275bc":
        raise RuntimeError("Weight integrity mismatch; no execution")
    reference_name = "owned_identity_fe02421dc76e4527840db97261ec0d60.png"
    reference = COMFY / "input" / reference_name
    with Image.open(reference) as image:
        image.load()
        if image.size != (512, 512):
            raise RuntimeError("Owned synthetic reference geometry changed")
    reference_sha = hashlib.sha256(reference.read_bytes()).hexdigest()
    if reference_sha != "d9a728d52b218004efc041210a9ee1223100b757ede383faef8b3116d19ae894":
        raise RuntimeError("Owned synthetic reference bytes changed; no private-image fallback")
    service = ArchitectService()
    service.refresh(request("/object_info"))
    expected = service.prepare_identity_reference({})["expected"]
    prepared = service.prepare_identity_reference({"build_draft": True, "portrait_authorized": True,
        "expected": expected,
        "reference_image": reference_name, "model_overrides": {"checkpoint": CHECKPOINT, "photomaker": WEIGHT, "identity_lora": LORA},
        "prompt": "wearing a dark blue jacket, close up head and shoulders portrait, centered face, looking directly at camera, plain light gray studio background, visible eyes, neutral expression, realistic photography", "subject": "man", "lora_strength": 0.6, "steps": 24, "width": 512, "height": 512})
    compiled = prepared["compiled"]
    if not compiled or not prepared["validation"]["valid"] or compiled["draft"]:
        raise RuntimeError("Actual PhotoMaker compile failed")
    prompt = compiled["api_prompt"]
    expected_nodes = {"LoadImage", "CheckpointLoaderSimple", "LoraLoaderModelOnly", "PhotoMakerLoader", "PhotoMakerEncode", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"}
    if len(prompt) != 10 or {node["class_type"] for node in prompt.values()} != expected_nodes:
        raise RuntimeError("Unexpected graph; no execution")
    run_id = "owned_photomaker_" + uuid.uuid4().hex
    save_id = next(key for key, node in prompt.items() if node["class_type"] == "SaveImage")
    prompt[save_id]["inputs"]["filename_prefix"] = "workflow_architect_owned_tests/" + run_id + "/candidate"
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Queue busy; no submission")
    started = time.monotonic()
    submitted = request("/prompt", {"prompt": prompt, "client_id": run_id})
    prompt_id = submitted["prompt_id"]
    receipt = {"run_id": run_id, "prompt_id": prompt_id, "status": "submitted", "private_input_used": False,
               "reference_input": str(reference), "reference_sha256": reference_sha, "model_sha256": digest.hexdigest(),
               "dimensions": [512, 512], "steps": 24, "subject": "man", "identity_lora_strength": 0.6, "identity_lora_applied": True, "quality_verified": False}
    receipt_path = ARCHIVE / (run_id + ".json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    (ARCHIVE / (run_id + "_workflow.json")).write_text(json.dumps(compiled["ui_workflow"], ensure_ascii=False, indent=2), encoding="utf-8")
    deadline = started + 300
    while time.monotonic() < deadline:
        history = request("/history/" + prompt_id).get(prompt_id)
        if history:
            status = history.get("status", {})
            if status.get("status_str") == "error":
                receipt.update(status="failed", elapsed_seconds=round(time.monotonic() - started, 2), errors=[message[1] for message in status.get("messages", []) if message[0] == "execution_error"])
                receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({"status": "failed", "receipt": str(receipt_path)}), flush=True)
                return
            if status.get("completed"):
                outputs = history.get("outputs", {}).get(save_id, {}).get("images", [])
                if len(outputs) != 1 or outputs[0].get("type") != "output":
                    raise RuntimeError("Unexpected owned output")
                output = (COMFY / "output" / outputs[0]["subfolder"] / outputs[0]["filename"]).resolve()
                if not output.is_relative_to((COMFY / "output/workflow_architect_owned_tests" / run_id).resolve()):
                    raise RuntimeError("Output scope mismatch")
                with Image.open(output) as image:
                    image.load()
                    if image.size != (512, 512) or image.format != "PNG":
                        raise RuntimeError("Output geometry/format mismatch")
                receipt.update(status="success", output=str(output), elapsed_seconds=round(time.monotonic() - started, 2), output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(), runtime_verified=True)
                receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(receipt, ensure_ascii=False), flush=True)
                return
        time.sleep(1)
    print(json.dumps({"status": "unresolved", "receipt": str(receipt_path), "notice": "No retry or global interrupt"}), flush=True)


if __name__ == "__main__":
    main()
