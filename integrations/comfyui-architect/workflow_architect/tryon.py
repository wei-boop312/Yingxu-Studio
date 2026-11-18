"""Explicit dual-reference Klein graph preparation. Never queue or download."""
import hashlib
import json
from .errors import InputError
from .settings import TEMPLATE_DIR
from .schemas import TaskSpec, InputRequirements, PromptRequirements, OutputRequirements

MODELS = {'diffusion': 'flux-2-klein-4b-fp8.safetensors', 'encoder': 'qwen_3_4b.safetensors', 'vae': 'flux2-vae.safetensors'}
PREFIX = ('Edit image 1 as a neutral adult commercial apparel try-on. Image 1 is the person; image 2 is the garment reference. '
          'Replace the entire original outfit with the reference garment, not a recolor. Keep face, hair, body proportions, pose, hands, feet and scene unchanged. '
          'Keep fully opaque lining over breasts and pelvic area, no visible nipples or genital details. Match garment structure and details. ')

def prepare_tryon(service, payload):
    allowed = {'build_draft', 'expected', 'person_image', 'garment_image', 'authorized', 'prompt', 'width', 'height', 'model_overrides'}
    if not isinstance(payload, dict) or set(payload) - allowed or type(payload.get('build_draft', False)) is not bool:
        raise InputError('换装字段不合法')
    snapshot = service.registry.current()
    path = TEMPLATE_DIR / 'experimental/klein_tryon.json'
    raw = path.read_bytes()
    template = json.loads(raw)
    expected = {'registry_generation': snapshot.generation, 'template_sha256': hashlib.sha256(raw).hexdigest()}
    def choices(kind, name):
        node = snapshot.nodes.get(kind)
        item = node.input_by_name(name) if node else None
        return list(item.choices) if item else []
    missing_nodes = sorted({n['class_type'] for n in template['nodes']} - set(snapshot.nodes))
    missing_models = [MODELS[b['binding']] for b in template['model_bindings'] if MODELS[b['binding']] not in choices(b['class_type'], b['input'])]
    images = choices('LoadImage', 'image')
    if len(images) > 2000:
        raise InputError('input图片选项超过2000，请收敛目录')
    result = {'route': 'klein-tryon-experimental', 'expected': expected, 'missing_nodes': missing_nodes, 'missing_models': missing_models,
              'selection': {'images': images, 'models': dict(MODELS)}, 'compiled': None,
              'execution_authorized': False, 'quality_approved': False,
              'notice': '实验双图换装；两次本机小样可运行，不保证商品花纹、身份、手脚或背景保真。仅编译不运行/下载，手动检查后排队。不会像素锁定。CPU编码较慢。'}
    if not payload.get('build_draft') or missing_nodes or missing_models:
        return result
    if payload.get('expected') != expected:
        raise InputError('节点或模板已变化，请重新检查')
    if payload.get('authorized') is not True or payload.get('model_overrides') != MODELS:
        raise InputError('须确认素材使用权与实验限制并明确选择匹配模型')
    person, garment = payload.get('person_image'), payload.get('garment_image')
    if not isinstance(person, str) or not isinstance(garment, str) or person == garment or person not in images or garment not in images:
        raise InputError('请选择当前input列表中不同的人物图和服装图')
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise InputError('换装描述须为1到2000字符')
    width, height = payload.get('width', 576), payload.get('height', 768)
    if any(type(v) is not int or v < 256 or v > 1024 or v % 16 for v in (width, height)) or width * height > 786432:
        raise InputError('实验画布256到1024、16倍数、最多786432像素')
    spec = TaskSpec(task='image_to_image', garment_category='not_applicable',
                    inputs=InputRequirements(source_image=True),
                    prompt=PromptRequirements(positive=PREFIX + prompt.strip(), steps=4, cfg=1.0),
                    output=OutputRequirements(width=width, height=height))
    for node in template['nodes']:
        if node['key'] in ('person', 'garment'):
            node['inputs']['image'] = person if node['key'] == 'person' else garment
    built = service.compile({'spec': spec.to_dict(), 'concrete_template': template, 'model_overrides': dict(MODELS),
                             'input_overrides': {'source_image': person}, 'allow_draft': False})
    if service.registry.current().generation != snapshot.generation or path.read_bytes() != raw:
        raise InputError('编译期间环境变化，请重新检查')
    result.update(compiled=built['compiled'], validation=built['validation'])
    if result['compiled']:
        metadata = result['compiled']['ui_workflow']['extra']['workflow_architect']
        metadata.update(identity_strategy=None, experimental_tryon={'route': result['route'], 'quality_approved': False, 'pixel_protection': False, 'manual_review_required': True})
    return result
