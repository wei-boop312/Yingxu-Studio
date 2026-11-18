from __future__ import annotations

import math
import re
from collections import deque
from typing import Any

from .schemas import (
    RegistrySnapshot,
    ValidationIssue,
    ValidationReport,
    WorkflowPlan,
    InputDefinition,
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
                    (stage.id for stage in plan.stages if capability in stage.missing),
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
        if not isinstance(compiled, dict):
            return ValidationReport(
                valid=False,
                errors=(
                    ValidationIssue(
                        code="INVALID_WORKFLOW", message="workflow 必须是对象"
                    ),
                ),
            )
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

        if any(not isinstance(node_id, str) or not node_id for node_id in prompt):
            errors.append(
                ValidationIssue(
                    code="INVALID_NODE_ID", message="节点 ID 必须是非空字符串"
                )
            )
        missing_nodes: set[str] = set()
        edges: dict[str, set[str]] = {node_id: set() for node_id in prompt}
        saver_ids: set[str] = set()

        for node_id, node in prompt.items():
            if not isinstance(node, dict):
                errors.append(
                    ValidationIssue(
                        code="INVALID_NODE",
                        node_id=str(node_id),
                        message="节点必须是对象",
                    )
                )
                continue
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
                target_input = node_def.input_by_name(name)
                if target_input is None:
                    errors.append(
                        ValidationIssue(
                            code="UNKNOWN_INPUT",
                            node_id=str(node_id),
                            message=f"{class_name} 不包含输入 {name}；动态输入需宿主合同核验",
                        )
                    )
                    continue
                if isinstance(value, list):
                    if not _is_prompt_link(value):
                        errors.append(
                            ValidationIssue(
                                code="INVALID_LINK",
                                node_id=str(node_id),
                                message=f"输入 {name} 的连接结构或端口类型不合法",
                            )
                        )
                        continue
                    source_id, output_index = str(value[0]), value[1]
                    if source_id not in prompt:
                        errors.append(
                            ValidationIssue(
                                code="BROKEN_LINK",
                                node_id=node_id,
                                message=f"输入 {name} 引用了不存在的节点 {source_id}",
                            )
                        )
                        continue
                    source = prompt[source_id]
                    if not isinstance(source, dict):
                        errors.append(
                            ValidationIssue(
                                code="BROKEN_LINK",
                                node_id=str(node_id),
                                message=f"输入 {name} 引用了无效节点",
                            )
                        )
                        continue
                    source_class = str(source.get("class_type", ""))
                    source_def = registry.nodes.get(source_class)
                    if source_def and not 0 <= output_index < len(source_def.outputs):
                        errors.append(
                            ValidationIssue(
                                code="INVALID_OUTPUT_INDEX",
                                node_id=str(node_id),
                                message=f"{source_class} 输出端口 {output_index} 越界",
                            )
                        )
                        continue
                    edges[source_id].add(node_id)
                    if (
                        target_input
                        and source_def
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
                    code = _scalar_error(target_input, value)
                    if code:
                        errors.append(
                            ValidationIssue(
                                code=code,
                                node_id=node_id,
                                message=(
                                    f"{class_name}.{name} 的值 {value!r} "
                                    "不符合当前节点参数合同"
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

        if _has_cycle(edges):
            errors.append(
                ValidationIssue(
                    code="CYCLIC_DEPENDENCY", message="workflow 包含循环依赖"
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

        metadata = compiled.get("ui_workflow", {})
        for key in ("extra", "workflow_architect"):
            if not isinstance(metadata, dict):
                break
            metadata = metadata.get(key, {})
        if not isinstance(metadata, dict):
            errors.append(
                ValidationIssue(
                    code="INVALID_WORKFLOW_METADATA",
                    message="workflow 元数据必须是对象",
                )
            )
            metadata = {}
        if not metadata.get("manual_review_required"):
            warnings.append(
                ValidationIssue(
                    code="MISSING_MANUAL_REVIEW_NOTICE",
                    message="workflow 未标记最终结果需人工检查",
                )
            )
        missing_models = compiled.get("missing_models", ())
        if not isinstance(missing_models, (list, tuple)) or any(
            not isinstance(x, str) for x in missing_models
        ):
            errors.append(
                ValidationIssue(
                    code="INVALID_WORKFLOW_METADATA", message="模型缺项必须是字符串列表"
                )
            )
            missing_models = ()
        return ValidationReport(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            missing_nodes=tuple(sorted(item for item in missing_nodes if item)),
            missing_models=tuple(missing_models),
            draft_created=is_draft,
        )


def _is_prompt_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and type(value[0]) in (str, int)
        and type(value[1]) is int
    )


def _compatible(output_type: str, input_type: str) -> bool:
    return output_type == input_type or "*" in {output_type, input_type}


def _has_predecessor(target: str, edges: dict[str, set[str]]) -> bool:
    return any(target in destinations for destinations in edges.values())


def _has_cycle(edges: dict[str, set[str]]) -> bool:
    incoming = {node: 0 for node in edges}
    for destinations in edges.values():
        for node in destinations:
            incoming[node] += 1
    ready = deque(node for node, count in incoming.items() if count == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for destination in edges[node]:
            incoming[destination] -= 1
            if incoming[destination] == 0:
                ready.append(destination)
    return visited != len(edges)


def _scalar_error(definition: InputDefinition, value: Any) -> str | None:
    if definition.force_input:
        return "INVALID_INPUT_TYPE"
    choices = definition.enum_values or definition.choices
    if choices:
        if not any(
            type(value) is type(choice) and value == choice for choice in choices
        ):
            return "INVALID_WIDGET_CHOICE"
        return None
    kind = definition.type
    if kind == "INT":
        valid = type(value) is int
    elif kind == "FLOAT":
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
    elif kind == "BOOLEAN":
        valid = type(value) is bool
    elif kind == "STRING":
        valid = isinstance(value, str)
    else:
        valid = False
    if not valid:
        return "INVALID_INPUT_TYPE"
    if kind in ("INT", "FLOAT"):
        for bound in (definition.minimum, definition.maximum):
            if bound is not None and (
                type(bound) not in (int, float)
                or (type(bound) is float and not math.isfinite(bound))
            ):
                return "INVALID_INPUT_CONTRACT"
        if definition.minimum is not None and value < definition.minimum:
            return "INPUT_OUT_OF_RANGE"
        if definition.maximum is not None and value > definition.maximum:
            return "INPUT_OUT_OF_RANGE"
    return None
