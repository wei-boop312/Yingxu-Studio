"""Review paid-smoke scope without interpreting a configuration as permission to spend."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visual_platform.domain.schemas import Generation, Targets


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration field")
        result[key] = value
    return result


class CommonAuthorizationScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authorization_id: UUID
    user_id: UUID
    product_id: UUID
    source_asset_version_id: UUID
    authorized_by: Annotated[str, Field(min_length=1, max_length=160)]
    authorized_at: datetime
    expires_at: datetime
    source_path: Annotated[str, Field(min_length=1, max_length=1000)]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    quote_input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    generation: Generation
    targets: Targets
    max_generate_calls: Annotated[int, Field(ge=1, le=1)] = 1
    max_detect_calls: Annotated[int, Field(ge=1, le=1)] = 1

    @field_validator("authorized_by", "source_path")
    @classmethod
    def text(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("blank or control characters rejected")
        return value

    @model_validator(mode="after")
    def scope(self):
        for value in (self.authorized_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("explicit UTC timestamps required")
        if not timedelta(0) < self.expires_at - self.authorized_at <= timedelta(hours=24):
            raise ValueError("authorization duration must be at most 24 hours")
        return self


class AuthorizationScope(CommonAuthorizationScope):
    bfl_model: Literal["flux-3-image"] = "flux-3-image"
    gemini_model: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"


class SmokeAuthorization(AuthorizationScope):
    schema_version: Annotated[int, Field(ge=2, le=2)] = 2
    currency: Literal["USD"] = "USD"
    generate_ceiling: str
    detect_ceiling: str
    total_ceiling: str

    @field_validator("generate_ceiling", "detect_ceiling", "total_ceiling")
    @classmethod
    def money(cls, value):
        # Exact decimal strings only: no float rounding, infinity or exponent notation.
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?", value):
            raise ValueError("positive fixed-point decimal string required")
        if Decimal(value) <= 0:
            raise ValueError("cost ceiling must be positive")
        return value

    @model_validator(mode="after")
    def total(self):
        if Decimal(self.total_ceiling) < Decimal(self.generate_ceiling) + Decimal(
            self.detect_ceiling
        ):
            raise ValueError("total ceiling cannot cover both authorized requests")
        return self


BudgetCurrency = Literal["USD", "CNY", "BFL_CREDIT", "HUBWAY_BALANCE_UNIT"]


class UnitCeiling(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    currency: BudgetCurrency
    ceiling: str

    @field_validator("ceiling")
    @classmethod
    def money(cls, value):
        return SmokeAuthorization.money(value)


class StepCeilings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    generate: UnitCeiling
    detect: UnitCeiling


class UnitSmokeAuthorization(AuthorizationScope):
    schema_version: Annotated[int, Field(ge=3, le=3)] = 3
    step_budgets: StepCeilings
    currency_totals: Annotated[list[UnitCeiling], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def totals(self):
        totals: dict[str, Decimal] = {
            row.currency: Decimal(row.ceiling) for row in self.currency_totals
        }
        if len(totals) != len(self.currency_totals):
            raise ValueError("duplicate currency total")
        required: dict[str, Decimal] = {}
        for row in (self.step_budgets.generate, self.step_budgets.detect):
            required[row.currency] = required.get(row.currency, Decimal(0)) + Decimal(row.ceiling)
        if set(totals) != set(required) or any(totals[k] < v for k, v in required.items()):
            raise ValueError("currency totals must cover exactly the authorized units")
        return self


class BailianSmokeAuthorization(CommonAuthorizationScope):
    """Schema 4 offline rehearsal, without legacy BFL model fields."""

    schema_version: Annotated[int, Field(ge=4, le=4)] = 4
    generation_model: Literal["qwen-image-3.0"] = "qwen-image-3.0"
    detection_model: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    step_budgets: StepCeilings
    currency_totals: Annotated[list[UnitCeiling], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def separate_bailian_units(self):
        if (
            self.step_budgets.generate.currency != "CNY"
            or self.step_budgets.detect.currency not in ("USD", "HUBWAY_BALANCE_UNIT")
        ):
            raise ValueError("unreviewed_bailian_budget_units")
        totals = {row.currency: Decimal(row.ceiling) for row in self.currency_totals}
        required = {
            row.currency: Decimal(row.ceiling)
            for row in (self.step_budgets.generate, self.step_budgets.detect)
        }
        if (
            len(totals) != len(self.currency_totals)
            or set(totals) != set(required)
            or any(totals[key] < ceiling for key, ceiling in required.items())
        ):
            raise ValueError("currency totals must cover exactly the authorized units")
        return self


class DualBailianSmokeAuthorization(CommonAuthorizationScope):
    """Schema 5 review only; no PG budget registration or model execution is enabled."""

    schema_version: Annotated[int, Field(ge=5, le=5)] = 5
    generation_model: Literal["qwen-image-3.0"] = "qwen-image-3.0"
    detection_model: Literal["qwen3.8-flash"] = "qwen3.8-flash"
    step_budgets: StepCeilings
    currency_totals: Annotated[list[UnitCeiling], Field(min_length=1, max_length=1)]

    @model_validator(mode="after")
    def shared_cny_total(self):
        rows = (self.step_budgets.generate, self.step_budgets.detect, self.currency_totals[0])
        if any(row.currency != "CNY" for row in rows):
            raise ValueError("dual_bailian_requires_cny_only")
        if Decimal(rows[2].ceiling) < Decimal(rows[0].ceiling) + Decimal(rows[1].ceiling):
            raise ValueError("shared_cny_total_must_cover_both_steps")
        return self


ProviderAuthorization = (
    SmokeAuthorization
    | UnitSmokeAuthorization
    | BailianSmokeAuthorization
    | DualBailianSmokeAuthorization
)


def load_authorization(path: Path, *, now: datetime | None = None):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("regular configuration file of at most 64 KiB required")
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("configuration grew beyond limit")
    data = json.loads(raw, object_pairs_hook=unique_object)
    # JSON mode accepts UUID/ISO timestamps while retaining strict numeric/string types.
    if not isinstance(data, dict):
        raise ValueError("configuration object required")
    version = data.get("schema_version", 2)
    model = (
        DualBailianSmokeAuthorization
        if type(version) is int and version == 5
        else BailianSmokeAuthorization
        if type(version) is int and version == 4
        else UnitSmokeAuthorization
        if type(version) is int and version == 3
        else SmokeAuthorization
    )
    scope = model.model_validate_json(json.dumps(data))
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or not scope.authorized_at <= instant < scope.expires_at:
        raise ValueError("authorization is not currently valid")
    return scope, hashlib.sha256(canonical_authorization(scope)).hexdigest()


def canonical_authorization(scope: ProviderAuthorization) -> bytes:
    return json.dumps(
        scope.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


async def verify_sample(scope: ProviderAuthorization, config_path: Path):
    source = Path(scope.source_path)
    if not source.is_absolute():
        source = config_path.parent / source
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("regular canonical PNG of at most 20 MiB required")
    with source.open("rb") as stream:
        data = stream.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("source grew beyond limit")
    if hashlib.sha256(data).hexdigest() != scope.source_sha256:
        raise ValueError("source bytes differ from reviewed hash")
    # Full pixel/canonical validation belongs to the existing isolated decoder.
    from visual_platform.providers.bfl import BflRequest
    from visual_platform.services.upload import decode

    canonical, decoded = await decode(data, "image/png")
    if hashlib.sha256(canonical).hexdigest() != scope.source_sha256:
        raise ValueError("sample must already be the canonical PNG, not an original upload")
    generation = scope.generation
    if generation.protection_mode == "pixel_exact":
        if (generation.output.width, generation.output.height) != (
            decoded["width"],
            decoded["height"],
        ):
            raise ValueError("pixel_exact output must match canonical source dimensions")
        if any(
            region.source_x + region.width > decoded["width"]
            or region.source_y + region.height > decoded["height"]
            for region in generation.regions
        ):
            raise ValueError("source rectangle exceeds reviewed sample")
    from visual_platform.providers.qwen_image import QwenImageRequest

    request_type = (
        QwenImageRequest
        if isinstance(scope, (BailianSmokeAuthorization, DualBailianSmokeAuthorization))
        else BflRequest
    )
    request_type.from_canonical(generation.model_dump(mode="json"), canonical)
    return {"sha256": scope.source_sha256, "width": decoded["width"], "height": decoded["height"]}
