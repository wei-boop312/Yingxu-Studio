from __future__ import annotations

import math
import re

from .errors import InputError
from .people import people_request
from .schemas import (
    ExecutionSettings,
    InputRequirements,
    OutputRequirements,
    ParseResult,
    PreservationConstraints,
    PromptRequirements,
    TaskSpec,
)
from .settings import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    LOW_VRAM_MAX_PIXELS,
    MAX_DIMENSION,
    MAX_PIXELS,
    MAX_TEXT_LENGTH,
    MIN_DIMENSION,
)

TASK_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ecommerce_detail",
        (
            r"电商.{0,8}(?:详情页|主视觉|首屏|卖点图)",
            r"(?:详情页|主视觉|首屏).{0,8}(?:产品图|商品图|电商)",
            r"参考.{0,8}(?:产品图|商品图).{0,20}(?:详情页|主视觉|首屏|卖点)",
        ),
    ),
    ("virtual_tryon", (r"换装", r"模特换衣", r"虚拟试衣", r"换上.{0,12}(?:上衣|衣服|服装)")),
    ("inpaint", (r"局部重绘", r"局部修复", r"涂抹重绘", r"蒙版重绘", r"去除.{0,10}(?:物体|人物|文字|水印)")),
    ("image_upscale", (r"超分", r"超分辨率", r"高清放大", r"无损放大", r"模型放大")),
    ("image_resize", (r"调整.{0,6}(?:尺寸|分辨率)", r"缩放图片", r"修改图片尺寸", r"图片改成")),
    (
        "image_to_image",
        (
            r"图生图",
            r"以图生图",
            r"基于.{0,8}(?:图片|图像).{0,8}生成",
            r"参考.{0,8}(?:这张图|图片|图像|产品图|商品图|照片).{0,20}(?:生成|制作|创建)",
        ),
    ),
    ("text_to_image", (r"文生图", r"生成一张", r"画一张", r"创建一张", r"生成图片", r"生成图像")),
)

IDENTITY_PATTERNS = (r"脸(?:部)?.{0,6}(?:不变|不要变化)", r"保持脸部", r"五官不变", r"保持人物特征", r"保持模特身份")
HAIR_PATTERNS = (r"发型.{0,6}(?:不变|不要变化)", r"保持发型", r"头发.{0,6}(?:不变|不要变化)", r"保持头发")
POSE_PATTERNS = (r"姿势不变", r"动作不变", r"保持原姿势", r"保持姿势")
BACKGROUND_PATTERNS = (r"背景不变", r"保留背景", r"不要改变背景", r"保持背景")

RESOLUTION_PATTERNS = (
    re.compile(r"(?<!\d)(\d{1,5})\s*[xX×＊*]\s*(\d{1,5})(?!\d)"),
    re.compile(r"宽(?:度)?\s*(\d{1,5})\s*[,，、\s]*高(?:度)?\s*(\d{1,5})"),
)


class RequirementParser:
    """Deterministic offline parser for common image workflow intents."""

    def parse(
        self,
        text: str,
        *,
        width_override: int | None = None,
        height_override: int | None = None,
        strict_identity: bool | None = None,
        preserve_background: bool | None = None,
        profile: str = "stable",
    ) -> ParseResult:
        if not isinstance(text, str):
            raise InputError("需求必须是文本")
        text = text.strip()
        if not text:
            raise InputError("需求文本不能为空")
        if len(text) > MAX_TEXT_LENGTH:
            raise InputError(f"需求文本不能超过 {MAX_TEXT_LENGTH} 个字符")
        if profile not in {"stable", "low_vram"}:
            raise InputError("运行模式只支持 stable 或 low_vram")
        if re.search(r"视频|动图|批量视频", text):
            return ParseResult(False, None, 0.0, ("2.0 暂不支持视频工作流。",))

        person = people_request(text)
        if person and person["kind"] in {"face_swap", "identity_reference"}:
            return ParseResult(False, None, 0.0, tuple(person["limitations"]))
        if person and person["batch_requested"]:
            return ParseResult(False, None, 0.0, tuple(person["limitations"]))

        task = _detect_task(text)
        if task is None and person and person["kind"] == "portrait_generation":
            task = "text_to_image"
        if task is None and person and person["kind"] == "virtual_tryon":
            task = "virtual_tryon"
        if task is None:
            return ParseResult(
                False,
                None,
                0.0,
                ("未识别任务；可描述电商详情页、文生图、图生图、局部重绘、图片缩放、模型超分或单人上衣换装。",),
            )

        warnings: list[str] = []
        if person:
            warnings.extend(person["limitations"])
        width, height, found_resolution = self._resolution(text)
        if width_override is not None or height_override is not None:
            if width_override is None or height_override is None:
                raise InputError("手动尺寸必须同时提供宽度和高度")
            width, height = int(width_override), int(height_override)
            _validate_resolution(width, height)
            found_resolution = True
        if not found_resolution:
            warnings.append(f"未识别到分辨率，已使用默认值 {width}×{height}。")
        if profile == "low_vram" and width * height > LOW_VRAM_MAX_PIXELS:
            original = (width, height)
            width, height = _fit_pixels(width, height, LOW_VRAM_MAX_PIXELS)
            warnings.append(f"低显存模式已将 {original[0]}×{original[1]} 调整为 {width}×{height}。")

        identity = _matches_any(text, IDENTITY_PATTERNS)
        hair = _matches_any(text, HAIR_PATTERNS)
        pose = _matches_any(text, POSE_PATTERNS)
        background = _matches_any(text, BACKGROUND_PATTERNS)
        if strict_identity is not None:
            identity = strict_identity
        if preserve_background is not None:
            background = preserve_background

        inputs = InputRequirements(
            person_image=task == "virtual_tryon",
            garment_image=task == "virtual_tryon",
            source_image=task
            in {
                "image_to_image",
                "inpaint",
                "image_resize",
                "image_upscale",
                "ecommerce_detail",
            },
            mask_image=task == "inpaint",
        )
        denoise = (
            0.15
            if task == "ecommerce_detail"
            else 0.65
            if task == "image_to_image"
            else 0.8
            if task == "inpaint"
            else 1.0
        )
        negative = (
            "低质量，模糊，商品变形，结构改变，多余部件，错误接口，错位Logo，"
            "乱码，文字，数字，价格，二维码，水印"
            if task == "ecommerce_detail"
            else "低质量，模糊，畸形，多余肢体，文字，水印"
        )
        prompt = PromptRequirements(
            positive=text
            if task in {"text_to_image", "image_to_image", "inpaint", "ecommerce_detail"}
            else "",
            negative=negative,
            steps=16 if profile == "low_vram" else 24,
            cfg=6.5 if profile == "low_vram" else 7.0,
            denoise=denoise,
        )
        if task == "ecommerce_detail":
            warnings.append(
                "电商双分支模板会自动调用本地背景移除模型生成商品前景蒙版；"
                "节点或模型缺失时仍会创建草稿，并提供 Manager 修复建议。"
            )
        constraints = PreservationConstraints(
            identity_preservation="strict" if identity else "normal",
            hair_preservation=hair,
            pose_preservation="high" if pose else "normal",
            background_preservation=background,
        )
        spec = TaskSpec(
            task=task,
            garment_category="upper_body" if task == "virtual_tryon" else "not_applicable",
            inputs=inputs,
            prompt=prompt,
            constraints=constraints,
            output=OutputRequirements(width=width, height=height),
            execution=ExecutionSettings(profile=profile, low_vram=profile == "low_vram"),
        )
        score = 0.72 + (0.10 if found_resolution else 0) + (0.06 if task == "virtual_tryon" and identity else 0)
        return ParseResult(True, spec, round(min(score, 0.99), 2), tuple(warnings))

    @staticmethod
    def _resolution(text: str) -> tuple[int, int, bool]:
        for pattern in RESOLUTION_PATTERNS:
            match = pattern.search(text)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
                _validate_resolution(width, height)
                return width, height, True
        return DEFAULT_WIDTH, DEFAULT_HEIGHT, False


def _detect_task(text: str) -> str | None:
    for task, patterns in TASK_PATTERNS:
        if _matches_any(text, patterns):
            return task
    return None


def _validate_resolution(width: int, height: int) -> None:
    if not (
        MIN_DIMENSION <= width <= MAX_DIMENSION
        and MIN_DIMENSION <= height <= MAX_DIMENSION
        and width * height <= MAX_PIXELS
    ):
        raise InputError(
            f"分辨率 {width}×{height} 超出安全范围 "
            f"({MIN_DIMENSION}–{MAX_DIMENSION}，最多 {MAX_PIXELS} 像素)"
        )


def _fit_pixels(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    scale = math.sqrt(max_pixels / (width * height))
    fitted_width = max(MIN_DIMENSION, int(width * scale) // 8 * 8)
    fitted_height = max(MIN_DIMENSION, int(height * scale) // 8 * 8)
    return fitted_width, fitted_height


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
