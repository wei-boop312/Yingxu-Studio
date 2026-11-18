"""Bounded appearance data shared by product writes and frozen task execution."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from visual_platform.domain.schemas import StrictModel

AppearanceLabel = Annotated[str, Field(strict=True, max_length=255)]


class AppearanceConfig(StrictModel):
    source_asset_version_id: Annotated[UUID, Field(strict=False)] | None = None
    understanding_id: Annotated[UUID, Field(strict=False)] | None = None
    description: str = Field(default="", strict=True, max_length=4000)
    colors: list[AppearanceLabel] = Field(default_factory=list, max_length=20)
    materials: list[AppearanceLabel] = Field(default_factory=list, max_length=20)

    @field_validator("description", "colors", "materials")
    @classmethod
    def text(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(ord(c) < 32 and c not in "\n\r\t" for item in values for c in item):
            raise ValueError("appearance contains unsupported control characters")
        return value
