from __future__ import annotations

from typing import Any
import hashlib
import json

from .compiler import WorkflowCompiler, _model_id
from .ecommerce import fit_product_layout, source_image_dimensions
from .errors import InputError
from .parser import RequirementParser
from .people import people_request, people_dependencies
from .planner import WorkflowPlanner
from .registry import CapabilityRegistry
from .schemas import TaskSpec
from .validator import WorkflowValidator


class ArchitectService:
    def __init__(self) -> None:
        self.parser = RequirementParser()
        self.registry = CapabilityRegistry()
        self.planner = WorkflowPlanner()
        self.compiler = WorkflowCompiler()
        self.validator = WorkflowValidator()
        self.api_provider = None

    def api_support(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .provider_support import validate_profile
        from .knowledge import _sha
        from .api_budget import REQUESTED_PROFILE,CAP_MICRO_USD
        from .credentials import local_vault_status
        if payload:
            raise InputError('状态读取不接受配置或密钥')
        provider = self.api_provider
        return {'configured': provider is not None, 'connection_verified': False,'local_credential':local_vault_status(),
                'status': 'configured-not-live-verified' if provider else 'not-configured',
                'protocols': ['chat_completions', 'responses'],
                'local_modes': ['manual-chatgpt-exchange', 'local-knowledge-and-rules'],
                'local_language_model_loaded': False, 'image_upload_enabled': False,
                'profile': validate_profile(provider.profile) if provider else None,
                'profile_digest': _sha(validate_profile(provider.profile)) if provider else None,
                'requested_profile': dict(REQUESTED_PROFILE), 'authorized_per_call_micro_usd': CAP_MICRO_USD,
                'price_and_account_limit_verified': False,
                'notice': '声明协议不等于站点/模型已验证；token上限不是账单金额硬上限。缺精确目标/费用批准/安全凭证端口时不联网。'}

    def api_profile_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .provider_support import validate_profile, build_request
        if not isinstance(payload, dict) or set(payload) != {'text', 'profile_config'}:
            raise InputError('仅接收需求和非密钥配置，不保存或发送')
        profile = validate_profile(payload['profile_config'])
        context = self.planning_context({'text': payload['text'], 'profile': 'stable'})
        raw = build_request(context, profile)
        return {'profile': profile, 'request_bytes': len(raw), 'context_digest': context['context_digest'],
                'configured': False, 'sent': False, 'credential_read': False, 'cost_authorized': False}

    def propose_api(self, payload: dict[str, Any], cancel=None) -> dict[str, Any]:
        provider = self.api_provider
        if provider is None:
            raise InputError('API未配置：需要准确模型ID/完整接口/费用批准与可信凭证端口；不读取浏览器或环境密钥')
        return provider.propose(self, payload, cancel)

    def refresh(self, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.registry.refresh(object_info).to_dict()

    def research_catalog(self) -> dict[str, Any]:
        """Offline metadata only. These recipes are not compiler templates."""
        from .research import ResearchCatalog

        return ResearchCatalog().search()

    def prepare_identity_reference(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .identity import prepare_identity
        return prepare_identity(self, payload)

    def prepare_tryon(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .tryon import prepare_tryon
        return prepare_tryon(self, payload)

    def foundation_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .foundation import foundation_report
        return foundation_report(self, payload)

    def planning_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .api_planner import planning_context
        return planning_context(self, payload)

    def detail_context(self,payload):
        from .detail_page import context
        return context(self,payload)

    def detail_validate(self,payload):
        from .detail_page import validate
        return validate(self,payload)

    def detail_compile(self,payload):
        from .detail_page import compile_page
        return compile_page(self,payload)

    def detail_propose(self,payload,cancel=None):
        from .detail_page import propose_api
        return propose_api(self,payload,cancel)

    def poster_options(self,payload):
        if not isinstance(payload,dict) or payload:raise InputError('素材选项读取不接受配置或密钥')
        from .poster_service import options
        return options(self)

    def poster_prepare(self,payload,cancel=None):
        from .poster_service import prepare
        return prepare(self,payload,cancel)

    def validate_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .api_planner import validate_proposal
        return validate_proposal(self, payload)

    def compile_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .plan_compile import compile_plan
        return compile_plan(self, payload)

    def knowledge_assist(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Local retrieval -> trusted template -> existing compiler, never execute."""
        from .knowledge import KnowledgeBase

        allowed = {"text", "options", "build_draft", "model_overrides", "input_overrides", "expected"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise InputError("未知知识辅助字段")
        build = payload.get("build_draft", False)
        if type(build) is not bool:
            raise InputError("build_draft必须为布尔值")
        parsed = self.parse({"text": payload.get("text"), "options": payload.get("options", {})})
        task = parsed["spec"]["task"] if parsed.get("recognized") and parsed.get("spec") else None
        if parsed.get("person_request", {}).get("reference_only"):
            task = None
        profile = parsed["spec"]["execution"]["profile"] if task else "stable"
        # Disposable in-memory SQLite per request. Explicit offline builders can
        # use a persistent owned DB; no startup writes to the user's Comfy root.
        knowledge = KnowledgeBase()
        try:
            snapshot = self.registry.current()
            retrieval = knowledge.search(payload.get("text"), snapshot, task=task, profile=profile)
            eligible = [d for d in retrieval["results"] if d["kind"] == "template" and d["compatibility"]["status"] == "dependencies-listed-not-authorized"]
            selected = eligible[0] if eligible else None
            result = {"parsed": parsed, "retrieval": retrieval, "selected_template": selected["id"] if selected else None,
                      "compiled": None, "status": "reference-only" if not selected else "template-candidate",
                      "execution_authorized": False, "api_enabled": False, "embedding_enabled": False}
            result["person_request"] = parsed.get("person_request")
            if result["person_request"]:
                result["person_request"]["template_available"] = selected is not None
                result["person_request"]["dependency_report"] = people_dependencies(result["person_request"], snapshot)
            expected = {"corpus_revision": knowledge.revision, "registry_generation": snapshot.generation,
                        "template_id": selected["id"] if selected else None,
                        "requirement_sha256": hashlib.sha256(json.dumps({"text": payload["text"], "options": payload.get("options", {})}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
            result["expected"] = expected
            result["selection"] = {"models": [], "source_image_required": False, "source_images": []}
            if build and (not isinstance(payload.get("expected"), dict) or payload["expected"] != expected):
                raise InputError("需求、知识版本或节点登记已变化；请重新检索并核对选择")
            if selected:
                template = knowledge.template(selected["id"].split(":", 1)[1])
                source_required = parsed["spec"]["inputs"]["source_image"]
                image_node = snapshot.nodes.get("LoadImage")
                image_entry = image_node.input_by_name("image") if image_node else None
                images = list(image_entry.choices) if image_entry and source_required else []
                model_choices = []
                for binding in template.get("model_bindings", []):
                    entry = snapshot.nodes[binding["class_type"]].input_by_name(binding["input"])
                    choices = list(entry.choices) if entry else []
                    if len(choices) > 2000:
                        raise InputError("模型选择数量超限，请收敛节点列表")
                    model_choices.append({"binding": binding["binding"], "label": binding.get("label", binding["binding"]), "choices": choices})
                if len(images) > 2000:
                    raise InputError("图片选择数量超限，请收敛input目录")
                result["selection"] = {"models": model_choices, "source_image_required": source_required, "source_images": images}
            if not build or not selected:
                return result
            template = knowledge.template(selected["id"].split(":", 1)[1])
            models = payload.get("model_overrides", {})
            inputs = payload.get("input_overrides", {})
            if not isinstance(models, dict) or not isinstance(inputs, dict):
                raise InputError("模型和输入选择必须是对象")
            if set(inputs) - {"source_image"}:
                raise InputError("未知知识辅助输入选择")
            bindings = {m["binding"]: m for m in template.get("model_bindings", [])}
            if set(models) - set(bindings):
                raise InputError("模型选择不属于检索到的模板")
            if set(bindings) - set(models) or (parsed["spec"]["inputs"]["source_image"] and not inputs.get("source_image")):
                result["status"] = "selection-required"
                result["required_model_bindings"] = sorted(bindings)
                result["source_image_required"] = parsed["spec"]["inputs"]["source_image"]
                return result
            for name, binding in bindings.items():
                node = self.registry.current().nodes.get(binding["class_type"])
                definition = node.input_by_name(binding["input"]) if node else None
                if not isinstance(models[name], str) or definition is None or models[name] not in definition.choices:
                    raise InputError("所选模型不在当前节点的真实选择列表")
            if parsed["spec"]["inputs"]["source_image"] and inputs["source_image"] not in result["selection"]["source_images"]:
                raise InputError("所选图片不在当前LoadImage的真实选择列表")
            if task == "ecommerce_detail" and source_image_dimensions(inputs.get("source_image", "")) is None:
                raise InputError("商品布局必须读取所选原图尺寸，不能使用猜测尺寸")
            compiled = self.compile({"spec": parsed["spec"], "concrete_template": template,
                                     "model_overrides": models, "input_overrides": inputs, "allow_draft": True})
            result["compiled"] = compiled["compiled"]
            if self.registry.current().generation != snapshot.generation:
                raise InputError("编译期间节点登记变化，请重新检索")
            if result["compiled"]:
                result["compiled"]["ui_workflow"]["extra"]["workflow_architect"]["knowledge"] = {
                    **expected, "document_sha256": selected["document_sha256"],
                    "retrieval": retrieval["retrieval"], "api_called": False,
                    "notice": "Static compilation evidence, not image quality or execution authorization"}
            result["validation"] = compiled["validation"]
            result["status"] = "compiled-not-executed" if compiled["compiled"] else "validation-rejected"
            return result
        finally:
            knowledge.close()

    def parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = payload.get("text")
        options = payload.get("options", {})
        if not isinstance(options, dict):
            raise InputError("options 必须是对象")
        allowed = {
            "width_override",
            "height_override",
            "strict_identity",
            "preserve_background",
            "profile",
        }
        unknown = set(options) - allowed
        if unknown:
            raise InputError(f"未知解析选项: {', '.join(sorted(unknown))}")
        result = self.parser.parse(text, **options).to_dict()
        person = people_request(text)
        if person:
            result["person_request"] = person
        return result

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = TaskSpec.from_dict(payload.get("spec", {}))
        concrete = self._concrete(payload, spec)
        plan = self.planner.plan(spec, self.registry.current(), concrete_template=concrete)
        report = self.validator.validate_plan(plan)
        return {"plan": plan.to_dict(), "validation": report.to_dict()}

    def compile(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = TaskSpec.from_dict(payload.get("spec", {}))
        concrete = self._concrete(payload, spec)
        plan = self.planner.plan(spec, self.registry.current(), concrete_template=concrete)
        preflight = self.validator.validate_plan(plan, require_executable=True)
        if concrete is None:
            return {
                "compiled": None,
                "plan": plan.to_dict(),
                "validation": preflight.to_dict(),
            }
        runtime_bindings, missing_runtime_models = self._runtime_bindings(
            concrete, payload.get("model_overrides")
        )
        runtime_bindings.update(
            self._input_bindings(spec, payload.get("input_overrides"))
        )
        if 'ecommerce_prompt_overrides' in payload:
            from .detail_page import visual_prompts
            if spec.task!='ecommerce_detail':raise InputError('背景提示词只用于商品场景模板')
            prompts=visual_prompts(payload['ecommerce_prompt_overrides'])
            runtime_bindings.update(background_positive_prompt=prompts['positive'],background_negative_prompt=prompts['negative'])
        installed_models = self._installed_models(concrete, payload.get("installed_models"))
        required_models = {
            _model_id(item) for item in concrete.get("required_models", [])
        }
        missing_models = sorted(
            set(missing_runtime_models) | (required_models - installed_models)
        )
        allow_draft = bool(payload.get("allow_draft", True))
        compiled = self.compiler.compile(
            spec,
            plan,
            self.registry.current(),
            concrete,
            installed_models=installed_models,
            runtime_bindings=runtime_bindings,
            allow_draft=allow_draft,
        )
        if missing_models:
            compiled["missing_models"] = missing_models
            compiled["dependency_report"]["missing_models"] = missing_models
            compiled["draft"] = True
            compiled["execution_ready"] = False
            metadata = compiled["ui_workflow"]["extra"]["workflow_architect"]
            metadata["draft"] = True
            metadata["execution_ready"] = False
            metadata["missing_models"] = missing_models
        suggestions = list(compiled["dependency_report"]["manager_suggestions"])
        known_candidates = {
            candidate
            for item in suggestions
            for candidate in item.get("candidate_nodes", [])
        }
        for class_name in compiled.get("missing_nodes", []):
            if class_name in known_candidates:
                continue
            suggestions.append(
                {
                    "capability": class_name,
                    "candidate_nodes": [class_name],
                    "search_terms": [class_name],
                    "recommended_actions": [
                        "install",
                        "enable",
                        "fix",
                        "update",
                        "replace",
                    ],
                    "continue_as_draft": True,
                    "install_requires_confirmation": True,
                }
            )
        compiled["dependency_report"]["manager_suggestions"] = suggestions
        report = self.validator.validate_compiled(compiled, self.registry.current())
        return {
            "compiled": compiled if report.valid or compiled.get("draft") else None,
            "plan": plan.to_dict(),
            "validation": report.to_dict(),
        }

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        compiled = payload.get("compiled")
        if not isinstance(compiled, dict):
            raise InputError("compiled 必须是对象")
        return self.validator.validate_compiled(
            compiled, self.registry.current()
        ).to_dict()

    def manager_status(self) -> dict[str, Any]:
        available = False
        try:
            import nodes as comfy_nodes  # type: ignore

            web_dirs = getattr(comfy_nodes, "EXTENSION_WEB_DIRS", {}) or {}
            available = any(
                "manager" in f"{name} {path}".lower()
                for name, path in web_dirs.items()
            )
        except ImportError:
            pass
        return {
            "available": available,
            "search_endpoint": "/customnode/getmappings?mode=cache",
            "list_endpoint": "/customnode/getlist?mode=cache",
            "install_requires_confirmation": True,
            "automatic_install": False,
            "repair_actions": ["install", "enable", "fix", "update", "replace"],
            "workflow_creation_blocked_by_missing_nodes": False,
        }

    def _concrete(
        self,
        payload: dict[str, Any],
        spec: TaskSpec,
    ) -> dict[str, Any] | None:
        supplied = payload.get("concrete_template")
        if supplied is not None:
            return supplied
        compatible = self.planner.templates.find_compatible_concrete(
            self.registry.current(),
            spec.task,
            spec.execution.profile,
        )
        return compatible or self.planner.templates.find_concrete_for_draft(
            spec.task,
            spec.execution.profile,
        )

    def _runtime_bindings(
        self,
        concrete: dict[str, Any],
        supplied: Any,
    ) -> tuple[dict[str, Any], list[str]]:
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            raise InputError("model_overrides 必须是对象")
        bindings: dict[str, Any] = {}
        missing: list[str] = []
        snapshot = self.registry.current()
        for item in concrete.get("model_bindings", []):
            if not isinstance(item, dict):
                continue
            binding = str(item.get("binding", "")).strip()
            class_type = str(item.get("class_type", "")).strip()
            input_name = str(item.get("input", "")).strip()
            label = str(item.get("label", binding or "模型"))
            if not binding or not class_type or not input_name:
                continue
            definition = snapshot.nodes.get(class_type)
            input_definition = (
                definition.input_by_name(input_name) if definition else None
            )
            value = supplied.get(binding)
            choices = input_definition.choices if input_definition else ()
            if value is None and choices:
                value = choices[0]
            if value is None and input_definition is not None:
                value = input_definition.default
            if value is None or (choices and str(value) not in choices):
                missing.append(f"{label}: {value or '未安装'}")
                bindings[binding] = f"__MISSING_MODEL__:{binding}"
                continue
            bindings[binding] = value
        return bindings, missing

    def _input_bindings(
        self,
        spec: TaskSpec,
        supplied: Any,
    ) -> dict[str, Any]:
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            raise InputError("input_overrides 必须是对象")
        unknown = set(supplied) - {"source_image"}
        if unknown:
            raise InputError(f"未知输入覆盖项: {', '.join(sorted(unknown))}")

        definition = self.registry.current().nodes.get("LoadImage")
        image_input = definition.input_by_name("image") if definition else None
        choices = image_input.choices if image_input else ()
        selected = supplied.get("source_image")
        if selected is None and choices:
            selected = choices[0]
        if spec.inputs.source_image and (
            not isinstance(selected, str) or (choices and selected not in choices)
        ):
            raise InputError("请选择 ComfyUI input 目录中存在的商品图")
        if not isinstance(selected, str):
            return {}

        bindings: dict[str, Any] = {"source_image": selected}
        if spec.task != "ecommerce_detail":
            return bindings
        dimensions = source_image_dimensions(selected)
        if dimensions is None:
            dimensions = (spec.output.width, spec.output.height)
        bindings.update(
            fit_product_layout(
                dimensions[0],
                dimensions[1],
                spec.output.width,
                spec.output.height,
            )
        )
        return bindings

    @staticmethod
    def _installed_models(
        concrete: dict[str, Any],
        supplied: Any,
    ) -> set[str]:
        if isinstance(supplied, list):
            return {str(item) for item in supplied if isinstance(item, str)}
        allowed_folders = {
            "checkpoints",
            "diffusion_models",
            "unet",
            "vae",
            "clip",
            "controlnet",
            "loras",
            "style_models",
        }
        try:
            import folder_paths  # type: ignore
        except ImportError:
            return set()
        installed: set[str] = set()
        for item in concrete.get("required_models", []):
            if not isinstance(item, dict):
                continue
            folder = str(item.get("folder", ""))
            name = str(item.get("name", ""))
            if folder not in allowed_folders or not name:
                continue
            try:
                names = folder_paths.get_filename_list(folder)
            except Exception:
                continue
            if name in names:
                installed.add(f"{folder}:{name}")
        return installed
