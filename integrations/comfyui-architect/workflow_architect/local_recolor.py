"""Explicit-mask, same-composition color grading, not generative try-on.

Pillow is imported only on use. Outside-mask RGB bytes are preserved exactly.
The mask is supplied by the caller; this module never infers garment boundaries.
"""


def recolor_rgb(source, mask, color=(45, 100, 180)):
    from PIL import Image

    if source.mode != "RGB" or mask.mode != "L" or source.size != mask.size:
        raise ValueError("Expected same-size RGB source and L mask")
    if source.width > 8192 or source.height > 8192 or source.width * source.height > 16_000_000:
        raise ValueError("Image exceeds local pixel limit")
    if not isinstance(color, (tuple, list)) or len(color) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in color):
        raise ValueError("Expected three integer RGB channels")
    pixels = source.tobytes()
    alpha = mask.tobytes()
    if not any(alpha) or not any(a == 0 for a in alpha):
        raise ValueError("Mask must contain editable and fully protected pixels")
    output = bytearray(pixels)
    for index, amount in enumerate(alpha):
        if not amount:
            continue
        offset = index * 3
        red, green, blue = pixels[offset:offset + 3]
        # Original luminance drives every target channel: folds are not synthesized.
        light = (54 * red + 183 * green + 19 * blue) / 256
        for channel, target in enumerate(color):
            graded = min(255, round(light * target / 128))
            output[offset + channel] = (pixels[offset + channel] * (255 - amount) + graded * amount + 127) // 255
    return Image.frombytes("RGB", source.size, bytes(output))
