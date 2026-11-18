from __future__ import annotations

import re
from typing import Any


def build_ecommerce_prompts(
    requirement: str,
    *,
    width: int,
    height: int,
) -> dict[str, str]:
    """Convert common Chinese ecommerce requirements into SDXL-friendly prompts."""
    text = requirement.strip()
    background: list[str] = [
        "premium ecommerce advertising background",
        "empty commercial set without any product",
        "clean minimal abstract set",
        "seamless empty stage with no visible photography equipment",
    ]

    if re.search(r"奶油白|奶白|米白|暖白|象牙白", text):
        background.append("warm ivory and creamy white seamless cyclorama")
    elif re.search(r"纯白|白色|白底", text):
        background.append("clean white seamless cyclorama")
    elif re.search(r"黑色|深色|暗色|黑金", text):
        background.append("dark charcoal luxury seamless background")
    elif re.search(r"蓝色|科技蓝|冰蓝", text):
        background.append("clean cool blue technology background")
    elif re.search(r"粉色|浅粉|少女", text):
        background.append("soft pastel pink seamless background")
    else:
        background.append("neutral warm light gray seamless background")

    if re.search(r"柔和.{0,4}(?:侧光|光线|灯光)|柔光", text):
        background.append("soft diffused side lighting")
    else:
        background.append("soft diffused commercial lighting")
    if re.search(r"自然.{0,4}(?:投影|阴影)|接触阴影", text):
        background.append("subtle realistic contact shadow area")
    if re.search(r"展台|台面|底座|圆台", text):
        background.append("simple matte product pedestal")
    if re.search(r"高级|高端|奢华|质感", text):
        background.append("refined premium art direction")
    if re.search(r"科技|未来|数码", text):
        background.append("subtle modern technology accents")
    if re.search(r"家居|客厅|卧室|生活方式", text):
        background.append("tasteful modern lifestyle interior cues")
    if re.search(r"预留|留白|标题|卖点|文案", text):
        background.append("generous clean negative space for later typography")

    background.append(
        "vertical ecommerce hero layout"
        if height > width
        else "horizontal ecommerce hero layout"
    )
    background.append("photorealistic, high detail, professional advertising")

    return {
        "background_positive_prompt": ", ".join(background),
        "background_negative_prompt": (
            "product, speaker, watch, object, people, hands, text, letters, words, "
            "logo, watermark, calligraphy, Chinese painting, ink landscape, mountains, "
            "photography equipment, backdrop stand, light stand, tripod, studio rig, "
            "rolled paper, busy composition, clutter, illustration, low quality, blurry"
        ),
        "product_positive_prompt": (
            "professional ecommerce product photo, preserve the exact product shape, "
            "proportions, material, color, controls, ports and logo placement, "
            "clean accurate edges, realistic surface detail, studio quality"
        ),
        "product_negative_prompt": (
            "changed product design, deformed shape, wrong proportions, altered logo, "
            "misspelled text, extra parts, missing parts, duplicate controls, wrong ports, "
            "new background, scenery, people, hands, blur, artifacts"
        ),
    }


def fit_product_layout(
    source_width: int,
    source_height: int,
    canvas_width: int,
    canvas_height: int,
) -> dict[str, int]:
    """Fit a product into a centered hero area while preserving its aspect ratio."""
    if min(source_width, source_height, canvas_width, canvas_height) <= 0:
        raise ValueError("image dimensions must be positive")

    max_width = max(64, int(canvas_width * 0.78))
    max_height = max(64, int(canvas_height * 0.52))
    scale = min(max_width / source_width, max_height / source_height)
    product_width = max(64, int(source_width * scale))
    product_height = max(64, int(source_height * scale))
    product_width = min(canvas_width, max(8, product_width // 8 * 8))
    product_height = min(canvas_height, max(8, product_height // 8 * 8))
    product_y = max(0, (canvas_height - product_height) // 2)
    if canvas_height > canvas_width:
        product_y = min(
            canvas_height - product_height,
            max(0, int(canvas_height * 0.78) - product_height),
        )
    return {
        "product_width": product_width,
        "product_height": product_height,
        "product_x": max(0, (canvas_width - product_width) // 2),
        "product_y": product_y,
    }


def source_image_dimensions(filename: Any) -> tuple[int, int] | None:
    """Read an input image size inside ComfyUI without making it a hard dependency."""
    if not isinstance(filename, str) or not filename.strip():
        return None
    try:
        import folder_paths  # type: ignore
        from PIL import Image

        path = folder_paths.get_annotated_filepath(filename)
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None
