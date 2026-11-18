import copy
import json
import threading
import unittest
from unittest.mock import patch
from test_tryon import fixture
from workflow_architect.service import ArchitectService
from workflow_architect.provider_support import validate_profile,build_request,parse_response,public_addresses,PlannerProvider,https_once
from workflow_architect.errors import InputError
from workflow_architect.knowledge import _sha

def profile(protocol='chat_completions'):
    return {'endpoint':'https://example.invalid/v1/'+('chat/completions' if protocol=='chat_completions' else 'responses'),
            'model':'explicit-provider-model','protocol':protocol,'token_parameter':'max_completion_tokens' if protocol=='chat_completions' else 'max_output_tokens',
            'max_output_tokens':2048,'timeout_seconds':5}

class ProviderSupportTests(unittest.TestCase):
    def setUp(self):
        self.s=ArchitectService();self.s.refresh(fixture());self.context=self.s.planning_context({'text':'参考图换装'})
        c=self.context['context']['candidates'][0]
        self.proposal={'context_digest':self.context['context_digest'],'template_id':c['template_id'],'evidence_ids':c['evidence_ids'],
                       'reason':'Owned fake response, not real model evidence','steps':[{'title':'Concrete graph','node_classes':c['node_classes']}]}
        self.payload={'text':'参考图换装','profile':'stable','context_digest':self.context['context_digest'],'profile_digest':_sha(profile()),'confirm_external_text':True,'confirm_one_paid_call':True}
        self.key_calls=0;self.send_calls=0
    def key(self,scope):self.key_calls+=1;return 'owned-fixture-key'
    def transport(self,p,key,raw,cancel):
        self.send_calls+=1;return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.proposal)}}]}).encode()
    def provider(self,**kw):return PlannerProvider(profile(),kw.get('reader',self.key),kw.get('approval',lambda scope:True),kw.get('transport',self.transport))
    def test_default_disabled(self):
        self.assertFalse(self.s.api_support({})['configured'])
        with self.assertRaises(InputError):self.s.propose_api(self.payload)
        self.assertEqual(self.key_calls,0)
    def test_profile_checks_do_not_connect(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError('no DNS')):
            r=self.s.api_profile_check({'text':'参考图换装','profile_config':profile()})
        self.assertFalse(r['sent']);self.assertFalse(r['credential_read'])
    def test_chat_tokens_and_no_temperature(self):
        p=profile();body=json.loads(build_request(self.context,p));self.assertEqual(body['max_completion_tokens'],2048)
        self.assertNotIn('temperature',body);self.assertNotIn('max_tokens',body)
        p['token_parameter']='max_tokens';self.assertIn('max_tokens',json.loads(build_request(self.context,p)))
    def test_responses_request(self):
        b=json.loads(build_request(self.context,profile('responses')))
        self.assertFalse(b['store']);self.assertFalse(b['stream']);self.assertNotIn('messages',b)
    def test_secret_field_rejected(self):
        with self.assertRaises(InputError):validate_profile({**profile(),'api_key':'never-send'})
    def test_endpoint_restrictions(self):
        for endpoint in ('http://example.invalid/v1/chat/completions','https://a:b@example.invalid/v1/chat/completions','https://example.invalid/dashboard','https://example.invalid:8443/v1/chat/completions','https://example.invalid/v1/chat/completions?key=x'):
            with self.subTest(endpoint=endpoint),self.assertRaises(InputError):validate_profile({**profile(),'endpoint':endpoint})
    def test_protocol_token_mismatch(self):
        for kw in ({'protocol':[]},{'protocol':'unknown'},{'token_parameter':[]},{'token_parameter':'max_output_tokens'},{'max_output_tokens':True},{'timeout_seconds':31}):
            with self.assertRaises(InputError):validate_profile({**profile(),**kw})
    def test_sanitized_context_has_real_graph(self):
        c=self.context['context']['candidates'][0]
        self.assertEqual(c['maturity'],'experimental');self.assertEqual(c['runtime_profile'],'stable')
        self.assertEqual(len(c['graph_contract']),23);self.assertTrue(c['node_contracts'])
        self.assertNotIn('person.png',self.context['manual_prompt']);self.assertNotIn('available_choices',self.context['manual_prompt'])
    def test_approval_before_key(self):
        for value in (False,1,'true',None):
            with self.assertRaises(InputError):self.provider(approval=lambda _:value).propose(self.s,self.payload)
        self.assertEqual(self.key_calls,0);self.assertEqual(self.send_calls,0)
    def test_confirmations_before_key(self):
        for key in ('confirm_external_text','confirm_one_paid_call'):
            with self.assertRaises(InputError):self.provider().propose(self.s,{**self.payload,key:False})
        self.assertEqual(self.key_calls,0)
    def test_stale_context_before_key(self):
        changed=fixture();changed['LoadImage']['input']['required']['new_required']=['STRING'];self.s.refresh(changed)
        with self.assertRaises(InputError):self.provider().propose(self.s,self.payload)
        self.assertEqual(self.key_calls,0)
    def test_valid_fixture_returns_grounded_plan(self):
        r=self.provider().propose(self.s,self.payload)
        self.assertTrue(r['api_called']);self.assertFalse(r['execution_authorized']);self.assertFalse(r['actual_cost_known'])
        self.assertEqual(self.send_calls,1);self.assertNotIn('owned-fixture-key',json.dumps(r))
    def test_fake_node_response_rejected(self):
        self.proposal['steps'][0]['node_classes']=['FakeNode']
        with self.assertRaises(InputError):self.provider().propose(self.s,self.payload)
        self.assertEqual(self.send_calls,1)
    def test_private_transport_errors_not_leaked(self):
        def fail(*_):raise InputError('private-secret')
        with self.assertRaises(InputError) as ctx:self.provider(transport=fail).propose(self.s,self.payload)
        self.assertNotIn('private-secret',str(ctx.exception));self.assertIn('费用可能未知',str(ctx.exception))
    def test_cancel_before_key(self):
        event=threading.Event();event.set()
        with self.assertRaises(InputError):self.provider().propose(self.s,self.payload,event)
        self.assertEqual(self.key_calls,0)
    def test_late_cancel_no_result(self):
        def late(p,k,r,c):
            value=self.transport(p,k,r,c);c.set();return value
        with self.assertRaises(InputError):self.provider(transport=late).propose(self.s,self.payload)
    def test_configuration_drift(self):
        provider=self.provider()
        def approval(_):provider.profile['model']='changed';return True
        provider.approval=approval
        with self.assertRaises(InputError):provider.propose(self.s,self.payload)
        self.assertEqual(self.key_calls,0)
    def test_nonpublic_dns(self):
        for ip in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:8.8.8.8'):
            with patch('socket.getaddrinfo',return_value=[(None,None,None,None,(ip,443))]),self.assertRaises(InputError):public_addresses('owned.invalid')
    def test_all_dns_records_checked(self):
        rows=[(None,None,None,None,('8.8.8.8',443)),(None,None,None,None,('127.0.0.1',443))]
        with patch('socket.getaddrinfo',return_value=rows),self.assertRaises(InputError):public_addresses('owned.invalid')
    def test_public_dns_is_pure_mock(self):
        with patch('socket.getaddrinfo',return_value=[(None,None,None,None,('8.8.8.8',443))]):self.assertEqual(public_addresses('owned.invalid'),['8.8.8.8'])
    def test_responses_parser(self):
        raw={'status':'completed','output':[{'type':'reasoning'},{'type':'message','status':'completed','role':'assistant','content':[{'type':'output_text','text':json.dumps(self.proposal)}]}]}
        self.assertEqual(parse_response(json.dumps(raw).encode(),'responses'),self.proposal)
        for change in ({'status':'incomplete'},{'output':[{'type':'function_call'}]},{'output':[1]}):
            with self.assertRaises(InputError):parse_response(json.dumps({**raw,**change}).encode(),'responses')
    def test_only_one_inflight_call(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def slow(p,k,r,c):entered.set();release.wait(2);return self.transport(p,k,r,c)
        provider=self.provider(transport=slow)
        def run():
            try:provider.propose(self.s,self.payload)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=run);worker.start();self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(InputError):provider.propose(self.s,self.payload)
        finally:release.set();worker.join(2)
        self.assertFalse(errors);self.assertFalse(worker.is_alive())
