import warnings
from io import BytesIO

from PIL import Image, ImageCms, ImageOps

from visual_platform.domain.image_limits import MAX_CANONICAL_PIXELS, MAX_IMAGE_EDGE

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = MAX_CANONICAL_PIXELS


def normalize(data: bytes) -> bytes:
    """Run in a resource-limited child process in upload completion, never the API loop."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("file size")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as raw:
            if raw.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("unsupported format")
            if getattr(raw, "n_frames", 1) != 1:
                raise ValueError("animation forbidden")
            if raw.width > MAX_IMAGE_EDGE or raw.height > MAX_IMAGE_EDGE:
                raise ValueError("image edge limit")
            if raw.width * raw.height > MAX_PIXELS:
                raise ValueError("pixel limit")
            raw.load()
            image = ImageOps.exif_transpose(raw)
            if image is None:
                raise ValueError("image_orientation_failed")
            if image.mode == "P" and "transparency" in image.info:
                image = image.convert("RGBA")
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            if image.info.get("icc_profile"):
                profile = ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"]))
                color = image.convert("RGB") if image.mode in ("RGBA", "P", "LA") else image
                converted = ImageCms.profileToProfile(
                    color, profile, ImageCms.createProfile("sRGB"), outputMode="RGB"
                )
                if converted is None:
                    raise ValueError("color_conversion_failed")
                image = converted
            else:
                image = image.convert("RGB")
            if alpha is not None:
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=alpha)
                image = background
            # Rebuild pixels so EXIF, comments and location metadata cannot leak.
            clean = Image.frombytes("RGB", image.size, image.tobytes())
            out = BytesIO()
            clean.save(out, "PNG")
            return out.getvalue()
