"""Receipt-scoped bill preflight. Matching a document never confirms supplier spending."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visual_platform.domain.provider_pricing import IDENTITIES, Money
from visual_platform.providers.authorization import BudgetCurrency, unique_object

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SupplierBillReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    task_id: UUID
    attempt_id: UUID
    quote_input_sha256: Fingerprint
    provider: Literal["bfl", "gemini"]
    model: str
    endpoint_origin: str
    merchant_fingerprint: Fingerprint
    billing_group: Annotated[str, Field(min_length=1, max_length=160)]
    provider_request_id: Annotated[str, Field(min_length=1, max_length=512)]
    response_digest: Fingerprint
    currency: BudgetCurrency
    amount: Money
    evidence_kind: Literal["offline_fixture", "reviewed_statement"]
    evidence_sha256: Fingerprint
    statement_fingerprint: Fingerprint
    line_fingerprint: Fingerprint

    @field_validator("billing_group", "provider_request_id")
    @classmethod
    def plain_text(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("blank or control characters rejected")
        return value

    @model_validator(mode="after")
    def scoped(self):
        model, origins = IDENTITIES[self.provider]
        if self.model != model or self.endpoint_origin not in origins:
            raise ValueError("unreviewed supplier identity")
        return self


def canonical_bill(review: SupplierBillReview):
    return json.dumps(
        review.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def review_supplier_bill(
    review: SupplierBillReview, context: dict, evidence: bytes, *, evidence_reviewed=False
):
    if evidence_reviewed is not True:
        raise ValueError("explicit_bill_evidence_review_required")
    if (
        not isinstance(evidence, bytes)
        or not 0 < len(evidence) <= 20 * 1024 * 1024
        or hashlib.sha256(evidence).hexdigest() != review.evidence_sha256
    ):
        raise ValueError("bill_evidence_mismatch")
    expected = context.get("scope")
    if not isinstance(expected, dict):
        raise ValueError("bill_scope_unavailable")
    document = review.model_dump(mode="json")
    fields = (
        "task_id",
        "attempt_id",
        "quote_input_sha256",
        "provider",
        "model",
        "endpoint_origin",
        "merchant_fingerprint",
        "billing_group",
        "provider_request_id",
        "response_digest",
        "currency",
    )
    if any(
        type(expected.get(field)) is not str or expected[field] != document[field]
        for field in fields
    ):
        raise ValueError("bill_scope_mismatch")
    if (
        context.get("receipt_applied") is not True
        or context.get("price_scope_verified") is not True
    ):
        raise ValueError("bill_requires_applied_receipt_and_frozen_price")
    if review.provider == "gemini" and review.provider_request_id == "gemini-" + str(
        review.attempt_id
    ):
        raise ValueError("local_gemini_id_is_not_supplier_billing_id")
    # No live receipt namespace exists yet. Never let a mock document masquerade
    # as confirmation just because its amount happens to match an estimate.
    if (
        context.get("profile") != "offline_protocol_mock"
        or review.evidence_kind != "offline_fixture"
    ):
        raise ValueError("live_bill_reconciliation_not_enabled")
    from visual_platform.providers.authorization import SmokeAuthorization

    cap = Decimal(SmokeAuthorization.money(context["step_ceiling"]))
    amount = Decimal(review.amount)
    estimate = context.get("estimate")
    delta = None
    if (
        isinstance(estimate, dict)
        and estimate.get("cost_status") == "estimated"
        and estimate.get("currency") == review.currency
    ):
        value = estimate.get("amount")
        # Validate even private context before decimal arithmetic; do not infer unknown as zero.
        if not isinstance(value, str):
            raise ValueError("invalid_estimate_context")
        from pydantic import TypeAdapter

        value = TypeAdapter(Money).validate_python(value, strict=True)
        delta = format(amount - Decimal(value), "f")
    return {
        "result": "OFFLINE_BILL_SCOPE_MATCHED",
        "paid_calls": 0,
        "review_sha256": hashlib.sha256(canonical_bill(review)).hexdigest(),
        "context_sha256": hashlib.sha256(
            json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest(),
        "amount": review.amount,
        "currency": review.currency,
        "step_ceiling_relation": "over_ceiling" if amount > cap else "within_ceiling",
        "same_unit_estimate_delta": delta,
        "total_ceiling_relation": "not_evaluated",
        "actual_spending_verified": False,
        "confirmation_enabled": False,
        "evidence_archived": False,
        "budget_reopened": False,
        "user_credits_changed": False,
    }


def load_bill_review(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("bounded regular bill review document required")
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("bill document grew beyond limit")
    data = json.loads(raw, object_pairs_hook=unique_object)
    return SupplierBillReview.model_validate_json(json.dumps(data))
