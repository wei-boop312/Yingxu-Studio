"""Assess initial rehearsal objectives without claiming a production backup SLA."""

import hashlib
import json
import math
from typing import Literal

from pydantic import Field

from visual_platform.domain.schemas import StrictModel


class RecoveryObjectives(StrictModel):
    version: int = Field(strict=True, ge=1, le=1)
    scope: Literal["initial_rehearsal"]
    source: str = Field(strict=True, min_length=1)
    source_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    rpo_seconds: int = Field(strict=True, gt=0)
    rto_seconds: int = Field(strict=True, gt=0)


def assess_recovery(rto_seconds, snapshot_age_seconds, document):
    for value in (rto_seconds, snapshot_age_seconds):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("finite nonnegative recovery measurement required")
    objectives = RecoveryObjectives.model_validate(document).model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(objectives, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "scope": "initial_rehearsal",
        "target_document_sha256": digest,
        "target_source": objectives["source"],
        "target_source_sha256": objectives["source_sha256"],
        "rpo_target_seconds": objectives["rpo_seconds"],
        "rto_target_seconds": objectives["rto_seconds"],
        "rehearsal_rpo_met": snapshot_age_seconds <= objectives["rpo_seconds"],
        "rehearsal_rto_met": rto_seconds <= objectives["rto_seconds"],
        "rpo_measurement": "snapshot age at recovery start; single rehearsal only",
        "production_backup_cadence_verified": False,
        "production_sla_verified": False,
    }
