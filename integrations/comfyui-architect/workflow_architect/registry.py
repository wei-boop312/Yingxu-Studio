from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping

from .errors import InputError
from .schemas import (
    CapabilityStatus,
    InputDefinition,
    NodeDefinition,
    RegistrySnapshot,
)
from .settings import CAPABILITY_FILE


class CapabilityRegistry:
    """In-memory capability view built from real ComfyUI node definitions."""

    def __init__(self, config_path: Path = CAPABILITY_FILE):
        self._config_path = Path(config_path)
        self._lock = threading.RLock()
        self._generation = 0
        self._snapshot = RegistrySnapshot(0, 0, {}, {})
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        # The .yaml file intentionally uses JSON syntax, which is valid YAML 1.2.
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"无法读取能力配置: {exc}") from exc
        if not isinstance(data.get("capabilities"), dict):
            raise InputError("能力配置缺少 capabilities 对象")
        return data

    def current(self) -> RegistrySnapshot:
        with self._lock:
            return self._snapshot

    def refresh(
        self,
        object_info: Mapping[str, Any] | None = None,
    ) -> RegistrySnapshot:
        """Refresh from supplied /object_info data or live NODE_CLASS_MAPPINGS."""
        if object_info is None:
            object_info = self._read_live_nodes()
        if not isinstance(object_info, Mapping):
            raise InputError("object_info 必须是节点定义对象")

        nodes: dict[str, NodeDefinition] = {}
        for class_name, raw in sorted(
            object_info.items(), key=lambda item: str(item[0])
        ):
            try:
                nodes[str(class_name)] = self._normalize_node(str(class_name), raw)
            except Exception:
                # A broken third-party node must not crash the registry.
                continue

        statuses: dict[str, CapabilityStatus] = {}
        for name, config in self._config["capabilities"].items():
            candidates = tuple(
                item["class"] if isinstance(item, dict) else str(item)
                for item in config.get("candidates", [])
            )
            matched = next(
                (candidate for candidate in candidates if candidate in nodes), None
            )
            statuses[name] = CapabilityStatus(
                name=name,
                required=bool(config.get("required", False)),
                matched_node=matched,
                candidates=candidates,
                search_terms=tuple(
                    str(item) for item in config.get("search_terms", ())
                ),
            )

        with self._lock:
            self._generation += 1
            self._snapshot = RegistrySnapshot(
                generation=self._generation,
                node_count=len(nodes),
                nodes=nodes,
                capabilities=statuses,
            )
            return self._snapshot

    @staticmethod
    def _read_live_nodes() -> Mapping[str, Any]:
        try:
            import nodes as comfy_nodes  # type: ignore
        except ImportError:
            return {}
        return getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}) or {}

    @staticmethod
    def _normalize_node(class_name: str, raw: Any) -> NodeDefinition:
        if isinstance(raw, Mapping):
            input_root = raw.get("input", {})
            outputs = tuple(str(item) for item in raw.get("output", ()))
            output_names = tuple(str(item) for item in raw.get("output_name", ()))
            category = str(raw.get("category", ""))
        else:
            input_types = getattr(raw, "INPUT_TYPES", None)
            input_root = input_types() if callable(input_types) else {}
            outputs = tuple(str(item) for item in getattr(raw, "RETURN_TYPES", ()))
            output_names = tuple(str(item) for item in getattr(raw, "RETURN_NAMES", ()))
            category = str(getattr(raw, "CATEGORY", ""))

        inputs: list[InputDefinition] = []
        for required, group_name in ((True, "required"), (False, "optional")):
            group = (
                input_root.get(group_name, {})
                if isinstance(input_root, Mapping)
                else {}
            )
            if not isinstance(group, Mapping):
                continue
            for name, definition in group.items():
                options = (
                    definition[1]
                    if isinstance(definition, (list, tuple))
                    and len(definition) > 1
                    and isinstance(definition[1], Mapping)
                    else {}
                )
                raw_type = (
                    definition[0]
                    if isinstance(definition, (list, tuple)) and definition
                    else definition
                )
                enum_values = (
                    tuple(raw_type)
                    if isinstance(raw_type, (list, tuple))
                    else tuple(options.get("options", ()))
                )
                (
                    input_type,
                    default,
                    choices,
                    control_after_generate,
                ) = _parse_input_definition(definition)
                inputs.append(
                    InputDefinition(
                        name=str(name),
                        type=input_type,
                        required=required,
                        default=default,
                        choices=choices,
                        control_after_generate=control_after_generate,
                        minimum=options.get("min"),
                        maximum=options.get("max"),
                        enum_values=enum_values,
                        force_input=bool(options.get("forceInput", False)),
                    )
                )
        return NodeDefinition(
            class_name=class_name,
            inputs=tuple(inputs),
            outputs=outputs,
            output_names=output_names,
            category=category,
        )


def _parse_input_definition(
    definition: Any,
) -> tuple[str, Any, tuple[str, ...], bool | str | None]:
    if isinstance(definition, (list, tuple)) and definition:
        raw_type = definition[0]
        options = (
            definition[1]
            if len(definition) > 1 and isinstance(definition[1], Mapping)
            else {}
        )
    else:
        raw_type, options = definition, {}
    if isinstance(raw_type, (list, tuple)):
        input_type = "COMBO"
        choices = tuple(str(item) for item in raw_type)
    else:
        input_type = str(raw_type)
        choices = tuple(str(item) for item in options.get("options", ()))
    default = options.get("default")
    if default is None and choices:
        default = (
            raw_type[0]
            if isinstance(raw_type, (list, tuple))
            else options["options"][0]
        )
    control_after_generate = options.get("control_after_generate")
    if not isinstance(control_after_generate, (bool, str)):
        control_after_generate = None
    return input_type, default, choices, control_after_generate
