"""Single offline owned-fixture VTON run, no API, queue mutation, or retry."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid
import argparse
from PIL import Image

from catvton_offline import OfflineCatVTON, HOME, ARCHIVE, torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--standalone", action="store_true", help="Allow confirmed connection-refused Comfy backend; never treat timeout as idle")
    parser.add_argument("--higher-resolution-check", action="store_true", help="Explicit bounded 512x768/50-step comparison; never automatic retry")
    args = parser.parse_args()
    width, height, steps = (512, 768, 50) if args.higher_resolution_check else (384, 576, 24)
    fixture_id = "owned_tryon_inputs_4b7b67e7fdb345acb5512f282f319fde"
    receipt = json.loads((ARCHIVE / (fixture_id + ".json")).read_text(encoding="utf-8"))
    if receipt["status"] != "fixtures-generated-pending-manual-mask-review" or receipt["private_input_used"]:
        raise RuntimeError("Owned fixture unavailable")
    folder = HOME / "fixtures" / fixture_id
    for name in ("person", "garment"):
        if hashlib.sha256((folder / (name + ".png")).read_bytes()).hexdigest() != receipt[name + "_sha256"]:
            raise RuntimeError("Owned fixture integrity mismatch")
    mask_path = folder / "mask.png"
    mask_receipt = json.loads((folder / "mask-review.json").read_text(encoding="utf-8"))
    if mask_receipt["fixture_id"] != fixture_id or hashlib.sha256(mask_path.read_bytes()).hexdigest() != mask_receipt["sha256"]:
        raise RuntimeError("Explicit reviewed mask required")
    person = Image.open(folder / "person.png").convert("RGB")
    garment = Image.open(folder / "garment.png").convert("RGB")
    mask = Image.open(mask_path).convert("L")
    if person.size != (512, 768) or mask.size != person.size or set(mask.tobytes()) != {0, 255}:
        raise RuntimeError("Mask/geometry mismatch")
    # Read-only queue and memory checks, never clear another application's memory.
    import urllib.request
    import urllib.error
    backend_status = "online-empty"
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=10) as response:
            queue = json.load(response)
        if queue["queue_running"] or queue["queue_pending"]:
            raise RuntimeError("Comfy busy; postpone")
    except urllib.error.URLError as error:
        if not args.standalone or not isinstance(error.reason, ConnectionRefusedError):
            raise
        backend_status = "connection-refused-explicit-standalone"
    free = int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], text=True).strip())
    if free < 3800:
        raise RuntimeError("Insufficient free GPU memory; do not stop user applications")
    run_id = "owned_catvton_" + uuid.uuid4().hex
    output = HOME / "outputs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    report = ARCHIVE / (run_id + ".json")
    result = dict(run_id=run_id, fixture_id=fixture_id, status="loading", private_input_used=False,
                  width=width, height=height, steps=steps, seed=628913, safety_checker_enabled=True,
                  quality_approved=False, commercial_permission_verified=False, free_gpu_mib_before=free)
    result["comfy_backend_status"] = backend_status
    def save():
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    save()
    started = time.monotonic()
    try:
        torch.cuda.reset_peak_memory_stats()
        pipeline = OfflineCatVTON(device="cuda", dtype=torch.bfloat16)
        pipeline.vae.enable_slicing()
        result["status"] = "running"
        save()
        images = pipeline(image=person, condition_image=garment, mask=mask, width=width, height=height,
                          num_inference_steps=steps, guidance_scale=2.5,
                          generator=torch.Generator(device="cuda").manual_seed(628913))
        if len(images) != 1 or images[0].size != (width, height):
            raise RuntimeError("Unexpected inference output")
        images[0].save(output / "raw.png")
        # Preserve the raw result. Composite is separately labeled, not identity proof.
        resized = person.resize((width, height), Image.Resampling.LANCZOS)
        edit_mask = mask.resize((width, height), Image.Resampling.NEAREST)
        composite = Image.composite(images[0].convert("RGB"), resized, edit_mask)
        composite.save(output / "protected.png")
        restored = Image.open(output / "protected.png").convert("RGB")
        before, after = resized.tobytes(), restored.tobytes()
        changed = sum(before[i*3:i*3+3] != after[i*3:i*3+3] for i, value in enumerate(edit_mask.tobytes()) if value == 0)
        result.update(status="completed-pending-visual-review", raw=str(output / "raw.png"),
                      protected=str(output / "protected.png"), protected_changed_pixels=changed,
                      output_sha256=hashlib.sha256((output / "raw.png").read_bytes()).hexdigest())
    except Exception as error:
        result.update(status="failed-no-retry", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        result.update(elapsed_seconds=round(time.monotonic()-started, 2), peak_torch_allocated_mib=round(torch.cuda.max_memory_allocated()/1024**2, 1))
        save()
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
