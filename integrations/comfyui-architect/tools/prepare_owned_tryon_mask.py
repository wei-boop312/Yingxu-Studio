"""Explicit coordinates reviewed against this one owned synthetic person image."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw


def main():
    fixture_id = "owned_tryon_inputs_4b7b67e7fdb345acb5512f282f319fde"
    folder = Path("E:/AI/CatVTON-Research/fixtures") / fixture_id
    if hashlib.sha256((folder / "person.png").read_bytes()).hexdigest() != "8ff19030d9bad72ed69e84cb3ca87911c50ca302fe839443b91abc1972789f32":
        raise RuntimeError("Reviewed source changed")
    polygon = [(216, 148), (227, 173), (249, 183), (270, 176), (285, 149),
               (348, 173), (370, 261), (329, 276), (333, 394), (320, 423),
               (201, 427), (176, 408), (182, 277), (134, 263), (160, 180)]
    mask = Image.new("L", (512, 768))
    ImageDraw.Draw(mask).polygon(polygon, fill=255)
    with (folder / "mask.png").open("xb") as handle:
        mask.save(handle, format="PNG")
    review = dict(fixture_id=fixture_id, polygon=polygon, white="editable", black="protected", automatic_segmentation=False,
                  sha256=hashlib.sha256((folder / "mask.png").read_bytes()).hexdigest())
    with (folder / "mask-review.json").open("x", encoding="utf-8") as handle:
        json.dump(review, handle, indent=2)
    print(json.dumps(review))


if __name__ == "__main__":
    main()
