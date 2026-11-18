import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

from visual_platform.imaging.worker import constrain


def main():
    _job = constrain()  # Keep the resource-limit handle in process scope.
    from PIL import Image

    from visual_platform.imaging.paste import Region, protect

    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    with Image.open(config["source"]) as im:
        source = im.copy()
    with Image.open(config["target"]) as im:
        target = im.copy()
    regions = [Region(**r) for r in config["regions"]]
    if config["mode"] == "pixel_exact" and not regions:
        raise ValueError("exact protection requires at least one region")
    if config["action"] == "protect":
        if config["mode"] == "pixel_exact":
            result = protect(source, target, regions)
        else:
            if regions:
                raise ValueError("no protection mode cannot contain regions")
            out = BytesIO()
            target.save(out, "PNG")
            result = out.getvalue()
        Path(config["output"]).write_bytes(result)
        with Image.open(BytesIO(result)) as im:
            target = im.copy()
    proof = []
    if config["mode"] == "pixel_exact":
        if source.mode != "RGB" or target.mode != "RGB" or source.size != target.size:
            raise ValueError("canonical dimensions differ")
        for r in regions:
            a = source.crop(
                (r.source_x, r.source_y, r.source_x + r.width, r.source_y + r.height)
            ).tobytes()
            b = target.crop(
                (r.target_x, r.target_y, r.target_x + r.width, r.target_y + r.height)
            ).tobytes()
            if a != b:
                raise ValueError("protected pixels differ")
            proof.append(
                {
                    "source": [r.source_x, r.source_y, r.width, r.height],
                    "target": [r.target_x, r.target_y, r.width, r.height],
                    "rgb_sha256": hashlib.sha256(a).hexdigest(),
                }
            )
    print(
        json.dumps(
            {
                "width": target.width,
                "height": target.height,
                "pixel_exact_passed": config["mode"] == "pixel_exact",
                "regions": proof,
                "protection_mode": config["mode"],
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print('{"error":"invalid_image"}')
        raise SystemExit(2)
    except Exception:
        print('{"error":"image_process_failed"}')
        raise SystemExit(3)
