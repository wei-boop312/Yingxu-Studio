"""Generate visibly synthetic fixtures; never evidence of real product quality."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "apps/commerce/tests/fixtures/studio"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    files = []
    for sku, color in (("A", "#BB3333"), ("B", "#3355BB")):
        image = Image.new("RGB", (256, 256), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 70, 215, 190), fill=color)
        draw.text((15, 15), "SYNTHETIC TEST SKU " + sku, fill="black")
        draw.text((15, 220), "NOT A REAL PRODUCT", fill="black")
        path = DEST / ("synthetic-sku-" + sku.lower() + ".png")
        image.save(path, "PNG")
        files.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "sku": sku,
                "role": "product_front",
                "synthetic": True,
            }
        )
    fixture = {
        "schema_version": 1,
        "synthetic": True,
        "product": "Synthetic Studio Fixture",
        "skus": [
            {
                "code": "A",
                "color": "red",
                "confirmed_faces": ["FRONT"],
                "unknown_faces": ["BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"],
                "dimensions": None,
                "package_list": None,
                "package_visual_reference": None,
            },
            {
                "code": "B",
                "color": "blue",
                "confirmed_faces": ["FRONT"],
                "unknown_faces": ["BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"],
                "dimensions": None,
                "package_list": None,
                "package_visual_reference": None,
            },
        ],
        "expected": {
            "slots": 10,
            "initial_missing_slots": ["09", "10"],
            "allowed_partial_delivery_count": 8,
            "cross_sku_inheritance": False,
        },
        "files": files,
        "limitation": "Only rule and integration fixtures. No real AI understanding, product truth or commercial quality proven.",
    }
    path = DEST / "manifest.json"
    path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Two synthetic SKU fixtures and immutable hashes prepared")


if __name__ == "__main__":
    main()
