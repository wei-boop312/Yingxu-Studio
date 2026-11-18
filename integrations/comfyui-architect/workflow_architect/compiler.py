from __future__ import annotations

import copy
import re
from typing import Any

from .ecommerce import build_ecommerce_prompts
from .errors import CompilationError
from .schemas import (
    InputDefinition,
    NodeDefinition,
    RegistrySnapshot,
    TaskSpec,
    WorkflowPlan,
)
from .settings import GENERATOR_VERSION
from .templates import TemplateRepository

PLACEHOLDER = re.compile(r"^\$\{([a-z_]+)\}$")
GROUP_COLORS = {
    "input": "#355c7d",
    "inputs": "#355c7d",
    "model": "#695985",
    "conditioning": "#9a5f7a",
    "latent": "#a66b5b",
    "sampling": "#c46872",
    "decode": "#bd8056",
    "preprocessing": "#6c5b7b",
    "protected_regions": "#c06c84",
    "generation": "#f67280",
    "identity_restore": "#f8b195",
    "edge_blend": "#4f7c7a",
    "output": "#466365",
}
WIDGET_INPUT_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


class WorkflowCompiler:
    """Compiles only a reviewed concrete template against the live registry."""

    def compile(
        self,
        spec: TaskSpec,
        plan: WorkflowPlan,
        registry: RegistrySnapshot,
        concrete_template: dict[str, Any],
        *,
        installed_models: set[str] | None = None,
        runtime_bindings: dict[str, Any] | None = None,
        allow_draft: bool = False,
    ) -> dict[str, Any]:
        concrete = TemplateRepository.validate_concrete(concrete_template)
        if not plan.executable and not allow_draft:
            raise CompilationError(
                "方案缺少能力或经验证的具体模板，不能编译为可执行 workflow",
                details={"missing_capabilities": list(plan.missing_capabilities)},
            )
        if concrete.get("task") != spec.task:
            raise CompilationError(f"具体模板任务类型与 {spec.task} 不匹配")

        required_models = {_model_id(item) for item in concrete.get("required_models", [])}
        missing_models: list[str] = []
        if required_models:
            installed = installed_models or set()
            missing_models = sorted(required_models - installed)
            if missing_models and not allow_draft:
                raise CompilationError(
                    "具体模板需要的模型尚未确认安装",
                    details={"missing_models": missing_models},
                )

        prefixes = {
            "text_to_image": "workflow_architect/text_to_image",
            "image_to_image": "workflow_architect/image_to_image",
            "inpaint": "workflow_architect/inpaint",
            "image_resize": "workflow_architect/resize",
            "image_upscale": "workflow_architect/upscale",
            "virtual_tryon": "workflow_architect/tryon",
            "ecommerce_detail": "workflow_architect/ecommerce_detail",
        }
        ecommerce_prompts = (
            build_ecommerce_prompts(
                spec.prompt.positive,
                width=spec.output.width,
                height=spec.output.height,
            )
            if spec.task == "ecommerce_detail"
            else {}
        )
        bindings: dict[str, Any] = {
            "width": spec.output.width,
            "height": spec.output.height,
            "format": spec.output.format,
            "person_image": _preferred_choice(
                registry, "LoadImage", "image", ("person.png",)
            ),
            "garment_image": _preferred_choice(
                registry, "LoadImage", "image", ("garment.png",)
            ),
            "source_image": _preferred_choice(
                registry, "LoadImage", "image", ("source.png",)
            ),
            "mask_image": _preferred_choice(
                registry, "LoadImage", "image", ("mask.png",)
            ),
            "filename_prefix": prefixes[spec.task],
            "positive_prompt": spec.prompt.positive,
            "negative_prompt": spec.prompt.negative,
            "seed": spec.prompt.seed,
            "steps": spec.prompt.steps,
            "cfg": float(spec.prompt.cfg),
            "denoise": float(spec.prompt.denoise),
            "product_width": max(64, int(spec.output.width * 0.72) // 8 * 8),
            "product_height": max(64, int(spec.output.height * 0.48) // 8 * 8),
            "product_x": max(0, int(spec.output.width * 0.14)),
            "product_y": max(0, int(spec.output.height * 0.26)),
            "sampler_name": _preferred_choice(
                registry,
                "KSampler",
                "sampler_name",
                ("dpmpp_2m", "euler", "euler_ancestral"),
            ),
            "scheduler": _preferred_choice(
                registry,
                "KSampler",
                "scheduler",
                ("karras", "normal", "simple"),
            ),
        }
        bindings.update(ecommerce_prompts)
        bindings.update(runtime_bindings or {})
        nodes = concrete["nodes"]
        ids = {str(node["key"]): index for index, node in enumerate(nodes, 1)}
        class_names: dict[str, str] = {}
        api_prompt: dict[str, Any] = {}

        for node in nodes:
            key = str(node["key"])
            class_name = self._resolve_class(node, registry, allow_draft=allow_draft)
            class_names[key] = class_name
            if class_name not in registry.nodes and not allow_draft:
                raise CompilationError(
                    f"模板节点类不存在: {class_name}",
                    details={"missing_nodes": [class_name]},
                )
            inputs = _resolve(copy.deepcopy(node.get("inputs", {})), bindings)
            api_inputs: dict[str, Any] = {}
            for name, value in inputs.items():
                if _is_link(value):
                    source_key, output_index = value["link"]
                    if str(source_key) not in ids:
                        raise CompilationError(f"节点 {key} 引用了不存在的节点 {source_key}")
                    api_inputs[str(name)] = [str(ids[str(source_key)]), int(output_index)]
                else:
                    api_inputs[str(name)] = value
            api_prompt[str(ids[key])] = {
                "class_type": class_name,
                "inputs": api_inputs,
                "_meta": {"title": str(node.get("title", class_name))},
            }

        node_definitions = self._node_definitions(nodes, class_names, registry)
        missing_nodes = sorted(
            {class_name for class_name in class_names.values() if class_name not in registry.nodes}
        )
        execution_ready = plan.executable and not missing_nodes and not missing_models
        ui_nodes = self._ui_nodes(
            nodes, ids, class_names, api_prompt, node_definitions
        )
        ui_links = self._ui_links(nodes, ids, class_names, node_definitions)
        groups = self._groups(nodes, ids)
        ui_workflow = {
            "last_node_id": max(ids.values(), default=0),
            "last_link_id": max((link[0] for link in ui_links), default=0),
            "nodes": ui_nodes,
            "links": ui_links,
            "groups": groups,
            "config": {},
            "extra": {
                "workflow_architect": {
                    "generator_version": GENERATOR_VERSION,
                    "abstract_template_id": plan.template_id,
                    "concrete_template_id": concrete.get("template_id"),
                    "task_schema_version": spec.schema_version,
                    "task": spec.task,
                    "profile": spec.execution.profile,
                    "draft": not execution_ready,
                    "execution_ready": execution_ready,
                    "missing_nodes": missing_nodes,
                    "missing_models": missing_models,
                    "identity_strategy": (
                        "protected_region_composite"
                        if spec.task == "virtual_tryon"
                        else None
                    ),
                    "manual_review_required": True,
                }
            },
            "version": 0.4,
        }
        return {
            "schema_version": "2.0",
            "generator_version": GENERATOR_VERSION,
            "template_id": plan.template_id,
            "concrete_template_id": concrete.get("template_id"),
            "task_spec": spec.to_dict(),
            "api_prompt": api_prompt,
            "ui_workflow": ui_workflow,
            "required_models": sorted(required_models),
            "missing_nodes": missing_nodes,
            "missing_models": missing_models,
            "draft": not execution_ready,
            "execution_ready": execution_ready,
            "dependency_report": {
                "missing_capabilities": list(plan.missing_capabilities),
                "missing_nodes": missing_nodes,
                "missing_models": missing_models,
                "manager_suggestions": list(plan.manager_suggestions),
                "next_step": (
                    "resolve_with_manager_then_refresh"
                    if not execution_ready
                    else "ready_to_run"
                ),
            },
        }

    @staticmethod
    def _resolve_class(
        node: dict[str, Any],
        registry: RegistrySnapshot,
        *,
        allow_draft: bool = False,
    ) -> str:
        explicit = node.get("class_type")
        if explicit:
            return str(explicit)
        capability = str(node.get("capability", "")).split(".", 1)[0]
        status = registry.capabilities.get(capability)
        if not status or not status.matched_node:
            if allow_draft and status and status.candidates:
                return status.candidates[0]
            raise CompilationError(
                f"能力 {capability or '(空)'} 没有可用节点",
                details={"missing_capabilities": [capability]},
            )
        return status.matched_node

    @staticmethod
    def _node_definitions(
        nodes: list[dict[str, Any]],
        class_names: dict[str, str],
        registry: RegistrySnapshot,
    ) -> dict[str, NodeDefinition]:
        definitions = dict(registry.nodes)
        output_counts: dict[str, int] = {}
        for target in nodes:
            for value in target.get("inputs", {}).values():
                if _is_link(value):
                    source_key, output_index = str(value["link"][0]), int(value["link"][1])
                    output_counts[source_key] = max(
                        output_counts.get(source_key, 0), output_index + 1
                    )
        for node in nodes:
            key = str(node["key"])
            class_name = class_names[key]
            if class_name in definitions:
                continue
            inputs = tuple(
                InputDefinition(
                    name=str(name),
                    type="*" if _is_link(value) else _guess_input_type(value),
                    required=False,
                    default=None if _is_link(value) else value,
                )
                for name, value in node.get("inputs", {}).items()
            )
            output_count = max(output_counts.get(key, 0), 1)
            definitions[class_name] = NodeDefinition(
                class_name=class_name,
                inputs=inputs,
                outputs=tuple("*" for _ in range(output_count)),
                output_names=tuple(f"待解析输出 {index + 1}" for index in range(output_count)),
                category="workflow_architect/draft",
            )
        return definitions

    @staticmethod
    def _ui_nodes(
        nodes: list[dict[str, Any]],
        ids: dict[str, int],
        class_names: dict[str, str],
        api_prompt: dict[str, Any],
        node_definitions: dict[str, NodeDefinition],
    ) -> list[dict[str, Any]]:
        link_map: dict[tuple[str, str], int] = {}
        link_id = 1
        for target in nodes:
            for input_name, value in target.get("inputs", {}).items():
                if _is_link(value):
                    link_map[(str(target["key"]), str(input_name))] = link_id
                    link_id += 1

        output_links: dict[tuple[str, int], list[int]] = {}
        for target in nodes:
            target_key = str(target["key"])
            for input_name, value in target.get("inputs", {}).items():
                if _is_link(value):
                    source_key, output_index = value["link"]
                    output_links.setdefault((str(source_key), int(output_index)), []).append(
                        link_map[(target_key, str(input_name))]
                    )

        result: list[dict[str, Any]] = []
        for order, node in enumerate(nodes):
            key = str(node["key"])
            node_def = node_definitions[class_names[key]]
            prompt_inputs = api_prompt[str(ids[key])]["inputs"]
            linked_inputs = {
                name: link_map[(key, name)]
                for name, value in node.get("inputs", {}).items()
                if _is_link(value)
            }
            connectable_inputs = [
                item for item in node_def.inputs if not _is_widget_input(item)
            ]
            ui_inputs = [
                {"name": item.name, "type": item.type, "link": linked_inputs.get(item.name)}
                for item in connectable_inputs
            ]
            ui_outputs = [
                {
                    "name": (
                        node_def.output_names[index]
                        if index < len(node_def.output_names)
                        else output_type
                    ),
                    "type": output_type,
                    "links": output_links.get((key, index)) or None,
                    "slot_index": index,
                }
                for index, output_type in enumerate(node_def.outputs)
            ]
            widgets_values: list[Any] = []
            for item in node_def.inputs:
                if (
                    not _is_widget_input(item)
                    or item.name not in prompt_inputs
                    or isinstance(prompt_inputs[item.name], list)
                ):
                    continue
                widgets_values.append(prompt_inputs[item.name])
                control_value = _control_after_generate_value(item)
                if control_value is not None:
                    widgets_values.append(control_value)
            result.append(
                {
                    "id": ids[key],
                    "type": class_names[key],
                    "pos": list(node.get("position", [order * 260, 0])),
                    "size": list(node.get("size", [220, 100])),
                    "flags": {},
                    "order": order,
                    "mode": 0,
                    "inputs": ui_inputs,
                    "outputs": ui_outputs,
                    "properties": {
                        "Node name for S&R": class_names[key],
                        "workflow_architect_stage": node.get("stage"),
                        "workflow_architect_missing": (
                            node_def.category == "workflow_architect/draft"
                        ),
                    },
                    "widgets_values": widgets_values,
                    "title": str(node.get("title", class_names[key])),
                }
            )
        return result

    @staticmethod
    def _ui_links(
        nodes: list[dict[str, Any]],
        ids: dict[str, int],
        class_names: dict[str, str],
        node_definitions: dict[str, NodeDefinition],
    ) -> list[list[Any]]:
        links: list[list[Any]] = []
        link_id = 1
        for target in nodes:
            target_key = str(target["key"])
            target_def = node_definitions[class_names[target_key]]
            for input_name, value in target.get("inputs", {}).items():
                if not _is_link(value):
                    continue
                source_key, output_index = str(value["link"][0]), int(value["link"][1])
                connectable_inputs = [
                    item for item in target_def.inputs if not _is_widget_input(item)
                ]
                input_index = next(
                    (
                        index
                        for index, item in enumerate(connectable_inputs)
                        if item.name == input_name
                    ),
                    -1,
                )
                if input_index < 0:
                    raise CompilationError(
                        f"节点 {target_key} 不存在输入端口 {input_name}"
                    )
                source_def = node_definitions[class_names[source_key]]
                if output_index >= len(source_def.outputs):
                    raise CompilationError(
                        f"节点 {source_key} 不存在输出端口 {output_index}"
                    )
                links.append(
                    [
                        link_id,
                        ids[source_key],
                        output_index,
                        ids[target_key],
                        input_index,
                        source_def.outputs[output_index],
                    ]
                )
                link_id += 1
        return links

    @staticmethod
    def _groups(nodes: list[dict[str, Any]], ids: dict[str, int]) -> list[dict[str, Any]]:
        del ids
        groups: list[dict[str, Any]] = []
        stage_order = list(GROUP_COLORS)
        for stage in stage_order:
            stage_nodes = [item for item in nodes if item.get("stage") == stage]
            if not stage_nodes:
                continue
            positions = [item.get("position", [0, 0]) for item in stage_nodes]
            sizes = [item.get("size", [220, 100]) for item in stage_nodes]
            left = min(pos[0] for pos in positions) - 30
            top = min(pos[1] for pos in positions) - 60
            right = max(pos[0] + size[0] for pos, size in zip(positions, sizes)) + 30
            bottom = max(pos[1] + size[1] for pos, size in zip(positions, sizes)) + 30
            title = next(
                (str(item.get("group_title")) for item in stage_nodes if item.get("group_title")),
                stage,
            )
            groups.append(
                {
                    "title": title,
                    "bounding": [left, top, right - left, bottom - top],
                    "color": GROUP_COLORS[stage],
                    "font_size": 24,
                    "flags": {},
                    "workflow_architect_note": (
                        "受保护区域不得被换装蒙版覆盖；最终结果仍需人工检查。"
                        if stage == "protected_regions"
                        else ""
                    ),
                }
            )
        return groups


def _resolve(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = PLACEHOLDER.match(value)
        if match:
            key = match.group(1)
            if key not in bindings:
                raise CompilationError(f"模板包含不支持的占位符: {value}")
            return bindings[key]
        return value
    if isinstance(value, list):
        return [_resolve(item, bindings) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, bindings) for key, item in value.items()}
    return value


def _is_link(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("link"), list)
        and len(value["link"]) == 2
    )


def _guess_input_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    return "STRING"


def _is_widget_input(definition: InputDefinition) -> bool:
    return definition.type in WIDGET_INPUT_TYPES


def _control_after_generate_value(
    definition: InputDefinition,
) -> str | None:
    configured = definition.control_after_generate
    enabled = (
        configured
        if configured is not None
        else definition.name in {"seed", "noise_seed"}
    )
    if not enabled:
        return None
    if isinstance(configured, str):
        return configured
    return "fixed" if definition.type == "FLOAT" else "randomize"


def _preferred_choice(
    registry: RegistrySnapshot,
    class_name: str,
    input_name: str,
    preferred: tuple[str, ...],
) -> str:
    node = registry.nodes.get(class_name)
    definition = node.input_by_name(input_name) if node else None
    choices = definition.choices if definition else ()
    if choices:
        return next((item for item in preferred if item in choices), choices[0])
    return preferred[0]


def _model_id(item: Any) -> str:
    if isinstance(item, dict):
        folder = str(item.get("folder", "")).strip()
        name = str(item.get("name", "")).strip()
        if not folder or not name:
            raise CompilationError("模型依赖必须同时包含 folder 和 name")
        return f"{folder}:{name}"
    value = str(item).strip()
    if not value:
        raise CompilationError("模型依赖名称不能为空")
    return value
