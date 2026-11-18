"""Bundled author recipes, not runtime model capability or execution authority."""
import copy
import hashlib
import json
import re
from .errors import InputError
from .settings import TEMPLATE_DIR

ENGINES = {'text-to-image', 'reference-edit', 'masked-edit', 'layout-only',
           'vton-input-only', 'image-to-video', 'text-to-video', 'planning-only'}
FIELDS = {'id','title','category','engine','inputs','variables','positive','negative','checks','note'}
TOKEN = re.compile(r'\{([a-z_]+)\}')

def _text(value, maximum):
    return isinstance(value,str) and len(value)<=maximum and not any(ord(c)<32 for c in value)

def catalog():
    path=TEMPLATE_DIR/'prompt_library.json'
    if path.stat().st_size>256*1024:
        raise InputError('提示词库文件超限')
    raw=path.read_bytes();data=json.loads(raw)
    if set(data)!={'schema_version','revision','authorship','cards'} or type(data['schema_version']) is not int or data['schema_version']!=1:
        raise InputError('提示词库版本无效')
    if not _text(data['revision'],64) or not _text(data['authorship'],128) or not isinstance(data['cards'],list) or not 1<=len(data['cards'])<=100:
        raise InputError('提示词库清单无效')
    ids=set()
    for card in data['cards']:
        if not isinstance(card,dict) or set(card)!=FIELDS:
            raise InputError('提示词卡字段不完整')
        if not _text(card['id'],64) or not re.fullmatch('[a-z][a-z0-9_]*',card['id']) or card['id'] in ids:
            raise InputError('提示词卡编号无效')
        ids.add(card['id'])
        if card['engine'] not in ENGINES or any(not _text(card[k],n) or not card[k] for k,n in [('title',100),('category',40),('positive',3000),('note',600)]) or not _text(card['negative'],1000):
            raise InputError('提示词卡内容无效')
        for field in ('inputs','checks'):
            if not isinstance(card[field],list) or not 1<=len(card[field])<=8 or any(not _text(s,400) or not s for s in card[field]):
                raise InputError('提示词素材或验收清单无效')
        variables=card['variables']
        if not isinstance(variables,dict) or len(variables)>8 or any(not re.fullmatch('[a-z_]+',k) or not _text(v,240) or not v or '{' in v or '}' in v for k,v in variables.items()):
            raise InputError('提示词变量无效')
        combined=card['positive']+card['negative']
        if set(TOKEN.findall(combined))!=set(variables) or any(c in TOKEN.sub('',combined) for c in '{}'):
            raise InputError('提示词占位符不匹配')
    source_path=TEMPLATE_DIR/'prompt_sources.json'
    if source_path.stat().st_size>64*1024:
        raise InputError('提示词来源文件超限')
    source_raw=source_path.read_bytes();sources=json.loads(source_raw)
    if set(sources)!={'schema_version','sources','card_sources','notice'} or sources['schema_version']!=1 or not isinstance(sources['sources'],list) or not isinstance(sources['card_sources'],dict):
        raise InputError('提示词来源清单无效')
    source_ids=set()
    for source in sources['sources']:
        if not isinstance(source,dict) or set(source)!={'id','title','url','summary','checked_at','evidence'} or any(not _text(v,800) for v in source.values()):
            raise InputError('提示词来源字段无效')
        if source['id'] in source_ids or not re.fullmatch('PS[0-9]{2}',source['id']) or not source['url'].startswith('https://'):
            raise InputError('提示词来源编号或网址无效')
        source_ids.add(source['id'])
    for ident,refs in sources['card_sources'].items():
        if ident not in ids or not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in source_ids for r in refs):
            raise InputError('提示词卡来源关系无效')
    return {**data,'research':sources,'content_sha256':hashlib.sha256(raw+b'\x00'+source_raw).hexdigest(),
            'execution_authorized':False,'api_called':False,'runtime_verified':False}

def search(payload):
    if not isinstance(payload,dict) or set(payload)-{'query','category','engine'}:
        raise InputError('提示词检索只接受关键词、分类和用途')
    query=payload.get('query','');category=payload.get('category','');engine=payload.get('engine','')
    if not _text(query,200) or not _text(category,40) or not _text(engine,40):
        raise InputError('提示词检索范围无效')
    data=catalog()
    if category and category not in {c['category'] for c in data['cards']} or engine and engine not in ENGINES:
        raise InputError('未知提示词分类或用途')
    terms=query.lower().split()
    data['cards']=[c for c in data['cards'] if (not category or c['category']==category) and (not engine or c['engine']==engine)
                   and all(term in json.dumps(c,ensure_ascii=False).lower() for term in terms)]
    return copy.deepcopy(data)

def render(payload):
    if not isinstance(payload,dict) or set(payload)!={'id','expected_sha256','variables'}:
        raise InputError('填写提示词需要编号、当前库摘要和完整变量')
    data=catalog()
    if payload['expected_sha256']!=data['content_sha256']:
        raise InputError('提示词库已变化，请重新读取')
    card=next((c for c in data['cards'] if c['id']==payload['id']),None)
    if card is None:
        raise InputError('未知提示词卡编号')
    values=payload['variables']
    if not isinstance(values,dict) or set(values)!=set(card['variables']) or any(not _text(v,240) or not v.strip() or '{' in v or '}' in v for v in values.values()):
        raise InputError('请填全当前卡变量，每项1至240字符，不能含控制字符或花括号')
    result=copy.deepcopy(card)
    refs=data['research']['card_sources'].get(card['id'],[])
    result['references']=[s for s in data['research']['sources'] if s['id'] in refs]
    result['variables']=copy.deepcopy(values)
    for key in ('positive','negative'):
        result[key]=TOKEN.sub(lambda m:values[m[1]],card[key])
    return {'schema_version':1,'library_revision':data['revision'],'content_sha256':data['content_sha256'],
            'card':result,'execution_authorized':False,'api_called':False,'runtime_verified':False,
            'negative_usage':'仅在实际模型与节点支持负面条件时使用；不自动接线',
            'notice':'这是提示词/制作说明，不是ComfyUI工作流。请勿填写密钥或敏感资料；不自动发送、上传或排队。'}
