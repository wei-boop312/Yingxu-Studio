"""User-approved target plus fail-closed cost preflight; no price scraping/secrets."""
import copy
import threading
import time
from .errors import InputError

CAP_MICRO_USD=20_000  # exactly USD0.02, integer millionths of a dollar
REQUESTED_PROFILE={'endpoint':'https://hubway.cc/v1/responses','model':'gpt-5.5','protocol':'responses',
                   'token_parameter':'max_output_tokens','max_output_tokens':2048,'timeout_seconds':30}

def cost_preflight(scope,quote,now):
    fields={'endpoint','model','request_sha256','currency','group_id','source_id','source_version','evidence_sha256',
            'expires_at','input_tokens_upper_bound','input_tokenizer_evidence','input_rate_micro_usd_per_million',
            'output_rate_micro_usd_per_million','fixed_fee_micro_usd','all_reasoning_tokens_in_output_cap',
            'all_group_multipliers_included','provider_hard_cap_micro_usd','provider_limit_evidence'}
    if not isinstance(quote,dict) or set(quote)!=fields:raise InputError('费用证据不完整，保持关闭')
    q=copy.deepcopy(quote);p=scope['profile']
    if any(p[k]!=REQUESTED_PROFILE[k] for k in ('endpoint','model','protocol')):
        raise InputError('超出当前用户授权的Hubway/gpt-5.5/Responses范围')
    if q['endpoint']!=p['endpoint'] or q['model']!=p['model'] or q['request_sha256']!=scope['request_sha256'] or q['currency']!='USD':
        raise InputError('费用范围/币种与实际请求不匹配')
    for key in ('group_id','source_id','source_version','input_tokenizer_evidence','provider_limit_evidence'):
        if not isinstance(q[key],str) or not 1<=len(q[key])<=160:raise InputError('费用来源/分组/账户限额证据缺失')
    if not isinstance(q['evidence_sha256'],str) or len(q['evidence_sha256'])!=64 or any(c not in '0123456789abcdef' for c in q['evidence_sha256']):raise InputError('费用证据摘要不合法')
    if type(q['expires_at']) not in (int,float) or not now<q['expires_at']<=now+300:raise InputError('费用证据已过期或期限过长')
    for key in ('input_tokens_upper_bound','input_rate_micro_usd_per_million','output_rate_micro_usd_per_million','fixed_fee_micro_usd','provider_hard_cap_micro_usd'):
        if type(q[key]) is not int or not 0<=q[key]<=10**12:raise InputError('费用整数不合法')
    if q['input_tokens_upper_bound']<1 or q['all_reasoning_tokens_in_output_cap'] is not True or q['all_group_multipliers_included'] is not True:
        raise InputError('必须包含完整输入/推理输出与分组倍率，不猜token或漏计')
    if not 0<q['provider_hard_cap_micro_usd']<=CAP_MICRO_USD:raise InputError('供应商硬限额证据不满足0.02USD授权')
    # Integer arithmetic; round UP, never silently shorten the request/model.
    numerator=q['input_tokens_upper_bound']*q['input_rate_micro_usd_per_million']+p['max_output_tokens']*q['output_rate_micro_usd_per_million']
    upper=(numerator+999_999)//1_000_000+q['fixed_fee_micro_usd']
    if upper>min(CAP_MICRO_USD,q['provider_hard_cap_micro_usd']):raise InputError('费用上界超过授权，不发送或自动缩减参数')
    return {'currency':'USD','authorized_cap_micro_usd':CAP_MICRO_USD,'estimated_upper_micro_usd':upper,
            'actual_cost_known':False,'source_id':q['source_id'],'source_version':q['source_version'],
            'group_id':q['group_id'],'expires_at':q['expires_at'],'provider_limit_evidence':q['provider_limit_evidence']}

class CostGuard:
    """Trusted source ports only; returned labels/hash alone are NOT proof."""
    def __init__(self,quote_reader,source_verifier,clock=time.time):
        self.reader=quote_reader;self.verifier=source_verifier;self.clock=clock;self._local=threading.local()
    def __call__(self,scope):
        self._local.current=None
        try:
            q=copy.deepcopy(self.reader(copy.deepcopy(scope)))
            result=cost_preflight(scope,q,self.clock())
            if self.verifier(copy.deepcopy(scope),copy.deepcopy(q)) is not True:raise ValueError()
        except Exception:
            raise InputError('可信价格/token/分组/供应商限额尚未核验，不授权调用') from None
        self._local.current=(copy.deepcopy(scope),q,self.reader,self.verifier,self.clock)
        return True
    def assert_current(self):
        current=getattr(self._local,'current',None)
        if current is None:raise InputError('无当次费用批准')
        scope,quote,reader,verifier,clock=current
        if self.reader is not reader or self.verifier is not verifier or self.clock is not clock:raise InputError('费用端口变化')
        cost_preflight(scope,quote,clock())
        try:valid=verifier(copy.deepcopy(scope),copy.deepcopy(quote)) is True
        except Exception:valid=False
        if not valid:raise InputError('费用来源或供应商限额已撤回')
    def close(self):
        self._local.current=None

class CostCheckedCancel:
    def __init__(self,event,guard):self.event=event;self.guard=guard
    def is_set(self):
        if self.event.is_set():return True
        self.guard.assert_current()
        return False
