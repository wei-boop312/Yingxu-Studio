from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import InputError
from .settings import CATALOG_FILE


class TemplateRepository:
    """Loads the reviewed V2 task catalog and concrete workflow templates."""

    def __init__(self, catalog_path: Path = CATALOG_FILE):
        self.catalog_path = Path(catalog_path)
        self.directory = self.catalog_path.parent

    def get_abstract(self, task: str) -> dict[str, Any]:
        catalog = self._read(self.catalog_path)
        tasks = catalog.get("tasks", {})
        template = tasks.get(task) if isinstance(tasks, dict) else None
        if not isinstance(template, dict) or not isinstance(template.get("stages"), list):
            raise InputError(f"任务 {task} 没有可用的抽象模板")
        return template

    def find_compatible_concrete(
        self,
        registry,
        task: str,
        profile: str,
    ) -> dict[str, Any] | None:
        directory = self.directory / "concrete"
        if not directory.exists():
            return None
        for path in sorted(directory.glob("*.json")):
            try:
                template = self.validate_concrete(self._read(path))
            except InputError:
                continue
            if template.get("task") != task:
                continue
            if profile not in template.get("profiles", ["stable"]):
                continue
            if all(self._node_available(node, registry) for node in template["nodes"]):
                return template
        return None

    def find_concrete_for_draft(
        self,
        task: str,
        profile: str,
    ) -> dict[str, Any] | None:
        """Return a reviewed template even when some runtime dependencies are absent."""
        directory = self.directory / "concrete"
        if not directory.exists():
            return None
        for path in sorted(directory.glob("*.json")):
            try:
                template = self.validate_concrete(self._read(path))
            except InputError:
                continue
            if template.get("task") != task:
                continue
            if profile not in template.get("profiles", ["stable"]):
                continue
            return template
        return None

    @staticmethod
    def _node_available(node: dict[str, Any], registry) -> bool:
        class_type = node.get("class_type")
        if class_type:
            return str(class_type) in registry.nodes
        capability = str(node.get("capability", "")).split(".", 1)[0]
        status = registry.capabilities.get(capability)
        return bool(status and status.available)

    @staticmethod
    def validate_concrete(template: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(template, dict) or template.get("kind") != "concrete":
            raise InputError("具体模板必须是 kind=concrete 的 JSON 对象")
        if not template.get("verified"):
            raise InputError("具体模板必须明确标记 verified=true")
        if not isinstance(template.get("nodes"), list) or not template["nodes"]:
            raise InputError("具体模板至少需要一个节点")
        keys = [str(item.get("key", "")) for item in template["nodes"]]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise InputError("具体模板节点 key 必须非空且唯一")
        return template

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"无法读取模板 {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise InputError(f"模板 {path.name} 必须是 JSON 对象")
        return data
