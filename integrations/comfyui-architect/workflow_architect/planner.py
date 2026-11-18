from __future__ import annotations

from .schemas import PlanStage, RegistrySnapshot, TaskSpec, WorkflowPlan
from .templates import TemplateRepository


class WorkflowPlanner:
    def __init__(self, templates: TemplateRepository | None = None):
        self.templates = templates or TemplateRepository()

    def plan(
        self,
        spec: TaskSpec,
        registry: RegistrySnapshot,
        *,
        concrete_template: dict | None = None,
    ) -> WorkflowPlan:
        abstract = self.templates.get_abstract(spec.task)
        concrete_id: str | None = None
        if concrete_template is not None:
            concrete = self.templates.validate_concrete(concrete_template)
            if concrete.get("task") == spec.task:
                concrete_id = str(concrete.get("template_id", "user_concrete_template"))

        stages: list[PlanStage] = []
        needed: list[str] = []
        missing: list[str] = []
        for stage in abstract["stages"]:
            profile_capabilities = stage.get("profile_capabilities", {})
            selected = (
                profile_capabilities.get(spec.execution.profile)
                if isinstance(profile_capabilities, dict)
                else None
            )
            capabilities = tuple(
                str(item)
                for item in (
                    selected if isinstance(selected, list) else stage.get("capabilities", ())
                )
            )
            stage_missing = tuple(item for item in capabilities if not _available(registry, item))
            needed.extend(capabilities)
            missing.extend(stage_missing)
            stages.append(
                PlanStage(
                    id=str(stage["id"]),
                    title=str(stage.get("title", stage["id"])),
                    capabilities=capabilities,
                    missing=stage_missing,
                )
            )

        unique_needed = tuple(dict.fromkeys(needed))
        unique_missing = tuple(dict.fromkeys(missing))
        satisfied = tuple(item for item in unique_needed if item not in unique_missing)
        suggestions = tuple(_manager_suggestion(registry, item) for item in unique_missing)
        warnings = list(abstract.get("warnings", ()))
        if concrete_id is None:
            warnings.append("没有匹配的已验证具体模板；已保留方案结构和依赖清单。")
        if unique_missing:
            warnings.append(
                "工作流草稿仍可创建；请通过 ComfyUI Manager 安装、启用、修复或更新依赖后重新扫描。"
            )
        if spec.execution.low_vram:
            warnings.append("低显存模式已启用：优先使用分块 VAE、单批次和较少采样步数。")
        return WorkflowPlan(
            template_id=str(abstract["template_id"]),
            task=spec.task,
            profile=spec.execution.profile,
            concrete_template_id=concrete_id,
            executable=not unique_missing and concrete_id is not None,
            stages=tuple(stages),
            satisfied_capabilities=satisfied,
            missing_capabilities=unique_missing,
            warnings=tuple(warnings),
            manager_suggestions=suggestions,
        )


def _available(registry: RegistrySnapshot, capability: str) -> bool:
    base = capability.split(".", 1)[0]
    status = registry.capabilities.get(base)
    return bool(status and status.available)


def _manager_suggestion(registry: RegistrySnapshot, capability: str) -> dict:
    base = capability.split(".", 1)[0]
    status = registry.capabilities.get(base)
    return {
        "capability": base,
        "candidate_nodes": list(status.candidates if status else ()),
        "search_terms": list(status.search_terms if status else (base,)),
        "recommended_actions": ["install", "enable", "fix", "update", "replace"],
        "continue_as_draft": True,
        "install_requires_confirmation": True,
    }
