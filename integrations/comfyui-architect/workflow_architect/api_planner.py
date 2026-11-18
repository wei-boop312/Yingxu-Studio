"""Grounded planning exchange. Public routes are offline; no credentials/network."""
import copy
import json
from urllib.parse import urlsplit
from .errors import InputError
from .knowledge import _sha
from .knowledge import KnowledgeBase
from .settings import TEMPLATE_DIR

INSTRUCTIONS = ('Return one JSON object, no markdown or commands. Choose exactly one supplied candidate. '
                'Do not invent nodes or models. Use schema: {context_digest:string,template_id:string,'
                'evidence_ids:[source IDs],reason:string,steps:[{title:string,node_classes:[registered node class names]}]}. '
                'Steps must together cover every candidate node class. This is a proposal, not permission to run. '
                'If there is no compatible candidate, do not invent one; ask for missing dependencies.')

def planning_context(service,payload):
    if not isinstance(payload,dict) or set(payload)-{'text','profile'}:
        raise InputError('规划上下文只接受text/profile')
    report=service.foundation_report(payload)
    candidates=[]
    for c in report['candidates']:
        if not c['audit']['compatible']:continue
        if c['experimental']:
            template=json.loads((TEMPLATE_DIR/'experimental/klein_tryon.json').read_text(encoding='utf-8'))
        else:
            kb=KnowledgeBase()
            try:template=kb.template(c['id'].removeprefix('template:'))
            finally:kb.close()
        contracts={name:{'inputs':[{'name':i['name'],'type':i['type'],'required':i['required']} for i in node['inputs']],
                         'outputs':node['outputs']} for name,node in c['audit']['node_signatures'].items()}
        # Never export LoadImage choices, private paths, images, raw report or keys.
        candidates.append({'template_id':c['id'],'template_sha256':c['template_sha256'],
            'signature_sha256':_sha(contracts),
            'signature_scope':'node-input-types-required-output-types-not-image-choice-list',
            'node_classes':sorted(c['audit']['node_signatures']),
            'evidence_ids':c['provenance']['upstream_reference_ids'],
            'experimental':c['experimental'],'entry':c['entry'],
            'runtime_profile':report['profile'],'maturity':'experimental' if c['experimental'] else 'static-template-not-quality-certified',
            'task':template['task'],'node_contracts':contracts,
            'graph_contract':[{'key':n['key'],'class_type':n.get('class_type'),'capability':n.get('capability'),'inputs':n.get('inputs',{})} for n in template['nodes']],
            'model_requirements':[{'binding':m['binding'],'required_name':m['required_name']} for m in c['audit']['model_options']]})
    core={'schema_version':1,'query':report['query'],'profile':report['profile'],
          'registry_generation':report['registry_generation'],'corpus_revision':report['corpus_revision'],
          'candidates':candidates,'sources':[{'id':s['id'],'url':s['url'],'checked_at':s['checked_at']} for s in report['sources']],
          'limits':{'no_execution':True,'no_image_upload':True,'manual_model_and_input_selection_required':True}}
    # A repeated scan or upload does not invalidate the same plan. Real
    # template/contracts/candidate/model requirements/query changes still do.
    identity={k:v for k,v in core.items() if k!='registry_generation'}
    digest=_sha(identity)
    bundle={'context_digest':digest,'context':core}
    return {'phase':2,'context_digest':digest,'context':core,'instructions':INSTRUCTIONS,
            'manual_prompt':INSTRUCTIONS+'\n'+json.dumps(bundle,ensure_ascii=False),
            'status':'ready-for-proposal' if candidates else 'blocked-no-compatible-template',
            'api_called':False,'execution_authorized':False}

def validate_proposal(service,payload):
    if not isinstance(payload,dict) or set(payload)-{'text','profile','proposal'}:
        raise InputError('方案导入字段不合法')
    context=planning_context(service,{k:v for k,v in payload.items() if k!='proposal'})
    proposal=payload.get('proposal')
    if not isinstance(proposal,dict) or set(proposal)!={'context_digest','template_id','evidence_ids','reason','steps'}:
        raise InputError('方案必须为完整严格JSON，不接受任意工作流/命令')
    if proposal['context_digest']!=context['context_digest']:
        raise InputError('需求/知识/节点清单已变化，请重新导出上下文')
    selected=next((c for c in context['context']['candidates'] if c['template_id']==proposal['template_id']),None)
    if not selected:raise InputError('模型选择不是当前兼容的具体模板')
    ids=proposal['evidence_ids']
    if not isinstance(ids,list) or not 1<=len(ids)<=8 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or not set(ids)<=set(selected['evidence_ids']):
        raise InputError('方案依据不属于所选模板的当前来源')
    reason=proposal['reason']
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:raise InputError('方案说明须1到1000字符')
    steps=proposal['steps']
    if not isinstance(steps,list) or not 1<=len(steps)<=16:raise InputError('方案须1到16个具体步骤')
    covered=set()
    for step in steps:
        if not isinstance(step,dict) or set(step)!={'title','node_classes'} or not isinstance(step['title'],str) or not 1<=len(step['title'].strip())<=160:
            raise InputError('步骤结构不合法')
        names=step['node_classes']
        if not isinstance(names,list) or not 1<=len(names)<=32 or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names) or not set(names)<=set(selected['node_classes']):
            raise InputError('步骤包含虚构或不属于模板的节点')
        covered.update(names)
    if covered!=set(selected['node_classes']):raise InputError('方案遗漏模板实际节点，不能只返回抽象步骤')
    return {'phase':2,'status':'grounded-proposal-not-executed','proposal':copy.deepcopy(proposal),
            'selected_template':copy.deepcopy(selected),'proposal_sha256':_sha(proposal),
            'next':'明确选择图片和模型后交给第三阶段编译；不是执行许可',
            'api_called':False,'execution_authorized':False,'quality_approved':False}

def build_chat_request(context,config):
    """Pure OpenAI-compatible chat serialization; does NOT send a request."""
    if not isinstance(config,dict) or set(config)!={'endpoint','model','max_tokens'}:
        raise InputError('适配器只接受明确endpoint/model/max_tokens，不接受密钥')
    url=urlsplit(config['endpoint']) if isinstance(config['endpoint'],str) else None
    if not url or url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or not url.path.endswith('/chat/completions'):
        raise InputError('仅支持无凭证查询的明确HTTPS chat/completions地址')
    if not isinstance(config['model'],str) or not 1<=len(config['model'])<=128 or any(c.isspace() for c in config['model']):raise InputError('模型ID不合法')
    if type(config['max_tokens']) is not int or not 512<=config['max_tokens']<=4096:raise InputError('输出token须512到4096')
    if context.get('status')!='ready-for-proposal':raise InputError('无兼容模板，不构造API请求')
    return {'endpoint':config['endpoint'],'body':{'model':config['model'],'messages':[
        {'role':'system','content':context['instructions']},
        {'role':'user','content':json.dumps({'context_digest':context['context_digest'],'context':context['context']},ensure_ascii=False)}],
        'max_tokens':config['max_tokens'],'temperature':0,'stream':False},
        'sent':False,'credential_configured':False,'cost_authorized':False}

def parse_chat_response(response):
    """Bounded strict envelope parsing for a trusted future transport port."""
    if not isinstance(response,bytes) or len(response)>128*1024:raise InputError('API响应过大或类型错误')
    def strict(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise ValueError('duplicate')
            d[k]=v
        return d
    try:
        value=json.loads(response.decode('utf-8'),object_pairs_hook=strict,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        choices=value['choices']
        if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict) or choices[0].get('finish_reason')!='stop':raise ValueError()
        message=choices[0]['message']
        if not isinstance(message,dict) or message.get('tool_calls') or message.get('function_call') or message.get('refusal'):raise ValueError()
        content=message['content']
        if not isinstance(content,str) or len(content)>32768:raise ValueError()
        proposal=json.loads(content,object_pairs_hook=strict,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(proposal,dict):raise ValueError()
        return proposal
    except (ValueError,KeyError,TypeError,IndexError,RecursionError):
        raise InputError('API返回不是完整严格JSON方案，拒绝截断/工具调用/Markdown') from None
