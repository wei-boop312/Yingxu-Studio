import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from visual_platform.domain.image_limits import (
    MAX_CANONICAL_PIXELS,
    MAX_OUTPUT_EDGE,
    MAX_PROTECTED_PIXELS,
    MIN_OUTPUT_EDGE,
)
from visual_platform.domain.platforms import Platform

Pixel = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Span = Annotated[int, Field(strict=True, ge=1, le=MAX_OUTPUT_EDGE)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Region(StrictModel):
    source_x: Pixel
    source_y: Pixel
    width: Span
    height: Span
    target_x: Pixel
    target_y: Pixel


class Output(StrictModel):
    width: Annotated[int, Field(strict=True, ge=MIN_OUTPUT_EDGE, le=MAX_OUTPUT_EDGE)]
    height: Annotated[int, Field(strict=True, ge=MIN_OUTPUT_EDGE, le=MAX_OUTPUT_EDGE)]
    format: Literal["png"]


class Generation(StrictModel):
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"protection_mode": {"const": "pixel_exact"}}},
                    "then": {"properties": {"regions": {"minItems": 1}}},
                },
                {
                    "if": {"properties": {"protection_mode": {"const": "none"}}},
                    "then": {"properties": {"regions": {"maxItems": 0}}},
                },
            ]
        }
    )
    type: Literal["white_background", "scene", "lifestyle"]
    prompt: Annotated[str, Field(strict=True, min_length=1, max_length=4000)]
    output: Output
    protection_mode: Literal["pixel_exact", "none"]
    regions: Annotated[list[Region], Field(max_length=20)]

    @model_validator(mode="after")
    def geometry(self):
        if self.output.width * self.output.height > MAX_CANONICAL_PIXELS:
            raise ValueError("output exceeds canonical 25MP processing budget")
        if self.protection_mode == "pixel_exact" and not self.regions:
            raise ValueError("pixel_exact requires at least one protection region")
        if self.protection_mode == "none" and self.regions:
            raise ValueError("regions require pixel_exact")
        if (
            self.protection_mode == "pixel_exact"
            and self.output.width * self.output.height > MAX_PROTECTED_PIXELS
        ):
            raise ValueError("pixel_exact limited to 4MP")
        boxes: list[tuple[int, int, int, int]] = []
        for r in self.regions:
            b = (r.target_x, r.target_y, r.target_x + r.width, r.target_y + r.height)
            if b[2] > self.output.width or b[3] > self.output.height:
                raise ValueError("target bounds")
            if any(
                max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])
                for a in boxes
            ):
                raise ValueError("overlap")
            boxes.append(b)
        return self


class Target(StrictModel):
    platform: Platform
    marketplace: Annotated[str, Field(strict=True, min_length=1, max_length=32)]
    category: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    image_type: Annotated[str, Field(strict=True, min_length=1, max_length=32)]


def unique_targets(value: list[Target]) -> list[Target]:
    identities = {(t.platform, t.marketplace, t.category, t.image_type) for t in value}
    if len(identities) != len(value):
        raise ValueError("duplicate targets")
    return value


Targets = Annotated[
    list[Target],
    Field(min_length=1, max_length=3, json_schema_extra={"uniqueItems": True}),
    AfterValidator(unique_targets),
]


class QuoteRequest(StrictModel):
    product_id: UUID
    appearance_profile_id: UUID | None = None
    source_asset_version_id: UUID
    generation: Generation
    targets: Targets


class TaskRequest(QuoteRequest):
    quote_id: UUID


def canonical_hash(model: BaseModel):
    data = model.model_dump(mode="json", exclude_none=True)
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
