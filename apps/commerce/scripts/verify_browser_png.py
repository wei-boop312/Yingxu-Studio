"""Independent verification of a real browser download against the canonical RGB fixture."""

import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
meta = json.loads((ROOT / "work/browser-input.json").read_text(encoding="utf-8"))
final_data = (ROOT / "work/browser-final.png").read_bytes()
source: Image.Image
final: Image.Image
with (
    Image.open(ROOT / "apps/web/e2e/fixtures/product.png") as source,
    Image.open(ROOT / "work/browser-final.png") as final,
):
    source = source.convert("RGB")
    final = final.convert("RGB")
    checks = []
    for r in meta["regions"]:
        a = source.crop(
            (r["source_x"], r["source_y"], r["source_x"] + r["width"], r["source_y"] + r["height"])
        ).tobytes()
        b = final.crop(
            (r["target_x"], r["target_y"], r["target_x"] + r["width"], r["target_y"] + r["height"])
        ).tobytes()
        checks.append(
            {"region": r, "rgb_equal": a == b, "region_sha256": hashlib.sha256(a).hexdigest()}
        )
report = {
    "task_id": meta["task_id"],
    "browser_download_sha256": hashlib.sha256(final_data).hexdigest(),
    "matches_database_final_sha256": hashlib.sha256(final_data).hexdigest() == meta["final_sha256"],
    "checks": checks,
}
if (
    not report["matches_database_final_sha256"]
    or not checks
    or not all(x["rgb_equal"] for x in checks)
):
    raise SystemExit("Browser PNG hash or protected pixels do not match")
(ROOT / "docs/evidence/browser-pixel-verification.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
print("Browser PNG hash and protected RGB pixels verified")
