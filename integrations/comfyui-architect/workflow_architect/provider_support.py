"""Explicit provider profiles and one-shot text-only planning. Disabled by default."""
import copy
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from .api_planner import parse_chat_response
from .errors import InputError
from .knowledge import _sha

def validate_profile(profile):
    fields={'endpoint','model','protocol','token_parameter','max_output_tokens','timeout_seconds'}
    if not isinstance(profile,dict) or set(profile)!=fields:raise InputError('配置仅允许完整接口/模型/协议/token参数/上限/超时，不接受密钥')
    p=copy.deepcopy(profile)
    if not isinstance(p['endpoint'],str) or len(p['endpoint'])>512 or re.search(r'[\s\\\x00-\x1f]',p['endpoint']):raise InputError('接口地址不合法')
    try:
        u=urlsplit(p['endpoint']);port=u.port
    except ValueError:raise InputError('接口地址不合法') from None
    if not isinstance(p['protocol'],str):raise InputError('接口协议不合法')
    suffix={'chat_completions':'/chat/completions','responses':'/responses'}.get(p['protocol'])
    if not suffix or u.scheme!='https' or not u.hostname or not re.fullmatch(r'[a-zA-Z0-9.-]+',u.hostname) or u.username or u.password or u.query or u.fragment or port not in (None,443) or not u.path.endswith(suffix):
        raise InputError('接口须为明确HTTPS443地址，不接受dashboard/查询凭证/其他协议')
    if not isinstance(p['model'],str) or not re.fullmatch(r'[A-Za-z0-9_./:-]{1,128}',p['model']):raise InputError('须填写准确模型ID，不按品牌名猜测')
    tokens={'chat_completions':{'max_tokens','max_completion_tokens'},'responses':{'max_output_tokens'}}
    if not isinstance(p['token_parameter'],str) or p['token_parameter'] not in tokens[p['protocol']]:raise InputError('token参数与接口协议不匹配')
    if type(p['max_output_tokens']) is not int or not 512<=p['max_output_tokens']<=4096:raise InputError('输出token上限须512到4096')
    if type(p['timeout_seconds']) is not int or not 5<=p['timeout_seconds']<=30:raise InputError('超时须5到30秒')
    return p

def build_request(context,profile):
    p=validate_profile(profile)
    if context.get('status')!='ready-for-proposal':raise InputError('没有兼容模板，不发送抽象规划')
    data=json.dumps({'context_digest':context['context_digest'],'context':context['context']},ensure_ascii=False)
    if p['protocol']=='chat_completions':
        body={'model':p['model'],'messages':[{'role':'system','content':context['instructions']},{'role':'user','content':data}],
              p['token_parameter']:p['max_output_tokens'],'stream':False}
    else:
        body={'model':p['model'],'instructions':context['instructions'],'input':data,'max_output_tokens':p['max_output_tokens'],'stream':False,'store':False}
    raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>128*1024:raise InputError('文本规划请求超过128KiB')
    return raw

def parse_response(raw,protocol):
    if protocol=='chat_completions':return parse_chat_response(raw)
    if protocol!='responses' or not isinstance(raw,bytes) or len(raw)>128*1024:raise InputError('返回协议/长度不合法')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise ValueError()
            d[k]=v
        return d
    try:
        data=json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if data['status']!='completed' or data.get('error') or data.get('incomplete_details'):raise ValueError()
        outputs=data['output']
        if not isinstance(outputs,list) or len(outputs)>32:raise ValueError()
        texts=[]
        for item in outputs:
            if item.get('type')=='reasoning':continue
            if item.get('type')!='message' or item.get('role')!='assistant' or item.get('status')!='completed':raise ValueError()
            for part in item['content']:
                if part.get('type')!='output_text' or not isinstance(part.get('text'),str):raise ValueError()
                texts.append(part['text'])
        if len(texts)!=1 or len(texts[0])>32768:raise ValueError()
        # Reuse the same strict nested JSON parser and downstream validator.
        envelope={'choices':[{'finish_reason':'stop','message':{'content':texts[0]}}]}
        return parse_chat_response(json.dumps(envelope).encode())
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
        raise InputError('Responses返回非完整JSON规划，拒绝工具调用/拒绝/截断') from None

def public_addresses(host):
    addresses=sorted({row[4][0] for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
    if not addresses or len(addresses)>16:raise InputError('DNS地址数量不合法')
    for address in addresses:
        ip=ipaddress.ip_address(address)
        if not ip.is_global or (ip.version==6 and ip.ipv4_mapped is not None):raise InputError('拒绝非公网或映射地址')
    return addresses

def https_once(profile,key,raw,cancel):
    """Single verified TLS connection, pinned public address, no proxy/retry/redirect."""
    p=validate_profile(profile);u=urlsplit(p['endpoint']);deadline=time.monotonic()+p['timeout_seconds']
    def check():
        if cancel.is_set() or time.monotonic()>=deadline:raise InputError('规划请求取消或到期；若已发送费用可能未知，不自动重试')
    check();addresses=public_addresses(u.hostname);check()
    connection=http.client.HTTPSConnection(u.hostname,443,timeout=p['timeout_seconds'],context=ssl.create_default_context())
    sock=None;response=None
    try:
        sock=socket.create_connection((addresses[0],443),timeout=max(.01,deadline-time.monotonic()));check()
        sock=connection._context.wrap_socket(sock,server_hostname=u.hostname);check()
        connection.sock=sock
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json','Accept-Encoding':'identity','Connection':'close'}
        check();sock.settimeout(max(.01,deadline-time.monotonic()))
        connection.request('POST',u.path,body=raw,headers=headers)
        # Keep ownership of the socket through bounded body reads. getresponse()
        # closes connection.sock immediately for Connection: close responses.
        response=http.client.HTTPResponse(sock,method='POST');response.begin();check()
        if response.status!=200:raise InputError('供应商未返回200；可能已计费，不自动重试')
        headers=response.getheaders()
        if len(headers)>100 or sum(len(k)+len(v) for k,v in headers)>16384:raise InputError('API响应头超限')
        def single(name):
            values=[v for k,v in headers if k.lower()==name]
            if len(values)>1:raise InputError('API响应头重复')
            return values[0] if values else None
        content_type=single('content-type') or ''
        if content_type.split(';')[0].strip().lower()!='application/json' or single('content-encoding') not in (None,'identity') or single('location'):
            raise InputError('拒绝非JSON、压缩或重定向响应')
        length=single('content-length')
        if length is not None and (not length.isdigit() or int(length)>128*1024):raise InputError('API响应长度超限')
        chunks=[];size=0
        while True:
            check();sock.settimeout(max(.01,deadline-time.monotonic()));chunk=response.read1(min(8192,128*1024+1-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
            if size>128*1024 or len(chunks)>4096:raise InputError('API响应超限')
        check()
        if length is not None and size!=int(length):raise InputError('API正文长度错配')
        return b''.join(chunks)
    finally:
        if response:response.close()
        connection.close()
        if sock:sock.close()

class PlannerProvider:
    """Trusted host wiring only. No HTTP route can install keys/config/approval."""
    def __init__(self,profile,credential_reader,approval,transport=https_once):
        self.profile=validate_profile(profile);self.credential_reader=credential_reader;self.approval=approval;self.transport=transport
        self._slot=threading.BoundedSemaphore(1)
    def propose(self,service,payload,cancel=None):
        if not isinstance(payload,dict) or set(payload)!={'text','profile','context_digest','profile_digest','confirm_external_text','confirm_one_paid_call'} or payload['confirm_external_text'] is not True or payload['confirm_one_paid_call'] is not True:
            raise InputError('须明确确认单次可能收费的文本规划，不上传图片')
        if not self._slot.acquire(blocking=False):raise InputError('已有规划调用未结束，不并发或自动重试')
        cancel=cancel or threading.Event()
        try:
            profile=validate_profile(self.profile)
            if payload['profile_digest']!=_sha(profile):raise InputError('实际API目标配置已变化，请重新读取状态并确认')
            reader,approval,transport=self.credential_reader,self.approval,self.transport
            if transport is https_once:
                from .api_budget import CostGuard
                if not isinstance(approval,CostGuard):raise InputError('真实HTTPS调用须可信价格和供应商限额批准，不接受恒True代替')
            def unchanged():
                if self.profile!=profile or self.credential_reader is not reader or self.approval is not approval or self.transport is not transport:
                    raise InputError('调用期间配置或端口变化，不读取凭证或发送')
            context=service.planning_context({k:payload[k] for k in ('text','profile')})
            if context['context_digest']!=payload['context_digest']:raise InputError('上下文已变化')
            raw=build_request(context,profile)
            scope={'profile':copy.deepcopy(profile),'context_digest':context['context_digest'],'request_sha256':hashlib.sha256(raw).hexdigest(),
                   'request_bytes':len(raw),
                   'single_call':True,'actual_cost_known':False}
            try:
                if approval(copy.deepcopy(scope)) is not True:raise ValueError()
            except Exception:raise InputError('独立费用/目标批准未通过，不读取凭证或发送') from None
            unchanged()
            if hasattr(approval,'assert_current'):
                from .api_budget import CostCheckedCancel
                cancel=CostCheckedCancel(cancel,approval)
            if cancel.is_set():raise InputError('调用已取消')
            try:key=reader(copy.deepcopy(scope))
            except Exception:raise InputError('凭证不可用') from None
            if not isinstance(key,str) or not 8<=len(key)<=4096 or any(ord(c)<33 or ord(c)>126 for c in key):raise InputError('凭证格式不合法')
            unchanged()
            try:
                if cancel.is_set():raise InputError('调用已取消或费用证据撤回，不调用发送端口')
                response=transport(copy.deepcopy(profile),key,raw,cancel)
            except Exception:raise InputError('API调用未完成；已发送时费用可能未知，请核对站点，不自动重试') from None
            finally:key=None
            if cancel.is_set():raise InputError('调用已取消，不采用迟到结果；费用可能未知')
            unchanged()
            proposal=parse_response(response,profile['protocol'])
            result=service.validate_proposal({'text':payload['text'],'profile':payload['profile'],'proposal':proposal})
            result.update(api_called=True,provider_model=profile['model'],provider_protocol=profile['protocol'],actual_cost_known=False)
            return result
        finally:
            try:
                if 'approval' in locals() and hasattr(approval,'close'):approval.close()
            finally:self._slot.release()
