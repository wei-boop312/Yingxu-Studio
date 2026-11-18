"""Phase-one, bounded local grounding report. No API/model execution or writes."""
from dataclasses import asdict
import hashlib
import json
from .errors import InputError
from .knowledge import KnowledgeBase, _sha
from .settings import TEMPLATE_DIR
from .tryon import MODELS

def audit_template(template, snapshot, *, fixed_models=None):
    """Check actual signatures and link types; placeholders still require binding."""
    definitions = {}
    issues = []
    nodes = {n['key']: n for n in template['nodes']}
    for key, node in nodes.items():
        name = node.get('class_type')
        if not name:
            capability = snapshot.capabilities.get(str(node.get('capability','')).split('.')[0])
            name = capability.matched_node if capability else None
        definition = snapshot.nodes.get(name)
        definitions[key] = definition
        if definition is None:
            issues.append({'code':'missing-node','node':key,'class_type':name or node.get('capability')})
    for key, node in nodes.items():
        definition = definitions[key]
        if not definition:
            continue
        fields = node.get('inputs',{})
        for item in definition.inputs:
            if item.required and item.name not in fields:
                issues.append({'code':'missing-input','node':key,'input':item.name})
        for name, value in fields.items():
            target = definition.input_by_name(name)
            if target is None:
                issues.append({'code':'unknown-input','node':key,'input':name}); continue
            if isinstance(value,dict) and 'link' in value:
                source_key,index=value['link']; source=definitions.get(source_key)
                if source is None or type(index) is not int or not 0 <= index < len(source.outputs):
                    issues.append({'code':'invalid-link','node':key,'input':name})
                elif target.type != '*' and source.outputs[index] != '*' and target.type != source.outputs[index]:
                    issues.append({'code':'link-type-mismatch','node':key,'input':name})
            elif isinstance(value,str) and value.startswith('${'):
                continue
            elif target.choices and str(value) not in target.choices:
                issues.append({'code':'unsupported-enum','node':key,'input':name})
    models=[]
    for binding in template.get('model_bindings',[]):
        definition=snapshot.nodes.get(binding['class_type'])
        item=definition.input_by_name(binding['input']) if definition else None
        choices=list(item.choices) if item else []
        if len(choices)>512:
            raise InputError('模型选项超过512，请收敛模型目录')
        required=(fixed_models or {}).get(binding['binding'])
        matched=required in choices if required else bool(choices)
        models.append({'binding':binding['binding'],'required_name':required,'available_choices':choices,'matched':matched,'selected':False})
        if not matched:
            issues.append({'code':'missing-model','binding':binding['binding'],'required_name':required})
    signature={n.class_name:asdict(n) for n in definitions.values() if n}
    return {'issues':issues,'compatible':not issues,'model_options':models,
            'node_signatures':signature,'signature_sha256':_sha(signature),
            'file_integrity_verified':False,'runtime_verified':False,'execution_authorized':False}

def foundation_report(service,payload):
    if not isinstance(payload,dict) or set(payload)-{'text','profile'}:
        raise InputError('底座报告只接受text/profile，不接受路径、模型或执行命令')
    text=payload.get('text');profile=payload.get('profile','stable')
    if not isinstance(text,str) or not 1<=len(text.strip())<=4000 or profile not in ('stable','low_vram'):
        raise InputError('需求须1到4000字符，模式为stable/low_vram')
    snapshot=service.registry.current()
    if snapshot.generation<1:
        raise InputError('请先扫描当前ComfyUI环境')
    parsed=service.parse({'text':text,'options':{'profile':profile}})
    person=parsed.get('person_request') or {}
    task=parsed['spec']['task'] if parsed.get('recognized') and parsed.get('spec') else None
    if person.get('reference_only'):
        task=None
    kb=KnowledgeBase()
    try:
        retrieval=kb.search(text,snapshot,task=task,profile=profile,limit=8)
        candidates=[]
        # Retrieval ranks unusable templates below references. Diagnostics must
        # still report their missing dependencies, rather than hide the gap.
        for item in kb.task_templates(task,profile) if task else []:
            template=kb.template(item['id'].removeprefix('template:'))
            candidates.append({'id':item['id'],'template_sha256':_sha(template),'provenance':{
                'local_file':'templates/concrete/'+item['title']+'.json',
                'origin':'plugin-maintained-implementation','upstream_reference_ids':['S01'],
                'note':'官方资料为节点/工作流参考，不声称本地模板与上游同一文件'},
                'experimental':False,'entry':'本地知识辅助','audit':audit_template(template,snapshot)})
        if person.get('kind')=='virtual_tryon':
            raw=(TEMPLATE_DIR/'experimental/klein_tryon.json').read_bytes();template=json.loads(raw)
            audit=audit_template(template,snapshot,fixed_models=MODELS)
            if profile!='stable':
                audit['issues'].append({'code':'profile-incompatible'});audit['compatible']=False
            candidates.append({'id':template['template_id'],'template_sha256':hashlib.sha256(raw).hexdigest(),
                'provenance':{'local_file':'templates/experimental/klein_tryon.json','origin':'plugin-maintained-adaptation',
                'upstream_reference_ids':['S01','S03','S04'],
                'note':'本机两次研究运行不代表商品细节质量验收；固定4步蒸馏/双参考，非Base模板'},
                'experimental':True,'entry':'Klein双图换装（实验）','audit':audit})
        source_data=json.loads((TEMPLATE_DIR/'research_sources.json').read_text(encoding='utf-8'))
        needed={s for c in candidates for s in c['provenance']['upstream_reference_ids']}
        needed.update(s for r in retrieval['results'] for s in r['source_ids'])
        sources=[s for s in source_data['sources'] if s['id'] in needed]
        if needed-{s['id'] for s in sources}:
            raise InputError('引用来源缺失，停止生成报告')
        if service.registry.current().generation!=snapshot.generation:
            raise InputError('扫描清单已变化，请重新生成报告')
        return {'schema_version':1,'phase':1,'query':text.strip(),'profile':profile,'registry_generation':snapshot.generation,
                'registry_node_count':snapshot.node_count,'corpus_revision':retrieval['corpus_revision'],
                'document_count':retrieval['document_count'],'retrieval':retrieval['retrieval'],
                'references':retrieval['results'],'sources':sources,'candidates':candidates,
                'status':'concrete-candidates' if candidates else 'reference-only-no-executable-template',
                'requirements':['明确选择输入与模型后编译','运行前人工检查','输出质量独立验收'],
                'not_verified':['权重文件完整性与安装包版本','显存/速度适足','商品细节/身份/背景保真'],
                'api_called':False,'small_model_loaded':False,'execution_authorized':False}
    finally:kb.close()
