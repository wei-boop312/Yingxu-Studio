"""Small real PNGs for Qwen protocol admission, without supplier calls."""

from io import BytesIO

from PIL import Image


def canonical_png(width=32, height=32):
    stream = BytesIO()
    Image.new("RGB", (width, height), (20, 30, 40)).save(stream, "PNG")
    return stream.getvalue()
