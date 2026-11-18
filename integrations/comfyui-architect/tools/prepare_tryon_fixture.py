"""One owned SDXL torso fixture plus explicit synthetic garment, never private input."""
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from generate_identity_reference import request, COMFY, ARCHIVE
from workflow_architect.service import ArchitectService
from workflow_architect.schemas import TaskSpec, InputRequirements, PromptRequirements, OutputRequirements


def main():
    run_id = "owned_tryon_inputs_" + uuid.uuid4().hex
    folder = Path("E:/AI/CatVTON-Research/fixtures") / run_id
    folder.mkdir(parents=True, exist_ok=False)
    spec = TaskSpec(task="text_to_image", garment_category="not_applicable",
        inputs=InputRequirements(person_image=False, garment_image=False),
        prompt=PromptRequirements(positive="studio catalog photograph of a fictional adult male fashion model, front view, standing straight, from head to upper thighs, entire head and entire torso visible, arms relaxed apart from body, wearing plain gray short sleeve t-shirt and black trousers, white background, centered, natural proportions",
            negative="closeup, cropped head, portrait close up, jacket, coat, shirtless, child, celebrity, text, watermark, hands covering torso", seed=924167, steps=20, cfg=5),
        output=OutputRequirements(width=512, height=768))
    service = ArchitectService()
    service.refresh(request("/object_info"))
    result = service.compile({"spec": spec.to_dict(), "model_overrides": {"checkpoint": "sd_xl_base_1.0.safetensors"}, "allow_draft": False})
    compiled = result["compiled"]
    if not compiled or not result["validation"]["valid"] or compiled["draft"]:
        raise RuntimeError("Fixture compile rejected")
    graph = compiled["api_prompt"]
    if {node["class_type"] for node in graph.values()} != {"CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"}:
        raise RuntimeError("Unexpected fixture graph")
    save_id = next(key for key, node in graph.items() if node["class_type"] == "SaveImage")
    graph[save_id]["inputs"]["filename_prefix"] = "workflow_architect_owned_tests/" + run_id + "/person"
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Queue busy")
    receipt = {"run_id": run_id, "status": "submission-unknown", "private_input_used": False, "folder": str(folder)}
    report = ARCHIVE / (run_id + ".json")
    def save():
        report.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    save()
    receipt.update(prompt_id=request("/prompt", {"prompt": graph, "client_id": run_id})["prompt_id"], status="submitted")
    save()
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        history = request("/history/" + receipt["prompt_id"]).get(receipt["prompt_id"])
        if history and history.get("status", {}).get("status_str") == "error":
            receipt["status"] = "generation-failed"
            save()
            raise RuntimeError("Fixture generation failed; no retry")
        if history and history.get("status", {}).get("completed"):
            images = history.get("outputs", {}).get(save_id, {}).get("images", [])
            if len(images) != 1 or images[0].get("type") != "output":
                raise RuntimeError("Unexpected output")
            output = (COMFY / "output" / images[0]["subfolder"] / images[0]["filename"]).resolve()
            if not output.is_relative_to((COMFY / "output/workflow_architect_owned_tests" / run_id).resolve()):
                raise RuntimeError("Scope mismatch")
            with Image.open(output) as image:
                image.load()
                if image.size != (512, 768) or image.format != "PNG":
                    raise RuntimeError("Unexpected fixture geometry")
                image.convert("RGB").save(folder / "person.png")
            # Explicit original flat-lay drawing, no downloaded garment or third-party logo.
            garment = Image.new("RGB", (512, 768), "white")
            silhouette = [(155, 170), (210, 150), (302, 150), (357, 170), (455, 260), (380, 330), (350, 298), (350, 610), (162, 610), (162, 298), (132, 330), (57, 260)]
            shape = Image.new("L", garment.size)
            ImageDraw.Draw(shape).polygon(silhouette, fill=255)
            fabric = Image.new("RGB", garment.size, (190, 35, 45))
            draw = ImageDraw.Draw(fabric)
            for y in range(260, 610, 100):
                draw.rectangle((0, y, 511, y+22), fill=(245, 210, 60))
            garment.paste(fabric, mask=shape)
            ImageDraw.Draw(garment).ellipse((210, 120, 302, 205), fill="white", outline=(120, 20, 30), width=5)
            garment.save(folder / "garment.png")
            receipt.update(status="fixtures-generated-pending-manual-mask-review", person=str(folder / "person.png"), garment=str(folder / "garment.png"),
                person_sha256=hashlib.sha256((folder / "person.png").read_bytes()).hexdigest(), garment_sha256=hashlib.sha256((folder / "garment.png").read_bytes()).hexdigest())
            save()
            print(json.dumps(receipt), flush=True)
            return
        time.sleep(1)
    raise RuntimeError("Fixture unresolved; no retry")


if __name__ == "__main__":
    main()
