"""Advisory estimate from original visual wire evidence; never budget or invoice authority."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from visual_platform.domain.provider_pricing import SupplierPriceVersion, estimate_supplier_cost
from visual_platform.providers.qwen_vision import validate_vision_archive

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class VisualPriceReviewScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    final_sha256: Digest
    request_sha256: Digest
    response_digest: Digest
    merchant_fingerprint: Digest
    billing_group: Annotated[str, Field(min_length=1, max_length=160)]
    observed_at: datetime


def estimate_visual_archive(price, record, scope):
    price = SupplierPriceVersion.model_validate(price.model_dump(), strict=True)
    scope = VisualPriceReviewScope.model_validate(scope.model_dump(), strict=True)
    record = validate_vision_archive(record)
    if any(
        record[name] != getattr(scope, name)
        for name in ("final_sha256", "request_sha256", "response_digest")
    ):
        raise ValueError("visual_price_review_provenance_mismatch")
    # Unknown or contradictory original counters are not replaced by operator-supplied quantities.
    quantities = record["quantities"] if record["billing_usage_integrity"] == "complete" else {}
    return {
        **estimate_supplier_cost(
            price,
            quantities,
            provider=record["provider"],
            model=record["model"],
            endpoint_origin=record["endpoint_origin"],
            merchant_fingerprint=scope.merchant_fingerprint,
            billing_group=scope.billing_group,
            observed_at=scope.observed_at,
        ),
        "advisory_only": True,
        "scope_authenticity_verified": False,
        "spending_authorized": False,
        "task_cost_record_created": False,
        "final_sha256": record["final_sha256"],
        "request_sha256": record["request_sha256"],
        "wire_response_sha256": record["wire_response_sha256"],
    }
