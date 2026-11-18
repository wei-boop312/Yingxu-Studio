"""Owned CPU image smoke test through an already running local ComfyUI.

No model load, diffusion, environment change, browser graph mutation or retry.
Run with the existing portable Python (Pillow required).
"""
import json
import sys
import time
import uuid
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_architect.service import ArchitectService


BASE = "http://127.0.0.1:8188"
ROOT = Path("E:/AI/ComfyUI_windows_portable/ComfyUI").resolve()


def request(route, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + route, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise RuntimeError("Response too large")
    return json.loads(raw)


def main():
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("Queue busy; no test submitted")
    info = request("/object_info")
    for name in ("LoadImage", "ImageScale", "SaveImage"):
        if name not in info:
            raise RuntimeError("Missing core image node")
    run_id = "owned_smoke_" + uuid.uuid4().hex
    # LoadImage's live selection list is top-level only in this environment.
    # Use collision-resistant owned filenames instead of relaxing its whitelist.
    input_dir = ROOT / "input"
    if not input_dir.is_dir():
        raise RuntimeError("Missing local input directory")
    results = []
    service = ArchitectService()
    for index, source_size in enumerate(((512, 256), (256, 512), (320, 320))):
        image_name = f"{run_id}_sample_{index}.png"
        source = input_dir / image_name
        if source.exists():
            raise RuntimeError("Owned filename collision; do not overwrite")
        image = Image.new("RGB", source_size, "#f4eadb")
        draw = ImageDraw.Draw(image)
        draw.rectangle((source_size[0] // 4, source_size[1] // 4, source_size[0] * 3 // 4, source_size[1] * 3 // 4), fill="#24637b")
        draw.text((10, 10), "OWNED TEST - NOT A USER PRODUCT", fill="black")
        image.save(source)
        width, height = source_size
        scale = min(192 / width, 192 / height)
        target = (round(width * scale), round(height * scale))
        relative = source.relative_to(ROOT / "input").as_posix()
        queue = request("/queue")
        if queue["queue_running"] or queue["queue_pending"]:
            raise RuntimeError("Queue changed; remaining tests not submitted")
        # Re-read live definitions after creating this owned input. Compile through
        # the plugin, not a hand-written prompt; never choose a user's input.
        service.refresh(request("/object_info"))
        parsed = service.parse({"text": f"把图片调整尺寸为{target[0]}x{target[1]}"})
        built = service.compile({"spec": parsed["spec"], "input_overrides": {"source_image": relative}, "allow_draft": False})
        compiled = built["compiled"]
        if not compiled["execution_ready"] or compiled["draft"]:
            raise RuntimeError("Plugin compile not ready")
        prompt = compiled["api_prompt"]
        if set(node["class_type"] for node in prompt.values()) != {"LoadImage", "ImageScale", "SaveImage"}:
            raise RuntimeError("Unexpected executable node")
        save_ids = [node_id for node_id, node in prompt.items() if node["class_type"] == "SaveImage"]
        if len(save_ids) != 1:
            raise RuntimeError("Unexpected output nodes")
        save_id = save_ids[0]
        prompt[save_id]["inputs"]["filename_prefix"] = f"workflow_architect_owned_tests/{run_id}/sample_{index}"
        # Exactly one submission. A response failure is unknown, never retried.
        submitted = request("/prompt", {"prompt": prompt, "client_id": run_id})
        prompt_id = submitted["prompt_id"]
        deadline = time.monotonic() + 30
        history = None
        while time.monotonic() < deadline:
            history = request("/history/" + prompt_id).get(prompt_id)
            if history is not None:
                break
            time.sleep(0.25)
        if history is None or not history.get("status", {}).get("completed"):
            raise RuntimeError("Own prompt incomplete; preserve ID, no retry or global interrupt: " + prompt_id)
        if history["status"].get("status_str") != "success":
            raise RuntimeError("Own prompt failed: " + prompt_id)
        outputs = history.get("outputs", {}).get(save_id, {}).get("images", [])
        if len(outputs) != 1:
            raise RuntimeError("Unexpected output count")
        record = outputs[0]
        if record.get("type") != "output":
            raise RuntimeError("Unexpected output scope")
        output = (ROOT / "output" / record["subfolder"] / record["filename"]).resolve()
        allowed = (ROOT / "output" / "workflow_architect_owned_tests" / run_id).resolve()
        if not output.is_relative_to(allowed):
            raise RuntimeError("Output escaped owned scope")
        with Image.open(output) as actual:
            if actual.size != target or actual.format != "PNG":
                raise RuntimeError("Output geometry/format mismatch")
            actual.load()
        results.append({"prompt_id": prompt_id, "input_size": list(source_size), "output_size": list(target), "output": str(output), "status": "success", "compiler_template": compiled["concrete_template_id"]})
    print(json.dumps({"run_id": run_id, "results": results, "uses_diffusion": False,
                      "queue_after": request("/queue"), "notice": "Three owned synthetic images; not real product quality or GPU generation acceptance."}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
