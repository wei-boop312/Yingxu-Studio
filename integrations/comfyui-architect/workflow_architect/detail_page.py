"""Six-section product page: grounded copy, controlled templates, no execution."""
import copy
import json
from .errors import InputError
from .knowledge import _sha

MODULES=[('hero','商品展示'),('benefits','核心卖点'),('details','商品细节'),('scene','使用场景'),('specs','规格参数'),('closing','商品总览')]
SCENES={'ivory':'奶油白柔光留白','white':'白色柔光留白','dark':'深色高级柔光留白','blue':'科技蓝柔光留白','pink':'浅粉柔光留白','interior':'家居生活方式柔光留白'}
QUERY='制作电商主图详情页'

def visual_prompts(value):
    if not isinstance(value,dict) or set(value)!={'positive','negative'}:raise InputError('视觉提示词须完整positive/negative，不接受工作流或命令字段')
    for field,maximum in [('positive',1800),('negative',1200)]:
        text=value[field]
        if not isinstance(text,str) or not 1<=len(text.strip())<=maximum or any(ord(c)<32 and c!='\n' for c in text):raise InputError('视觉提示词为空、超限或含非法字符')
    return {key:text.strip() for key,text in value.items()}

def product_value(value):
    if not isinstance(value,dict) or set(value)!={'name','facts'}:raise InputError('商品资料仅接受name/facts')
    name=value['name'];facts=value['facts']
    if not isinstance(name,str) or not 1<=len(name.strip())<=60:raise InputError('商品名须1到60字')
    if not isinstance(facts,list) or not 1<=len(facts)<=12:raise InputError('须提供1到12条真实资料，含规格请自行填写')
    if any(not isinstance(f,str) or not 1<=len(f.strip())<=100 or any(ord(c)<32 for c in f) for f in facts):raise InputError('每条事实须1到100字且不含控制字符')
    return {'name':name.strip(),'facts':[{'id':'F'+str(i+1),'text':f.strip()} for i,f in enumerate(facts)]}

def context(service,payload):
    if not isinstance(payload,dict) or set(payload)!={'product'}:raise InputError('详情上下文只接受product')
    product=product_value(payload['product'])
    grounded=service.planning_context({'text':QUERY})
    selected=next((c for c in grounded['context']['candidates'] if c['template_id']=='template:core_ecommerce_detail_hybrid_v1'),None)
    if not selected:raise InputError('当前缺商品场景模板/模型/节点，请先扫描并补齐依赖')
    basis={'schema_version':2,'product':product,'modules':[{'id':i,'title':t} for i,t in MODULES],
           'optional_visual_prompt':{'positive':'English background/environment-only prompt, maximum 1800 characters','negative':'English exclusions, maximum 1200 characters'},
           'scenes':SCENES,'template_context_digest':grounded['context_digest']}
    digest=_sha(basis)
    plan={'context_digest':digest,'modules':[{'id':i,'scene':scene,'fact_ids':[f['id'] for f in product['facts'][:4]]} for (i,_),scene in zip(MODULES,['ivory','white','dark','interior','white','blue'])]}
    prompt='只返回JSON对象，按default_plan结构。六模块顺序和id不可变；scene只能选scenes键；fact_ids只能选product内ID，每模块1到4条。每模块可加visual_prompt:{positive,negative}：详细英文场景提示词，明确构图留白、相机、材质、灯光、色彩与禁用元素；实际商品由原图合成，不让模型生成商品、Logo、接口、参数文字。positive最多1800字，negative最多1200字。不新增广告事实/价格/功效/规格，不返回工作流或执行命令。提示词不证明质量，此为待人工确认方案。\n'+json.dumps({'context_digest':digest,'context':basis,'default_plan':plan},ensure_ascii=False)
    snapshot=service.registry.current();fields=[]
    for binding,cls,field in [('checkpoint','CheckpointLoaderSimple','ckpt_name'),('background_removal_model','LoadBackgroundRemovalModel','bg_removal_name')]:
        choices=list(snapshot.nodes[cls].input_by_name(field).choices)
        if len(choices)>512:raise InputError('模型选项超限')
        fields.append({'binding':binding,'choices':choices})
    images=list(snapshot.nodes['LoadImage'].input_by_name('image').choices)
    if len(images)>2000:raise InputError('图片选项超限')
    return {'context_digest':digest,'context':basis,'default_plan':plan,'manual_prompt':prompt,
            'selection':{'models':fields,'images':images},'execution_authorized':False,'api_called':False}

def validate(service,payload):
    if not isinstance(payload,dict) or set(payload)!={'product','plan'}:raise InputError('详情校验只接受product/plan')
    bundle=context(service,{'product':payload['product']});plan=payload['plan']
    if not isinstance(plan,dict) or set(plan)!={'context_digest','modules'} or plan['context_digest']!=bundle['context_digest']:raise InputError('资料或模板已变化，方案不可沿用')
    if not isinstance(plan['modules'],list) or len(plan['modules'])!=6:raise InputError('必须恰好六个有序模块')
    facts={f['id']:f['text'] for f in bundle['context']['product']['facts']};sections=[]
    for (ident,title),module in zip(MODULES,plan['modules']):
        if not isinstance(module,dict) or not {'id','scene','fact_ids'}<=set(module) or set(module)-{'id','scene','fact_ids','visual_prompt'} or module['id']!=ident or not isinstance(module['scene'],str) or module['scene'] not in SCENES:raise InputError('模块顺序或场景不合法')
        ids=module['fact_ids']
        if not isinstance(ids,list) or not 1<=len(ids)<=4 or any(not isinstance(f,str) or f not in facts for f in ids) or len(set(ids))!=len(ids):raise InputError('文案必须引用已提供事实，不允许编造')
        section={'id':ident,'title':title,'scene':module['scene'],'copy':[facts[f] for f in ids]}
        if 'visual_prompt' in module:section['visual_prompt']=visual_prompts(module['visual_prompt'])
        sections.append(section)
    return {'plan':copy.deepcopy(plan),'plan_sha256':_sha(plan),'product_name':bundle['context']['product']['name'],
            'sections':sections,'execution_authorized':False,'commercial_quality_approved':False}

def compile_page(service,payload):
    required={'product','plan','models','image','confirmed'}
    if not isinstance(payload,dict) or not required<=set(payload) or set(payload)-required-{'module_images'} or payload['confirmed'] is not True:raise InputError('须完整资料、绑定图片模型并人工确认')
    module_images=payload.get('module_images')
    if module_images is not None and (not isinstance(module_images,dict) or set(module_images)!={i for i,_ in MODULES} or any(not isinstance(v,str) for v in module_images.values())):raise InputError('逐模块图片必须明确完整六个模块绑定')
    accepted=validate(service,{k:payload[k] for k in ('product','plan')});generation=service.registry.current().generation
    baseline=service.planning_context({'text':QUERY});candidate=next(c for c in baseline['context']['candidates'] if c['template_id']=='template:core_ecommerce_detail_hybrid_v1')
    proposal={'context_digest':baseline['context_digest'],'template_id':candidate['template_id'],'evidence_ids':candidate['evidence_ids'],
              'reason':'人工确认的固定六模块详情页模板','steps':[{'title':'商品图与场景双分支合成','node_classes':candidate['node_classes']}]}
    jobs=[]
    for index,section in enumerate(accepted['sections']):
        detailed={'background_prompt':section['visual_prompt']} if 'visual_prompt' in section else {}
        built=service.compile_plan({'text':QUERY,'proposal':proposal,'build':True,'authorized':True,'models':payload['models'],
            'images':{'source_image':module_images[section['id']] if module_images is not None else payload['image']},'prompt':SCENES[section['scene']]+'商品场景背景，预留标题区域，不生成文字',
            'width':512,'height':640,**detailed})
        if not built.get('compiled') or not built['validation']['valid']:raise InputError('模块未通过真实工作流校验，不导出部分批次')
        compiled=built['compiled']
        jobs.append({'id':section['id'],'section':section,'api_prompt':compiled['api_prompt'],'ui_workflow':compiled['ui_workflow']})
    final=validate(service,{k:payload[k] for k in ('product','plan')})
    if final['plan_sha256']!=accepted['plan_sha256'] or generation!=service.registry.current().generation:raise InputError('编译期间环境变化，不提交批次')
    return {**accepted,'schema_version':1,'jobs':jobs,'job_count':6,'bundle_sha256':_sha({'plan':accepted['plan_sha256'],'jobs':[{'id':j['id'],'api_prompt':j['api_prompt']} for j in jobs]}),
            'executed':False,'notice':'仅编译；每张两次采样，共十二次采样。需人工确认后逐张本机运行，失败不自动重试；文字在输出后排版。'}

def propose_api(service,payload,cancel=None):
    allowed={'product','context_digest','profile_digest','confirm_external_text','confirm_one_paid_call'}
    if not isinstance(payload,dict) or set(payload)!=allowed:raise InputError('详情API规划字段不合法')
    if service.api_provider is None:raise InputError('真实API未配置；可用本地草案或手动GPT方案')
    product=copy.deepcopy(payload['product'])
    class DetailAdapter:
        def planning_context(self,_):
            bundle=context(service,{'product':product})
            return {**bundle,'status':'ready-for-proposal','instructions':bundle['manual_prompt'].split('\n',1)[0],
                    'context':{**bundle['context'],'default_plan':bundle['default_plan']}}
        def validate_proposal(self,value):
            accepted=validate(service,{'product':product,'plan':value['proposal']})
            return {**accepted,'proposal':copy.deepcopy(value['proposal'])}
    return service.api_provider.propose(DetailAdapter(),{'text':'六模块商品详情页资料规划','profile':'stable',
        **{k:payload[k] for k in allowed-{'product'}}},cancel)
