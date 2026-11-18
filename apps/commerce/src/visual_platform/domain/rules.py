from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from visual_platform.domain.platforms import OFFICIAL_RULE_HOSTS, Platform
from visual_platform.domain.schemas import Positive, StrictModel

Ratio = Annotated[float, Field(ge=0, le=1, strict=True)]


class RequiredAspectRatio(StrictModel):
    width: Annotated[int, Field(strict=True, ge=1, le=10000)]
    height: Annotated[int, Field(strict=True, ge=1, le=10000)]


class RuleConfig(StrictModel):
    min_width: Positive | None = None
    min_height: Positive | None = None
    max_bytes: Positive | None = None
    max_width: Annotated[int, Field(strict=True, ge=1, le=1000000)] | None = None
    max_height: Annotated[int, Field(strict=True, ge=1, le=1000000)] | None = None
    min_longest_edge: Annotated[int, Field(strict=True, ge=1, le=1000000)] | None = None
    max_longest_edge: Annotated[int, Field(strict=True, ge=1, le=1000000)] | None = None
    required_aspect_ratio: RequiredAspectRatio | None = None
    require_white_background: bool = Field(default=False, strict=True)
    forbid_added_text: bool = Field(default=False, strict=True)
    forbid_added_watermarks: bool = Field(default=False, strict=True)
    allow_on_product_marks: bool = Field(default=True, strict=True)
    min_product_long_edge_ratio: Ratio | None = None
    minimum_confidence: Ratio = 0.9
    require_identity_review: bool = Field(default=True, strict=True)
    complete_rule_set: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def dimension_bounds(self):
        for minimum, maximum in (
            (self.min_width, self.max_width),
            (self.min_height, self.max_height),
            (self.min_longest_edge, self.max_longest_edge),
        ):
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError("rule minimum exceeds maximum")
        return self


class RuleDocument(StrictModel):
    platform: Platform
    marketplace: str = Field(strict=True, min_length=1, max_length=40)
    category: str = Field(strict=True, min_length=1, max_length=255)
    image_type: str = Field(strict=True, min_length=1, max_length=40)
    version: str = Field(strict=True, min_length=1, max_length=100)
    evidence_status: Literal["unverified", "verified"]
    source_urls: list[str] = Field(default_factory=list, max_length=20)
    retrieved_at: datetime | None = None
    evidence_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    config: RuleConfig

    @model_validator(mode="after")
    def evidence(self):
        hosts = OFFICIAL_RULE_HOSTS[self.platform]
        for value in self.source_urls:
            u = urlsplit(value)
            if (
                u.scheme != "https"
                or not u.hostname
                or u.username
                or u.password
                or len(value) > 2048
            ):
                raise ValueError("HTTPS evidence URL required")
            if self.evidence_status == "verified" and not any(
                u.hostname == h or u.hostname.endswith("." + h) for h in hosts
            ):
                raise ValueError("verified evidence requires official platform source")
        if self.retrieved_at and self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at requires timezone")
        if self.evidence_status == "verified" and (
            not self.source_urls or not self.retrieved_at or not self.evidence_sha256
        ):
            raise ValueError("verified rules require archived evidence and retrieval date")
        return self
