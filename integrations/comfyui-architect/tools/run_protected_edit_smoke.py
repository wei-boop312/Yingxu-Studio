"""One owned, explicit-mask local edit; no identity/try-on claim or auto retry."""
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_identity_reference import request, COMFY, ARCHIVE


def main():
    reference_name = "owned_identity_fe02421dc76e4527840db97261ec0d60.png"
    reference = COMFY / "input" / reference_name
    if hashlib.sha256(reference.read_bytes()).hexdigest() != "d9a728d52b218004efc041210a9ee1223100b757ede383faef8b3116d19ae894":
        raise RuntimeError("Owned source changed")
    source = Image.open(reference).convert("RGB")
    if source.size != (512, 512):
        raise RuntimeError("Source geometry changed")
    run_id = "owned_protected_edit_" + uuid.uuid4().hex
    # Explicit manually selected lower-shirt rectangle. No face detection or inferred consent.
    mask = Image.new("L", source.size, 0)
    mask.paste(255, (0, 448, 512, 512))
    mask_name = run_id + "_mask.png"
    with (COMFY / "input" / mask_name).open("xb") as handle:
        mask.convert("RGB").save(handle, format="PNG")
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": reference_name}},
        "2": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "realistic photograph, bright blue cotton t-shirt, plain blue fabric, soft studio lighting"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": "text, watermark, pattern, distorted fabric"}},
        "7": {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["1", 0], "vae": ["4", 2], "mask": ["3", 0], "grow_mask_by": 0}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": 628913, "steps": 20, "cfg": 5.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 0.75}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
        "10": {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["1", 0], "source": ["9", 0], "x": 0, "y": 0, "resize_source": False, "mask": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "workflow_architect_owned_tests/" + run_id + "/protected"}},
    }
    registry = request("/object_info")
    if any(node["class_type"] not in registry for node in graph.values()):
        raise RuntimeError("Required native nodes missing")
    if "sd_xl_base_1.0.safetensors" not in registry["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]:
        raise RuntimeError("Explicit checkpoint unavailable")
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Queue busy; no submission")
    receipt = {"run_id": run_id, "status": "prepared", "private_input_used": False,
               "source": str(reference), "mask": str(COMFY / "input" / mask_name),
               "source_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
               "mask_sha256": hashlib.sha256((COMFY / "input" / mask_name).read_bytes()).hexdigest(),
               "editable_rectangle": [0, 448, 512, 512], "identity_generation_verified": False,
               "virtual_tryon_verified": False, "quality_approved": False}
    path = ARCHIVE / (run_id + ".json")
    def save():
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    (ARCHIVE / (run_id + "_api.json")).write_text(json.dumps(graph, indent=2), encoding="utf-8")
    start = time.monotonic()
    # Single submission; an unknown HTTP outcome must never cause automatic resubmission.
    receipt["status"] = "submission-unknown"
    save()
    submitted = request("/prompt", {"prompt": graph, "client_id": run_id})
    receipt.update(prompt_id=submitted["prompt_id"], status="submitted")
    save()
    print(json.dumps(receipt), flush=True)
    while time.monotonic() - start < 300:
        history = request("/history/" + receipt["prompt_id"]).get(receipt["prompt_id"])
        if history and history.get("status", {}).get("status_str") == "error":
            receipt.update(status="execution-failed", messages=history["status"].get("messages", []))
            save()
            raise RuntimeError("Owned edit failed; no retry")
        if history and history.get("status", {}).get("completed"):
            images = history.get("outputs", {}).get("11", {}).get("images", [])
            if len(images) != 1 or images[0].get("type") != "output":
                raise RuntimeError("Unexpected output")
            output = (COMFY / "output" / images[0]["subfolder"] / images[0]["filename"]).resolve()
            if not output.is_relative_to((COMFY / "output/workflow_architect_owned_tests" / run_id).resolve()):
                raise RuntimeError("Output scope mismatch")
            with Image.open(output) as image:
                image.load()
                if image.format != "PNG" or image.size != source.size:
                    raise RuntimeError("Output format/geometry mismatch")
                result = image.convert("RGB")
            difference = ImageChops.difference(source, result)
            changed = [any(pixel) for pixel in difference.getdata()]
            protected_changes = sum(value for value, selected in zip(changed, mask.getdata()) if selected == 0)
            editable_changes = sum(value for value, selected in zip(changed, mask.getdata()) if selected == 255)
            receipt.update(status="completed", output=str(output), output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                           elapsed_seconds=round(time.monotonic() - start, 2), protected_pixels=512 * 448,
                           protected_changed_pixels=protected_changes, editable_changed_pixels=editable_changes,
                           pixel_protection_passed=protected_changes == 0 and editable_changes > 0)
            save()
            print(json.dumps(receipt), flush=True)
            if not receipt["pixel_protection_passed"]:
                raise RuntimeError("Pixel protection acceptance failed")
            return
        time.sleep(1)
    receipt["status"] = "unresolved-no-retry"
    save()


if __name__ == "__main__":
    main()
