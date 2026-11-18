from __future__ import annotations

import re
from typing import Any

from .schemas import (
    RegistrySnapshot,
    ValidationIssue,
    ValidationReport,
    WorkflowPlan,
)

UNRESOLVED = re.compile(r"\$\{[^}]+\}")
REMOTE_NODE = re.compile(r"(?:http|https|api|cloud|remote|upload|websocket)", re.I)


class WorkflowValidator:
    def validate_plan(
        self,
        plan: WorkflowPlan,
        *,
        require_executable: bool = False,
    ) -> ValidationReport:
        issues = tuple(
            ValidationIssue(
                code="MISSING_CAPABILITY",
                stage=next(
                    (
                        stage.id
                        for stage in plan.stages
                        if capability in stage.missing
                    ),
                    None,
                ),
                capability=capability,
                message=f"当前环境没有可用的 {capability} 实现",
            )
            for capability in plan.missing_capabilities
        )
        if not plan.concrete_template_id:
            issues += (
                ValidationIssue(
                    code="MISSING_CONCRETE_TEMPLATE",
                    message=f"没有匹配 {plan.task} / {plan.profile} 的已验证具体模板",
                ),
            )
        return ValidationReport(
            valid=not issues if require_executable else True,
            errors=issues if require_executable else (),
            warnings=() if require_executable else issues,
            missing_nodes=plan.missing_capabilities,
        )

    def validate_compiled(
        self,
        compiled: dict[str, Any],
        registry: RegistrySnapshot,
    ) -> ValidationReport:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        is_draft = bool(compiled.get("draft"))
        prompt = compiled.get("api_prompt")
        if not isinstance(prompt, dict) or not prompt:
            return ValidationReport(
                valid=False,
                errors=(
                    ValidationIssue(
                        code="EMPTY_WORKFLOW", message="workflow 不包含可执行节点"
                    ),
                ),
            )

        ids = list(prompt)
        if len(ids) != len(set(ids)):
            errors.append(
                ValidationIssue(code="DUPLICATE_NODE_ID", message="存在重复节点 ID")
            )
        missing_nodes: set[str] = set()
        edges: dict[str, set[str]] = {node_id: set() for node_id in prompt}
        saver_ids: set[str] = set()

        for node_id, node in prompt.items():
            class_name = str(node.get("class_type", ""))
            node_def = registry.nodes.get(class_name)
            if not node_def:
                missing_nodes.add(class_name)
                target = warnings if is_draft else errors
                target.append(
                    ValidationIssue(
                        code="MISSING_NODE",
                        node_id=node_id,
                        message=(
                            f"草稿引用尚未安装的节点类: {class_name}"
                            if is_draft
                            else f"节点类不存在: {class_name}"
                        ),
                    )
                )
                continue
            if REMOTE_NODE.search(class_name):
                errors.append(
                    ValidationIssue(
                        code="UNCONFIRMED_REMOTE_NODE",
                        node_id=node_id,
                        message=f"未确认的远程调用节点被拒绝: {class_name}",
                    )
                )
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                errors.append(
                    ValidationIssue(
                        code="INVALID_INPUTS",
                        node_id=node_id,
                        message="节点 inputs 必须是对象",
                    )
                )
                continue
            for definition in node_def.inputs:
                if definition.required and definition.name not in inputs:
                    errors.append(
                        ValidationIssue(
                            code="MISSING_REQUIRED_INPUT",
                            node_id=node_id,
                            message=f"{class_name} 缺少必填输入 {definition.name}",
                        )
                    )
            for name, value in inputs.items():
                if _is_prompt_link(value):
                    source_id, output_index = str(value[0]), int(value[1])
                    if source_id not in prompt:
                        errors.append(
                            ValidationIssue(
                                code="BROKEN_LINK",
                                node_id=node_id,
                                message=f"输入 {name} 引用了不存在的节点 {source_id}",
                            )
                        )
                        continue
                    edges[source_id].add(node_id)
                    target_input = node_def.input_by_name(name)
                    source_class = str(prompt[source_id].get("class_type", ""))
                    source_def = registry.nodes.get(source_class)
                    if (
                        target_input
                        and source_def
                        and output_index < len(source_def.outputs)
                        and not _compatible(
                            source_def.outputs[output_index], target_input.type
                        )
                    ):
                        errors.append(
                            ValidationIssue(
                                code="PORT_TYPE_MISMATCH",
                                node_id=node_id,
                                message=(
                                    f"{source_class}[{output_index}] 的 "
                                    f"{source_def.outputs[output_index]} 不能连接到 "
                                    f"{class_name}.{name} 的 {target_input.type}"
                                ),
                            )
                        )
                else:
                    target_input = node_def.input_by_name(name)
                    if (
                        target_input
                        and target_input.choices
                        and str(value) not in target_input.choices
                    ):
                        errors.append(
                            ValidationIssue(
                                code="INVALID_WIDGET_CHOICE",
                                node_id=node_id,
                                message=(
                                    f"{class_name}.{name} 的值 {value!r} "
                                    "不在当前 ComfyUI 的有效选项中"
                                ),
                            )
                        )
            if any(
                status.matched_node == class_name and name == "image_saver"
                for name, status in registry.capabilities.items()
            ):
                saver_ids.add(node_id)
            if UNRESOLVED.search(repr(node)):
                errors.append(
                    ValidationIssue(
                        code="UNRESOLVED_PLACEHOLDER",
                        node_id=node_id,
                        message="节点包含未解析占位符",
                    )
                )

        if not saver_ids:
            target = warnings if is_draft else errors
            target.append(
                ValidationIssue(
                    code="MISSING_OUTPUT_NODE", message="workflow 没有可识别的保存节点"
                )
            )
        elif not any(_has_predecessor(saver, edges) for saver in saver_ids):
            errors.append(
                ValidationIssue(
                    code="DISCONNECTED_OUTPUT",
                    message="保存节点没有可到达的上游图像分支",
                )
            )

        metadata = (
            compiled.get("ui_workflow", {})
            .get("extra", {})
            .get("workflow_architect", {})
        )
        if not metadata.get("manual_review_required"):
            warnings.append(
                ValidationIssue(
                    code="MISSING_MANUAL_REVIEW_NOTICE",
                    message="workflow 未标记最终结果需人工检查",
                )
            )
        return ValidationReport(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            missing_nodes=tuple(sorted(item for item in missing_nodes if item)),
            missing_models=tuple(compiled.get("missing_models", ())),
            draft_created=is_draft,
        )


def _is_prompt_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def _compatible(output_type: str, input_type: str) -> bool:
    return output_type == input_type or "*" in {output_type, input_type}


def _has_predecessor(target: str, edges: dict[str, set[str]]) -> bool:
    return any(target in destinations for destinations in edges.values())
