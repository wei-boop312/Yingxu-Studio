"""Constrained Studio connectivity template, explicitly not a generative model."""

import hashlib
import json
import re
from uuid import UUID

from .registry import CapabilityRegistry
from .validator import WorkflowValidator


def compile_connection_test(plan, object_info, input_name, intent_id):
    UUID(str(intent_id))
    if not re.fullmatch(r"yingxu/[0-9a-f-]{36}/[0-9a-f]{64}\.png", input_name):
        raise ValueError("verified_content_addressed_input_required")
    if plan.get("route") != "comfyui":
        raise ValueError("comfy_plan_required")
    if not plan.get("facts") or not plan.get("references") or not plan.get("allowed_faces"):
        raise ValueError("frozen_evidence_plan_required")
    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": input_name}},
        "2": {"class_type": "SaveImage", "inputs": {
            "images": ["1", 0], "filename_prefix": "yingxu/" + str(intent_id),
        }},
    }
    registry = CapabilityRegistry().refresh(object_info)
    report = WorkflowValidator().validate_compiled({"api_prompt": prompt, "draft": False}, registry)
    if not report.to_dict()["execution_ready"]:
        raise ValueError("target_template_validation_failed:" + ",".join(e.code for e in report.errors))
    return {
        "api_prompt": prompt,
        "workflow_sha256": hashlib.sha256(json.dumps(prompt, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
        "execution_kind": "connection_test",
        "real_model_execution": False,
        "appearance_instruction_applied": False,
        "copy_instruction_applied": False,
        "notice": "Only LoadImage→SaveImage validates transport and recovery. No redraw, model inference or quality claim.",
        "validation": report.to_dict(),
    }
