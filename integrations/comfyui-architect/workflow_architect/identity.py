"""Explicit experimental PhotoMaker static preparation; never load/run/download."""
import json
import hashlib
from .errors import InputError
from .settings import TEMPLATE_DIR
from .schemas import TaskSpec, InputRequirements, PromptRequirements, OutputRequirements

CHECKPOINT = "sd_xl_base_1.0.safetensors"
WEIGHT = "photomaker-v1.bin"
LORA = "workflow_architect_photomaker_v1_lora.safetensors"


def prepare_identity(service, payload):
    allowed = {"build_draft", "prompt", "reference_image", "model_overrides", "portrait_authorized", "width", "height", "expected", "subject", "lora_strength", "steps"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise InputError("未知身份参考字段")
    build = payload.get("build_draft", False)
    if type(build) is not bool:
        raise InputError("build_draft必须为布尔值")
    snapshot = service.registry.current()
    template_path = TEMPLATE_DIR / "experimental/photomaker_v1_reference.json"
    template_bytes = template_path.read_bytes()
    template = json.loads(template_bytes)
    expected = {"registry_generation": snapshot.generation, "template_sha256": hashlib.sha256(template_bytes).hexdigest()}
    def options(node, name):
        definition = snapshot.nodes.get(node)
        entry = definition.input_by_name(name) if definition else None
        return list(entry.choices) if entry else []
    missing_nodes = sorted({node["class_type"] for node in template["nodes"] if node["class_type"] not in snapshot.nodes})
    checkpoint = CHECKPOINT if CHECKPOINT in options("CheckpointLoaderSimple", "ckpt_name") else None
    weight = WEIGHT if WEIGHT in options("PhotoMakerLoader", "photomaker_model_name") else None
    lora = LORA if LORA in options("LoraLoaderModelOnly", "lora_name") else None
    missing_models = ([] if checkpoint else [CHECKPOINT]) + ([] if weight else [WEIGHT]) + ([] if lora else [LORA])
    images = options("LoadImage", "image")
    if len(images) > 2000:
        raise InputError("身份参考图片选项超限，请收敛input目录")
    result = {"route": "photomaker-v1-native", "registry_generation": snapshot.generation,
              "status": "dependency-required" if missing_nodes or missing_models else "explicit-selection-required",
              "missing_nodes": missing_nodes, "missing_models": missing_models,
              "selection": {"checkpoints": [checkpoint] if checkpoint else [], "photomaker": [weight] if weight else [], "lora": [lora] if lora else [], "reference_images": images},
              "expected": expected,
              "compiled": None, "execution_authorized": False, "runtime_verified": False, "quality_approved": False,
              "notice": "独立实验分支，仅静态准备；本机小样运行完成但人物质量未通过，不用于生产。不加载模型/推理/下载或消费API。"}
    if not build or missing_nodes or missing_models:
        return result
    if payload.get("expected") != expected:
        raise InputError("身份参考模板或节点清单变化，请重新检查依赖")
    if payload.get("portrait_authorized") is not True:
        raise InputError("请明确确认参考肖像已获授权")
    models = payload.get("model_overrides")
    if models != {"checkpoint": CHECKPOINT, "photomaker": WEIGHT, "identity_lora": LORA}:
        raise InputError("必须明确选择当前路线匹配的SDXL与PhotoMaker v1，不自动替换模型")
    reference = payload.get("reference_image")
    if not isinstance(reference, str) or reference not in options("LoadImage", "image"):
        raise InputError("请选择当前LoadImage列表中的身份参考原图")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise InputError("画面描述须为1到2000字符")
    if "photomaker" in prompt.lower().split():
        raise InputError("触发词由专用模板插入，请不要在描述中重复photomaker")
    subject = payload.get("subject", "person")
    strength = payload.get("lora_strength", 0.6)
    steps = payload.get("steps", 24)
    if subject not in ("person", "man", "woman"):
        raise InputError("主体词只允许person/man/woman，不由参考图推断身份或性别")
    if type(strength) not in (int, float) or not 0.0 <= strength <= 1.0:
        raise InputError("实验LoRA强度须在0到1之间")
    if type(steps) is not int or not 16 <= steps <= 32:
        raise InputError("实验采样步数仅允许16到32")
    width, height = payload.get("width", 512), payload.get("height", 768)
    if any(type(value) is not int or not 64 <= value <= 1024 or value % 8 for value in [width, height]) or width * height > 1024 * 1024:
        raise InputError("此实验分支仅允许64到1024、8倍数、最多1048576像素的单张画布")
    # Dedicated branch projects to the existing compiler spec without advertising
    # a new generic identity task or leaking this template into normal T2I search.
    spec = TaskSpec(task="text_to_image", garment_category="not_applicable",
                    inputs=InputRequirements(person_image=False, garment_image=False, source_image=True),
                    prompt=PromptRequirements(positive="a photograph of a " + subject + " photomaker " + prompt.strip(),
                                              negative="blurry, distorted face, deformed, sunglasses, cropped face, out of frame, multiple people, text, watermark", steps=steps, cfg=5.0),
                    output=OutputRequirements(width=width, height=height))
    for node in template["nodes"]:
        if node["key"] == "identity_lora":
            node["inputs"]["strength_model"] = float(strength)
    built = service.compile({"spec": spec.to_dict(), "concrete_template": template, "model_overrides": models,
                             "input_overrides": {"source_image": reference}, "allow_draft": False})
    if service.registry.current().generation != snapshot.generation or hashlib.sha256(template_path.read_bytes()).hexdigest() != expected["template_sha256"]:
        raise InputError("准备期间节点清单变化，请重新检查")
    result["compiled"] = built["compiled"]
    result["validation"] = built["validation"]
    result["status"] = "static-compiled-not-runtime-verified" if built["compiled"] else "validation-rejected"
    if built["compiled"]:
        metadata = built["compiled"]["ui_workflow"]["extra"]["workflow_architect"]
        metadata["identity_reference"] = {"route": result["route"], "trigger_word": "photomaker", "subject": subject, "lora_strength": strength, "runtime_verified": False, "quality_approved": False, "execution_authorized": False}
    return result
