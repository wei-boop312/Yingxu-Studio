"""CPU-only owned sample. No Comfy queue, downloads, API, or private inputs."""
import hashlib
import json
import sys
import uuid
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow_architect.local_recolor import recolor_rgb


def main():
    comfy = Path("E:/AI/ComfyUI_windows_portable/ComfyUI")
    source_path = comfy / "input/owned_identity_fe02421dc76e4527840db97261ec0d60.png"
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != "d9a728d52b218004efc041210a9ee1223100b757ede383faef8b3116d19ae894":
        raise RuntimeError("Owned source mismatch")
    source = Image.open(source_path).convert("RGB")
    if source.size != (512, 512):
        raise RuntimeError("Geometry mismatch")
    # Hand-drawn owned-sample clothing interiors, not automatic segmentation.
    polygons = [[(0, 462), (125, 416), (151, 453), (190, 511), (0, 511)],
                [(389, 423), (511, 466), (511, 511), (365, 511), (375, 463)]]
    mask = Image.new("L", source.size)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        draw.polygon(polygon, fill=255)
    # Feather inward; never extend edits outside the explicit polygons.
    soft = mask.filter(ImageFilter.GaussianBlur(5))
    soft = Image.frombytes("L", mask.size, bytes(a if b else 0 for a, b in zip(soft.tobytes(), mask.tobytes())))
    result = recolor_rgb(source, soft)
    run_id = "owned_recolor_" + uuid.uuid4().hex
    folder = comfy / "output/workflow_architect_owned_tests" / run_id
    folder.mkdir(exist_ok=False)
    result.save(folder / "recolor.png")
    soft.save(folder / "mask.png")
    actual = Image.open(folder / "recolor.png").convert("RGB")
    original, edited, weights = source.tobytes(), actual.tobytes(), soft.tobytes()
    protected = sum(a == 0 for a in weights)
    protected_changes = sum(original[i*3:i*3+3] != edited[i*3:i*3+3] for i, a in enumerate(weights) if not a)
    changed = sum(original[i*3:i*3+3] != edited[i*3:i*3+3] for i, a in enumerate(weights) if a)
    receipt = dict(run_id=run_id, method="explicit-mask-luminance-color-grade", source=str(source_path),
                   polygons=polygons, output=str(folder / "recolor.png"), mask=str(folder / "mask.png"),
                   protected_pixels=protected, protected_changed_pixels=protected_changes,
                   editable_changed_pixels=changed, generated=False, automatic_segmentation=False,
                   virtual_tryon=False, quality_approved=False)
    archive = ROOT.parent / "research/comfyui-commerce-2026-10-14"
    (archive / (run_id + ".json")).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt))
    if protected_changes or not changed:
        raise RuntimeError("Saved-image pixel acceptance failed")


if __name__ == "__main__":
    main()
