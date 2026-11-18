import copy
import json
import threading
import unittest
from test_tryon import fixture
from workflow_architect.api_budget import CAP_MICRO_USD,REQUESTED_PROFILE,cost_preflight,CostGuard
from workflow_architect.knowledge import _sha
from workflow_architect.provider_support import PlannerProvider
from workflow_architect.service import ArchitectService
from workflow_architect.errors import InputError

def quote(scope):
    return {'endpoint':scope['profile']['endpoint'],'model':scope['profile']['model'],'request_sha256':scope['request_sha256'],
        'currency':'USD','group_id':'owned-fixture-group-not-real-price','source_id':'owned-fake-billing','source_version':'fixture1',
        'evidence_sha256':'a'*64,'expires_at':150,'input_tokens_upper_bound':1000,'input_tokenizer_evidence':'owned-fixture-token-bound',
        'input_rate_micro_usd_per_million':1_000_000,'output_rate_micro_usd_per_million':2_000_000,'fixed_fee_micro_usd':0,
        'all_reasoning_tokens_in_output_cap':True,'all_group_multipliers_included':True,
        'provider_hard_cap_micro_usd':20000,'provider_limit_evidence':'owned-fake-limit-not-account-proof'}

class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.scope={'profile':dict(REQUESTED_PROFILE),'request_sha256':'b'*64,'request_bytes':200,'context_digest':'c'*64}
    def test_fixed_authorization_exact_cents(self):
        r=cost_preflight(self.scope,quote(self.scope),100)
        self.assertEqual(CAP_MICRO_USD,20000);self.assertEqual(r['estimated_upper_micro_usd'],5096);self.assertFalse(r['actual_cost_known'])
    def test_round_up_not_float_undercharge(self):
        q=quote(self.scope);q.update(input_tokens_upper_bound=1,input_rate_micro_usd_per_million=1,output_rate_micro_usd_per_million=0)
        self.assertEqual(cost_preflight(self.scope,q,100)['estimated_upper_micro_usd'],1)
    def test_exact_cap_and_exceed(self):
        q=quote(self.scope);q.update(input_rate_micro_usd_per_million=0,output_rate_micro_usd_per_million=0,fixed_fee_micro_usd=20000)
        self.assertEqual(cost_preflight(self.scope,q,100)['estimated_upper_micro_usd'],20000)
        q['fixed_fee_micro_usd']=20001
        with self.assertRaises(InputError):cost_preflight(self.scope,q,100)
    def test_unknown_evidence_and_rate_fields_fail(self):
        for key in quote(self.scope):
            q=quote(self.scope);del q[key]
            with self.subTest(key=key),self.assertRaises(InputError):cost_preflight(self.scope,q,100)
    def test_exact_request_and_model_scope(self):
        for change in ({'request_sha256':'d'*64},{'currency':'CNY'},{'endpoint':'https://other.invalid/v1/responses'},{'model':'another'}):
            with self.assertRaises(InputError):cost_preflight(self.scope,{**quote(self.scope),**change},100)
        scope=copy.deepcopy(self.scope);scope['profile']['model']='another'
        with self.assertRaises(InputError):cost_preflight(scope,quote(scope),100)
    def test_group_and_reasoning_must_be_accounted(self):
        for change in ({'all_reasoning_tokens_in_output_cap':False},{'all_group_multipliers_included':1},{'provider_limit_evidence':''},{'input_tokenizer_evidence':''}):
            with self.assertRaises(InputError):cost_preflight(self.scope,{**quote(self.scope),**change},100)
    def test_no_provider_hard_cap_no_send(self):
        for value in (0,20001,None,True):
            with self.assertRaises(InputError):cost_preflight(self.scope,{**quote(self.scope),'provider_hard_cap_micro_usd':value},100)
    def test_source_expiry(self):
        for value in (99,100,401,float('nan')):
            with self.assertRaises(InputError):cost_preflight(self.scope,{**quote(self.scope),'expires_at':value},100)
    def test_numbers_strict(self):
        for key in ('input_tokens_upper_bound','input_rate_micro_usd_per_million','output_rate_micro_usd_per_million','fixed_fee_micro_usd'):
            for value in (-1,True,1.5,10**13):
                with self.subTest(key=key,value=value),self.assertRaises(InputError):cost_preflight(self.scope,{**quote(self.scope),key:value},100)
    def test_schema_or_source_label_not_permission(self):
        for v in (False,1,'true'):
            guard=CostGuard(quote,lambda s,q:v,lambda:100)
            with self.assertRaises(InputError):guard(self.scope)
    def test_revoke_expire_and_close(self):
        valid=[True];clock=[100]
        guard=CostGuard(quote,lambda s,q:valid[0],lambda:clock[0]);self.assertIs(guard(self.scope),True);guard.assert_current()
        valid[0]=False
        with self.assertRaises(InputError):guard.assert_current()
        valid[0]=True;clock[0]=151
        with self.assertRaises(InputError):guard.assert_current()
        guard.close()
        with self.assertRaises(InputError):guard.assert_current()
    def test_failed_new_check_cannot_reuse_old(self):
        guard=CostGuard(quote,lambda s,q:True,lambda:100);guard(self.scope)
        broken=copy.deepcopy(self.scope);broken['profile']['model']='other'
        with self.assertRaises(InputError):guard(broken)
        with self.assertRaises(InputError):guard.assert_current()
    def test_integrated_guard_before_key_and_transport(self):
        s=ArchitectService();s.refresh(fixture());c=s.planning_context({'text':'参考图换装'});candidate=c['context']['candidates'][0]
        payload={'text':'参考图换装','profile':'stable','context_digest':c['context_digest'],'profile_digest':_sha(REQUESTED_PROFILE),'confirm_external_text':True,'confirm_one_paid_call':True}
        calls=[]
        def reader(scope):calls.append('key');return 'owned-fake-key'
        def transport(p,key,raw,cancel):
            self.assertFalse(cancel.is_set());calls.append('transport')
            proposal={'context_digest':c['context_digest'],'template_id':candidate['template_id'],'evidence_ids':candidate['evidence_ids'],'reason':'fixture only',
                      'steps':[{'title':'graph','node_classes':candidate['node_classes']}]}
            return json.dumps({'status':'completed','output':[{'type':'message','status':'completed','role':'assistant','content':[{'type':'output_text','text':json.dumps(proposal)}]}]}).encode()
        guard=CostGuard(quote,lambda scope,q:True,lambda:100)
        provider=PlannerProvider(REQUESTED_PROFILE,reader,guard,transport)
        result=provider.propose(s,payload);self.assertFalse(result['actual_cost_known']);self.assertEqual(calls,['key','transport'])
        guard.reader=lambda scope:{};calls.clear()
        with self.assertRaises(InputError):provider.propose(s,payload)
        self.assertEqual(calls,[])
    def test_guard_recheck_after_key(self):
        s=ArchitectService();s.refresh(fixture());c=s.planning_context({'text':'参考图换装'});valid=[True];sent=[]
        payload={'text':'参考图换装','profile':'stable','context_digest':c['context_digest'],'profile_digest':_sha(REQUESTED_PROFILE),'confirm_external_text':True,'confirm_one_paid_call':True}
        guard=CostGuard(quote,lambda s,q:valid[0],lambda:100)
        def reader(scope):valid[0]=False;return 'owned-fake-key'
        def transport(*args):sent.append(True);args[-1].is_set();return b'{}'
        provider=PlannerProvider(REQUESTED_PROFILE,reader,guard,transport)
        with self.assertRaises(InputError):provider.propose(s,payload)
        self.assertEqual(sent,[])
