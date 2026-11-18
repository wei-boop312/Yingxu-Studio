from typing import Annotated, Literal

from pydantic import Field, model_validator

from visual_platform.domain.schemas import Pixel, Positive, StrictModel

Confidence = Annotated[float, Field(ge=0, le=1, strict=True)]


class Mark(StrictModel):
    text: str = Field(strict=True, max_length=4000)
    bbox: tuple[Pixel, Pixel, Pixel, Pixel]
    origin: Literal["on_product", "added_overlay", "unknown"]
    confidence: Confidence


class Background(StrictModel):
    classification: Literal["white", "non_white", "unknown"]
    confidence: Confidence


class Extent(StrictModel):
    bbox: tuple[Pixel, Pixel, Pixel, Pixel]
    confidence: Confidence


class Facts(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    analysis_complete: bool = Field(strict=True)
    image_width: Positive
    image_height: Positive
    text_items: list[Mark] = Field(max_length=300)
    logo_items: list[Mark] = Field(max_length=300)
    watermark_items: list[Mark] = Field(max_length=300)
    background: Background
    product_extent: Extent | None
    uncertainties: list[str] = Field(max_length=100)

    @model_validator(mode="after")
    def boxes(self):
        marks: list[Mark | Extent] = [
            *self.text_items,
            *self.logo_items,
            *self.watermark_items,
            *([self.product_extent] if self.product_extent else []),
        ]
        for mark in marks:
            x1, y1, x2, y2 = mark.bbox
            if not (x1 < x2 <= self.image_width and y1 < y2 <= self.image_height):
                raise ValueError("fact bbox outside final image")
        return self
