from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from visual_platform.domain.image_limits import MAX_PROTECTED_PIXELS


@dataclass(frozen=True)
class Region:
    source_x: int
    source_y: int
    width: int
    height: int
    target_x: int
    target_y: int

    def __post_init__(self):
        if any(type(v) is not int for v in vars(self).values()):
            raise ValueError("integer pixels required")
        if (
            min(self.source_x, self.source_y, self.target_x, self.target_y) < 0
            or min(self.width, self.height) <= 0
        ):
            raise ValueError("invalid rectangle")


def protect(source: Image.Image, generated: Image.Image, regions: list[Region]) -> bytes:
    if not regions:
        raise ValueError("exact protection requires at least one region")
    if source.mode != "RGB" or generated.mode != "RGB":
        raise ValueError("canonical RGB required")
    if source.size != generated.size:
        raise ValueError("exact mode requires equal dimensions")
    if source.width * source.height > MAX_PROTECTED_PIXELS or len(regions) > 20:
        raise ValueError("limits exceeded")
    boxes: list[tuple[int, int, int, int]] = []
    for r in regions:
        if r.source_x + r.width > source.width or r.source_y + r.height > source.height:
            raise ValueError("source bounds")
        b = (r.target_x, r.target_y, r.target_x + r.width, r.target_y + r.height)
        if b[2] > generated.width or b[3] > generated.height:
            raise ValueError("target bounds")
        if any(
            max(b[0], a[0]) < min(b[2], a[2]) and max(b[1], a[1]) < min(b[3], a[3]) for a in boxes
        ):
            raise ValueError("overlapping targets")
        boxes.append(b)
    output = generated.copy()
    patches = []
    for r, b in zip(regions, boxes):
        patch = source.crop((r.source_x, r.source_y, r.source_x + r.width, r.source_y + r.height))
        output.paste(patch, (r.target_x, r.target_y))
        patches.append(patch)
    stream = BytesIO()
    output.save(stream, format="PNG")
    with Image.open(BytesIO(stream.getvalue())) as decoded:
        for patch, b in zip(patches, boxes):
            if decoded.crop(b).tobytes() != patch.tobytes():
                raise ValueError("pixel verification failed")
    return stream.getvalue()
