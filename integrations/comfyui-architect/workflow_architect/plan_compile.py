"""Compile only a freshly revalidated grounded proposal. Never execute."""
from .errors import InputError
from .knowledge import KnowledgeBase, _sha
from .tryon import MODELS
from .ecommerce import source_image_dimensions

def compile_plan(service,payload):
    allowed={'text','profile','proposal','build','authorized','models','images','prompt','width','height','repair_attempt','feedback_code','background_prompt'}
    if not isinstance(payload,dict) or set(payload)-allowed or type(payload.get('build',False)) is not bool:
        raise InputError('编译字段不合法，不接受任意工作流或命令')
    attempt=payload.get('repair_attempt',0)
    feedback=payload.get('feedback_code')
    if type(attempt) is not int or not 0<=attempt<=2 or feedback not in (None,'INPUT_SELECTION','MODEL_SELECTION','DIMENSIONS','PROMPT') or (attempt>0 and feedback is None):
        raise InputError('每次修订编号只允许0至2，且须明确有限修订类型；不会自动重试')
    accepted=service.validate_proposal({k:payload[k] for k in ('text','profile','proposal') if k in payload})
    selected=accepted['selected_template']; snapshot=service.registry.current()
    experiment=selected['template_id']=='klein_dual_reference_tryon_experimental_v1'
    image_node=snapshot.nodes.get('LoadImage');image_input=image_node.input_by_name('image') if image_node else None
    images=list(image_input.choices) if image_input else []
    if len(images)>2000:raise InputError('input图片选项超过2000')
    template=None;spec=None
    if experiment:
        preview=service.prepare_tryon({})
        model_fields=[{'binding':key,'choices':[name]} for key,name in MODELS.items()]
        roles=['person_image','garment_image']
    else:
        kb=KnowledgeBase()
        try:template=kb.template(selected['template_id'].removeprefix('template:'))
        finally:kb.close()
        parsed=service.parse({'text':payload['text'],'options':{'profile':payload.get('profile','stable')}})
        spec=parsed.get('spec')
        if not spec or template['task']!=spec['task']:raise InputError('模板与需求任务不一致')
        roles=['source_image'] if spec['inputs']['source_image'] else []
        model_fields=[]
        for binding in template.get('model_bindings',[]):
            node=snapshot.nodes.get(binding['class_type']);item=node.input_by_name(binding['input']) if node else None
            choices=list(item.choices) if item else []
            if len(choices)>512:raise InputError('模型选项超过512')
            model_fields.append({'binding':binding['binding'],'choices':choices})
    task='virtual_tryon' if experiment else spec['task']
    output={'width':576,'height':768} if experiment else {k:spec['output'][k] for k in ('width','height')}
    prompt_default=('Match reference garment structure, color, fabric and pattern. Keep original person and background. Fully opaque lining.'
                    if experiment else 'Preserve the source; resize only.' if task=='image_resize' else '')
    result={'phase':3,'status':'selection-required','compiled':None,'execution_authorized':False,'quality_approved':False,
            'proposal_sha256':accepted['proposal_sha256'],'selection':{'models':model_fields,'image_roles':roles,'images':images if roles else [],
            'task':task,'output':output,'prompt_default':prompt_default},
            'repair_attempt':attempt,'notice':'只编译受控模板，不排队、不修复任意图、不保证质量；后续运行失败先停止，最多两次显式参数修订。'}
    if not payload.get('build'):return result
    if payload.get('authorized') is not True:raise InputError('须确认素材授权、实验限制与仅编译')
    models=payload.get('models');inputs=payload.get('images')
    if not isinstance(models,dict) or set(models)!={m['binding'] for m in model_fields}:
        raise InputError('须明确选择全部模型，不默认取第一项')
    if any(not isinstance(models[m['binding']],str) or models[m['binding']] not in m['choices'] for m in model_fields):
        raise InputError('所选模型不在当前匹配选项')
    if not isinstance(inputs,dict) or set(inputs)!=set(roles) or any(not isinstance(v,str) or v not in images for v in inputs.values()):
        raise InputError('须明确选择当前列表中所需原图')
    if experiment and inputs['person_image']==inputs['garment_image']:raise InputError('人物图与服装图不能相同')
    width,height=payload.get('width',576),payload.get('height',768)
    if any(type(v) is not int or v<256 or v>1024 or v%16 for v in (width,height)) or width*height>786432:
        raise InputError('画布须256到1024、16倍数、最多786432像素')
    prompt=payload.get('prompt')
    if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=2000:raise InputError('提示词须1到2000字符')
    if experiment:
        if 'background_prompt' in payload:raise InputError('换装模板不接受商品场景背景覆盖')
        built=service.prepare_tryon({'build_draft':True,'expected':preview['expected'],'authorized':True,'model_overrides':models,
                                    'person_image':inputs['person_image'],'garment_image':inputs['garment_image'],
                                    'prompt':prompt,'width':width,'height':height})
    else:
        if spec['task']=='ecommerce_detail' and source_image_dimensions(inputs.get('source_image','')) is None:
            raise InputError('商品布局必须读取所选原图尺寸，不使用猜测尺寸')
        spec['output'].update(width=width,height=height);spec['prompt']['positive']=prompt.strip()
        extra={}
        if 'background_prompt' in payload:
            if spec['task']!='ecommerce_detail':raise InputError('背景提示词只适用于商品场景')
            extra['ecommerce_prompt_overrides']=payload['background_prompt']
        built=service.compile({'spec':spec,'concrete_template':template,'model_overrides':models,'input_overrides':inputs,'allow_draft':False,**extra})
    compiled=built.get('compiled');validation=built.get('validation')
    if not compiled or not validation or not validation['valid'] or compiled.get('draft'):
        result.update(status='validation-rejected',validation=validation);return result
    final=service.validate_proposal({k:payload[k] for k in ('text','profile','proposal') if k in payload})
    if service.registry.current().generation!=snapshot.generation or final['proposal_sha256']!=accepted['proposal_sha256']:
        raise InputError('编译期间环境/方案变化，请重新核验')
    ui=compiled['ui_workflow']
    if not ui.get('nodes') or not ui.get('links'):raise InputError('编译结果不是非空ComfyUI工作流')
    ui['extra']['workflow_architect']['planning']={'proposal_sha256':accepted['proposal_sha256'],'context_digest':payload['proposal']['context_digest'],
                                                 'repair_attempt':attempt,'feedback_code':feedback,'execution_authorized':False}
    result.update(status='workflow-compiled-not-executed',compiled=compiled,validation=validation,workflow_sha256=_sha(ui))
    return result
