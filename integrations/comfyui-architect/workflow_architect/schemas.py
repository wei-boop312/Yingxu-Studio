from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .errors import InputError
from .settings import (
    DEFAULT_FORMAT,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MAX_DIMENSION,
    MAX_PIXELS,
    MIN_DIMENSION,
)


@dataclass(frozen=True)
class InputRequirements:
    person_image: bool = True
    garment_image: bool = True
    source_image: bool = False
    mask_image: bool = False

    def __post_init__(self) -> None:
        for name in ("person_image", "garment_image", "source_image", "mask_image"):
            if not isinstance(getattr(self, name), bool):
                raise InputError("图片输入开关必须是布尔值")


@dataclass(frozen=True)
class PromptRequirements:
    positive: str = ""
    negative: str = "低质量，模糊，畸形，多余肢体，文字，水印"
    seed: int = 0
    steps: int = 24
    cfg: float = 7.0
    denoise: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.positive, str) or not isinstance(self.negative, str):
            raise InputError("正向和负向提示词必须是文本")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise InputError("seed 必须是非负整数")
        if not isinstance(self.steps, int) or not 1 <= self.steps <= 200:
            raise InputError("steps 必须在 1 到 200 之间")
        if not isinstance(self.cfg, (int, float)) or not 0 <= float(self.cfg) <= 30:
            raise InputError("cfg 必须在 0 到 30 之间")
        if not isinstance(self.denoise, (int, float)) or not 0 <= float(self.denoise) <= 1:
            raise InputError("denoise 必须在 0 到 1 之间")


@dataclass(frozen=True)
class PreservationConstraints:
    identity_preservation: str = "normal"
    hair_preservation: bool = False
    pose_preservation: str = "normal"
    garment_fidelity: str = "high"
    background_preservation: bool = False
    protect_hands: bool = True

    def __post_init__(self) -> None:
        if self.identity_preservation not in {"normal", "strict"}:
            raise InputError("identity_preservation 只支持 normal 或 strict")
        if self.pose_preservation not in {"normal", "high"}:
            raise InputError("pose_preservation 只支持 normal 或 high")
        if self.garment_fidelity not in {"normal", "high"}:
            raise InputError("garment_fidelity 只支持 normal 或 high")
        for name in (
            "hair_preservation",
            "background_preservation",
            "protect_hands",
        ):
            if not isinstance(getattr(self, name), bool):
                raise InputError(f"{name} 必须是布尔值")


@dataclass(frozen=True)
class OutputRequirements:
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    format: str = DEFAULT_FORMAT
    upscale: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.width, int)
            or isinstance(self.width, bool)
            or not isinstance(self.height, int)
            or isinstance(self.height, bool)
        ):
            raise InputError("输出宽度和高度必须是整数")
        if not (MIN_DIMENSION <= self.width <= MAX_DIMENSION):
            raise InputError(f"输出宽度必须在 {MIN_DIMENSION} 到 {MAX_DIMENSION} 之间")
        if not (MIN_DIMENSION <= self.height <= MAX_DIMENSION):
            raise InputError(f"输出高度必须在 {MIN_DIMENSION} 到 {MAX_DIMENSION} 之间")
        if self.width * self.height > MAX_PIXELS:
            raise InputError(f"输出总像素不得超过 {MAX_PIXELS}")
        if self.format not in {"png", "jpg", "webp"}:
            raise InputError("输出格式只支持 png、jpg 或 webp")
        if not isinstance(self.upscale, bool):
            raise InputError("upscale 必须是布尔值")


@dataclass(frozen=True)
class ExecutionSettings:
    profile: str = "stable"
    allow_optional_nodes: bool = True
    low_vram: bool = False

    def __post_init__(self) -> None:
        if self.profile not in {"stable", "low_vram"}:
            raise InputError("运行模式只支持 stable 或 low_vram")
        if self.low_vram != (self.profile == "low_vram"):
            raise InputError("low_vram 必须与 profile 保持一致")
        if not isinstance(self.allow_optional_nodes, bool) or not isinstance(
            self.low_vram, bool
        ):
            raise InputError("执行开关必须是布尔值")


@dataclass(frozen=True)
class TaskSpec:
    schema_version: str = "2.0"
    task: str = "virtual_tryon"
    garment_category: str = "upper_body"
    inputs: InputRequirements = field(default_factory=InputRequirements)
    prompt: PromptRequirements = field(default_factory=PromptRequirements)
    constraints: PreservationConstraints = field(default_factory=PreservationConstraints)
    output: OutputRequirements = field(default_factory=OutputRequirements)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)

    def __post_init__(self) -> None:
        from .settings import SUPPORTED_TASKS

        if self.schema_version != "2.0":
            raise InputError("不支持的任务规格版本")
        if self.task not in SUPPORTED_TASKS:
            raise InputError(f"不支持的任务类型: {self.task}")
        if self.task == "virtual_tryon" and self.garment_category != "upper_body":
            raise InputError("虚拟换装目前只支持单人上衣")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        if not isinstance(data, dict):
            raise InputError("任务规格必须是 JSON 对象")
        return cls(
            schema_version="2.0" if str(data.get("schema_version", "2.0")) in {"1.0", "2.0"} else str(data.get("schema_version")),
            task=str(data.get("task", "virtual_tryon")),
            garment_category=str(data.get("garment_category", "upper_body")),
            inputs=InputRequirements(**_known(data.get("inputs", {}), InputRequirements)),
            prompt=PromptRequirements(**_known(data.get("prompt", {}), PromptRequirements)),
            constraints=PreservationConstraints(
                **_known(data.get("constraints", {}), PreservationConstraints)
            ),
            output=OutputRequirements(**_known(data.get("output", {}), OutputRequirements)),
            execution=ExecutionSettings(
                **_known(data.get("execution", {}), ExecutionSettings)
            ),
        )


@dataclass(frozen=True)
class ParseResult:
    recognized: bool
    spec: TaskSpec | None
    confidence: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "recognized": self.recognized,
            "spec": self.spec.to_dict() if self.spec else None,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class InputDefinition:
    name: str
    type: str
    required: bool
    default: Any = None
    choices: tuple[str, ...] = ()
    control_after_generate: bool | str | None = None


@dataclass(frozen=True)
class NodeDefinition:
    class_name: str
    inputs: tuple[InputDefinition, ...]
    outputs: tuple[str, ...]
    output_names: tuple[str, ...] = ()
    category: str = ""

    def input_by_name(self, name: str) -> InputDefinition | None:
        return next((item for item in self.inputs if item.name == name), None)


@dataclass(frozen=True)
class CapabilityStatus:
    name: str
    required: bool
    matched_node: str | None
    candidates: tuple[str, ...]
    search_terms: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.matched_node is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "available": self.available,
            "matched_node": self.matched_node,
            "candidates": list(self.candidates),
            "search_terms": list(self.search_terms),
        }


@dataclass(frozen=True)
class RegistrySnapshot:
    generation: int
    node_count: int
    nodes: dict[str, NodeDefinition]
    capabilities: dict[str, CapabilityStatus]

    def to_dict(self, *, include_nodes: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "generation": self.generation,
            "node_count": self.node_count,
            "capabilities": {
                key: value.to_dict() for key, value in self.capabilities.items()
            },
            "missing_required": [
                key
                for key, value in self.capabilities.items()
                if value.required and not value.available
            ],
        }
        if include_nodes:
            result["nodes"] = {
                key: {
                    "class_name": value.class_name,
                    "inputs": [asdict(item) for item in value.inputs],
                    "outputs": list(value.outputs),
                    "output_names": list(value.output_names),
                    "category": value.category,
                }
                for key, value in self.nodes.items()
            }
        return result


@dataclass(frozen=True)
class PlanStage:
    id: str
    title: str
    capabilities: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowPlan:
    template_id: str
    task: str
    profile: str
    executable: bool
    stages: tuple[PlanStage, ...]
    satisfied_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    warnings: tuple[str, ...]
    concrete_template_id: str | None = None
    manager_suggestions: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "task": self.task,
            "profile": self.profile,
            "concrete_template_id": self.concrete_template_id,
            "creatable": self.concrete_template_id is not None,
            "executable": self.executable,
            "execution_ready": self.executable,
            "stages": [asdict(item) for item in self.stages],
            "satisfied_capabilities": list(self.satisfied_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
            "warnings": list(self.warnings),
            "manager_suggestions": list(self.manager_suggestions),
        }


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    stage: str | None = None
    capability: str | None = None
    node_id: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()
    missing_nodes: tuple[str, ...] = ()
    missing_models: tuple[str, ...] = ()
    draft_created: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "execution_ready": self.valid and not self.missing_nodes and not self.missing_models,
            "draft_created": self.draft_created,
            "errors": [asdict(item) for item in self.errors],
            "warnings": [asdict(item) for item in self.warnings],
            "missing_nodes": list(self.missing_nodes),
            "missing_models": list(self.missing_models),
        }


def _known(data: Any, cls: type) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError(f"{cls.__name__} 必须是 JSON 对象")
    names = set(cls.__dataclass_fields__)
    unknown = sorted(set(data) - names)
    if unknown:
        raise InputError(f"{cls.__name__} 包含未知字段: {', '.join(unknown)}")
    return data
