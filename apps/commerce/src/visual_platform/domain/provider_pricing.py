"""Exact, scoped supplier estimates; never treat a price table as a paid invoice."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visual_platform.providers.qwen_image import workspace_origin

Measure = Literal[
    "bfl_credit",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "qima_input_1k",
    "qima_input_2k",
    "qima_output_1k",
    "qima_output_2k",
]
Money = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,8})?$")]
IDENTITIES = {
    "bfl": ("flux-3-image", {"https://api.bfl.ai"}),
    "gemini": (
        "gemini-3.8-flash",
        {"https://generativelanguage.googleapis.com", "https://hubway.cc", "https://z.hubway.cc"},
    ),
}


class UnitRate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    measure: Measure
    amount: Money
    per: Annotated[int, Field(ge=1, le=1_000_000_000)]


class SupplierPriceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1, 2, 3] = 1
    provider: Literal["bfl", "gemini", "aliyun_bailian", "aliyun_bailian_vision"]
    model: str
    endpoint_origin: str
    merchant_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    billing_group: Annotated[str, Field(min_length=1, max_length=160)]
    version: Annotated[str, Field(min_length=1, max_length=160)]
    currency: Literal["USD", "CNY", "BFL_CREDIT", "HUBWAY_BALANCE_UNIT"]
    valid_from: datetime
    valid_until: datetime
    evidence_kind: Literal["offline_fixture", "reviewed_price"]
    evidence_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    complete_meter_coverage: Literal[True]
    rates: Annotated[list[UnitRate], Field(min_length=1, max_length=6)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_type(cls, value):
        if type(value) is not int:
            raise ValueError("integer schema version required")
        return value

    @field_validator("complete_meter_coverage", mode="before")
    @classmethod
    def coverage_type(cls, value):
        if type(value) is not bool:
            raise ValueError("boolean coverage attestation required")
        return value

    @field_validator("billing_group", "version")
    @classmethod
    def plain_text(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("blank or control characters rejected")
        return value

    @model_validator(mode="after")
    def scoped(self):
        if self.provider in ("aliyun_bailian", "aliyun_bailian_vision"):
            workspace_origin(self.endpoint_origin)
            expected = (
                (2, "qwen-image-3.0") if self.provider == "aliyun_bailian" else (3, "qwen3.8-flash")
            )
            if (self.schema_version, self.model) != expected or self.currency != "CNY":
                raise ValueError("unreviewed supplier identity")
        else:
            model, origins = IDENTITIES[self.provider]
            if (
                self.schema_version != 1
                or self.model != model
                or self.endpoint_origin not in origins
            ):
                raise ValueError("unreviewed supplier identity")
        for instant in (self.valid_from, self.valid_until):
            if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
                raise ValueError("explicit UTC required")
        if not timedelta(0) < self.valid_until - self.valid_from <= timedelta(days=31):
            raise ValueError("price review validity must be at most 31 days")
        measures = [rate.measure for rate in self.rates]
        if len(set(measures)) != len(measures):
            raise ValueError("duplicate price meter")
        if (
            (self.provider == "bfl" and measures != ["bfl_credit"])
            or (
                self.provider == "gemini"
                and not set(measures)
                <= {
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "cache_write_tokens",
                    "thinking_tokens",
                }
            )
            or (
                self.provider == "aliyun_bailian_vision"
                and set(measures) != {"input_tokens", "cached_input_tokens", "output_tokens"}
            )
            or (
                self.provider == "aliyun_bailian"
                and set(measures)
                != {"qima_input_1k", "qima_input_2k", "qima_output_1k", "qima_output_2k"}
            )
        ):
            raise ValueError("price meter does not match provider")
        return self


def canonical_price(price: SupplierPriceVersion):
    return json.dumps(
        price.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def estimate_supplier_cost(
    price: SupplierPriceVersion,
    quantities: dict,
    *,
    provider: str,
    model: str,
    endpoint_origin: str,
    merchant_fingerprint: str,
    billing_group: str,
    observed_at: datetime,
):
    """Quantities are explicit disjoint meters; missing/extra meters are unknown, never zero."""
    result = {
        "cost_status": "unknown",
        "amount": None,
        "currency": None,
        "actual_spending_verified": False,
        "calculation_version": "exact-rational-24-decimal-v2",
        "evidence_kind": price.evidence_kind,
        "price_sha256": hashlib.sha256(canonical_price(price)).hexdigest(),
        "price_version": price.version,
    }
    if (provider, model, endpoint_origin, merchant_fingerprint, billing_group) != (
        price.provider,
        price.model,
        price.endpoint_origin,
        price.merchant_fingerprint,
        price.billing_group,
    ):
        return {**result, "reason": "price_identity_mismatch"}
    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() != timedelta(0)
        or not price.valid_from <= observed_at < price.valid_until
    ):
        return {**result, "reason": "price_not_effective"}
    if type(quantities) is not dict or set(quantities) != {rate.measure for rate in price.rates}:
        return {**result, "reason": "incomplete_or_unpriced_usage"}
    if price.provider == "aliyun_bailian":
        if any(type(value) is not int or value < 0 for value in quantities.values()):
            return {**result, "reason": "invalid_usage"}
        incoming = quantities["qima_input_1k"] + quantities["qima_input_2k"]
        outgoing = quantities["qima_output_1k"] + quantities["qima_output_2k"]
        if (
            not 1 <= incoming <= 3
            or outgoing != 1
            or any(
                (quantities["qima_input_" + tier] > 0) != (quantities["qima_output_" + tier] == 1)
                for tier in ("1k", "2k")
            )
        ):
            return {**result, "reason": "invalid_usage"}
    exact = Fraction(0)
    for rate in price.rates:
        quantity = quantities[rate.measure]
        if rate.measure == "bfl_credit":
            if type(quantity) is not str or not re.fullmatch(
                r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,24})?", quantity
            ):
                return {**result, "reason": "invalid_usage"}
        elif type(quantity) is not int or not 0 <= quantity <= 1_000_000_000_000:
            return {**result, "reason": "invalid_usage"}
        exact += Fraction(quantity) * Fraction(rate.amount) / rate.per
    scaled = (exact.numerator * 100000000 + exact.denominator - 1) // exact.denominator
    if scaled >= 100000000000000000000:
        return {**result, "reason": "estimate_overflow"}
    amount = f"{scaled // 100000000}.{scaled % 100000000:08d}"
    return {
        **result,
        "cost_status": "estimated",
        "amount": amount,
        "currency": price.currency,
        "reason": "reviewed_table_estimate_not_invoice",
    }


def compare_supplier_ceiling(estimate: dict, ceiling: str, currency: str):
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,8})?", ceiling):
        raise ValueError("invalid exact ceiling")
    if estimate.get("cost_status") != "estimated" or estimate.get("currency") != currency:
        return "unknown"
    return "over_ceiling" if Decimal(estimate["amount"]) > Decimal(ceiling) else "within_ceiling"


class SupplierMeteringContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str
    model: str
    endpoint_origin: str
    merchant_fingerprint: str
    billing_group: str
    observed_at: datetime
    quantities: dict[str, Any]
