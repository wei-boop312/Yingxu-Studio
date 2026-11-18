import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from visual_platform.domain.platforms import Platform
from visual_platform.domain.schemas import StrictModel

Text = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
Face = Literal["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "UNKNOWN"]


class OutputSpec(StrictModel):
    width: int = Field(default=1600, strict=True, ge=64, le=5000)
    height: int = Field(default=1600, strict=True, ge=64, le=5000)
    format: Literal["jpeg", "png"] = "jpeg"
    max_bytes: int = Field(default=3_000_000, strict=True, ge=10_000, le=20_000_000)


class SkuCreate(StrictModel):
    product_id: UUID
    code: str = Field(strict=True, min_length=1, max_length=80)
    name: Text


class ProjectCreate(StrictModel):
    template: Literal["sop10", "detail6", "custom"] = "sop10"
    sku_id: UUID
    title: Text
    platform: Platform
    marketplace: str = Field(strict=True, min_length=1, max_length=32)
    language: str = Field(strict=True, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    category: Text
    image_use: Literal["MAIN", "GALLERY", "DETAIL"]
    slot_count: int = Field(default=10, strict=True, ge=1, le=50)
    output: OutputSpec = Field(default_factory=OutputSpec)

    @model_validator(mode="after")
    def template_size(self):
        if self.template == "detail6" and self.slot_count != 6:
            raise ValueError("detail_template_requires_six_slots")
        return self


class SlotAddition(StrictModel):
    title: Text
    purpose: Literal["product", "dimensions", "package"] = "product"


class SlotScopeWrite(StrictModel):
    expected_revision: int = Field(strict=True, gt=0)
    slot_ids: list[UUID] = Field(max_length=50)
    additions: list[SlotAddition] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_scope(self):
        if len(self.slot_ids) != len(set(self.slot_ids)):
            raise ValueError("duplicate_slots")
        if not 1 <= len(self.slot_ids) + len(self.additions) <= 50:
            raise ValueError("active_slot_count_out_of_range")
        return self


class ReferenceCreate(StrictModel):
    asset_version_id: UUID
    role: Literal["product", "packaging", "accessory", "specification", "style", "scene"]
    physical_face: Face = "UNKNOWN"
    confirmed: bool = False
    note: str = Field(default="", strict=True, max_length=4000)


class FactWrite(StrictModel):
    appearance_profile_id: UUID | None = None
    fact_key: str = Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    expected_revision: int = Field(default=0, strict=True, ge=0)
    label: Text
    value: str = Field(default="", strict=True, max_length=4000)
    status: Literal["confirmed", "unknown", "conflict"]
    conditions: str = Field(default="", strict=True, max_length=2000)
    reference_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def confirmed_has_evidence(self):
        if self.status == "confirmed" and (not self.value.strip() or not self.reference_ids):
            raise ValueError("confirmed_fact_requires_value_and_evidence")
        return self


class PlanWrite(StrictModel):
    expected_revision: int = Field(strict=True, gt=0)
    goal: str = Field(strict=True, min_length=1, max_length=2000)
    copy_text: str = Field(default="", alias="copy", strict=True, max_length=4000)
    fact_version_ids: list[UUID] = Field(min_length=1, max_length=50)
    reference_ids: list[UUID] = Field(min_length=1, max_length=50)
    allowed_faces: list[Face] = Field(min_length=1, max_length=6)
    route: Literal["manual_import", "offline_fixture", "comfyui"] = "manual_import"


class ImportOutput(StrictModel):
    plan_id: UUID
    source_asset_version_id: UUID
    route: Literal["manual_import", "offline_fixture"] = "manual_import"
    source_note: str = Field(strict=True, min_length=1, max_length=2000)


class ExecutionCreate(StrictModel):
    expected_revision: int = Field(strict=True, gt=0)
    plan_id: UUID
    source_asset_version_id: UUID
    confirmed_connection_test: bool = Field(strict=True)

    @model_validator(mode="after")
    def explicit_scope(self):
        if self.confirmed_connection_test is not True:
            raise ValueError("connection_test_confirmation_required")
        return self


CheckResult = Literal["passed", "failed", "unknown", "not_applicable"]


class ReviewChecks(StrictModel):
    identity: CheckResult
    structure: CheckResult
    accessories: CheckResult
    copy_check: CheckResult = Field(alias="copy")
    scene: CheckResult
    technical: CheckResult


class ReviewWrite(StrictModel):
    expected_revision: int = Field(strict=True, gt=0)
    decision: Literal["passed", "rework", "insufficient_evidence"]
    critical: bool
    checks: ReviewChecks
    notes: str = Field(strict=True, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def pass_requires_all_checks(self):
        if self.decision == "passed" and (
            self.critical
            or any(v not in ("passed", "not_applicable") for v in self.checks.model_dump().values())
        ):
            raise ValueError("critical_or_unknown_checks_cannot_pass")
        return self


class DeliveryWrite(StrictModel):
    expected_revision: int = Field(strict=True, gt=0)
    mode: Literal["draft", "formal"]
    output_ids: list[UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_outputs(self):
        if len(set(self.output_ids)) != len(self.output_ids):
            raise ValueError("duplicate_outputs")
        return self


class CaseWrite(StrictModel):
    delivery_id: UUID
    title: Text
    lessons: str = Field(strict=True, min_length=1, max_length=8000)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def default_slots(count, template="sop10"):
    titles = [
        "首图",
        "总览",
        "卖点一",
        "卖点二",
        "卖点三",
        "使用场景",
        "细节",
        "补充说明",
        "尺寸",
        "包装配件",
    ]
    if template == "detail6":
        titles = ["商品展示", "核心卖点", "商品细节", "使用场景", "规格参数", "商品总览"]
    return [
        dict(
            display_number=i + 1,
            title=titles[i] if i < len(titles) else f"自定义图 {i + 1}",
            purpose="dimensions"
            if (i == 4 and template == "detail6") or i == 8
            else "package"
            if i == 9
            else "product",
            required_fact_keys=["appearance"]
            + (
                ["dimensions"]
                if (i == 4 and template == "detail6") or i == 8
                else ["package_list"]
                if i == 9
                else []
            ),
        )
        for i in range(count)
    ]


def readiness(slot, facts, references):
    by_key = {f["fact_key"]: f for f in facts}
    reasons = []
    for key in slot["required_fact_keys"]:
        if key not in by_key or by_key[key]["status"] != "confirmed":
            reasons.append("fact:" + key)
    if not any(
        r["role"] == "product" and r["confirmed"] and r["physical_face"] != "UNKNOWN"
        for r in references
    ):
        reasons.append("reference:confirmed_product_face")
    if slot["purpose"] == "package" and not any(
        r["role"] in ("packaging", "accessory") and r["confirmed"] for r in references
    ):
        reasons.append("reference:package_visual")
    conflict = any(
        by_key.get(key, {}).get("status") == "conflict" for key in slot["required_fact_keys"]
    )
    return {
        "status": "conflict" if conflict else "missing" if reasons else "ready",
        "reasons": reasons,
    }
