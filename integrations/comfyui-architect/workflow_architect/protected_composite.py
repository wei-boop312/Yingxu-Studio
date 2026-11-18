"""Same-size, exact outside-mask RGB preservation. No generation or segmentation."""


def composite_exact(original, candidate, mask):
    from PIL import Image
    if original.mode != "RGB" or candidate.mode != "RGB" or mask.mode != "L":
        raise ValueError("Explicit RGB images and L mask required")
    if original.size != candidate.size or original.size != mask.size:
        raise ValueError("Dimensions must match; implicit resize forbidden")
    if original.width * original.height > 16_000_000:
        raise ValueError("Pixel limit exceeded")
    if set(mask.tobytes()) != {0, 255}:
        raise ValueError("Binary mask with editable and protected regions required")
    result = Image.composite(candidate, original, mask)
    before, after = original.tobytes(), result.tobytes()
    if any(before[i*3:i*3+3] != after[i*3:i*3+3] for i, value in enumerate(mask.tobytes()) if value == 0):
        raise RuntimeError("Outside-mask pixels changed")
    return result
