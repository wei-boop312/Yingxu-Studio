"""Explicit offline reference-copy preparation in a resource-limited child."""

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

from visual_platform.imaging.worker import constrain


def main():
    _job = constrain()
    from PIL import Image

    from visual_platform.imaging.normalize import normalize
    from visual_platform.providers.qwen_image import checked_dimensions, reference_metadata

    raw = Path(sys.argv[1]).read_bytes()
    edge = int(sys.argv[3])
    allow_resize = sys.argv[4] == "allow-resize"
    if not 512 <= edge <= 2000:
        raise ValueError("sample_edge_out_of_range")
    canonical = normalize(raw)
    with Image.open(BytesIO(canonical)) as image:
        original_size = image.size
        resized = max(image.size) > edge
        if resized and not allow_resize:
            raise ValueError("explicit_resize_required")
        if resized:
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        checked_dimensions(*image.size)
        clean = Image.frombytes("RGB", image.size, image.tobytes())
        output = BytesIO()
        clean.save(output, "PNG")
        data = output.getvalue()
    meta = reference_metadata(data)
    Path(sys.argv[2]).write_bytes(data)
    print(
        json.dumps(
            {
                **meta,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "original_file_sha256": hashlib.sha256(raw).hexdigest(),
                "canonical_original_sha256": hashlib.sha256(canonical).hexdigest(),
                "source_sha256": hashlib.sha256(data).hexdigest(),
                "resized": resized,
                "resampling": "LANCZOS" if resized else "none",
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print('{"error":"sample_preparation_rejected"}')
        raise SystemExit(2)
    except Exception:
        print('{"error":"sample_preparation_failed"}')
        raise SystemExit(3)
