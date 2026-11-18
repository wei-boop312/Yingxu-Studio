import copy
import json
import unittest
from unittest.mock import patch
from test_tryon import fixture
from workflow_architect.service import ArchitectService
from workflow_architect.api_planner import build_chat_request,parse_chat_response
from workflow_architect.errors import InputError

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.s=ArchitectService();self.info=fixture();self.s.refresh(self.info)
        self.context=self.s.planning_context({'text':'参考图换装'})
        c=self.context['context']['candidates'][0]
        self.proposal={'context_digest':self.context['context_digest'],'template_id':c['template_id'],
                       'evidence_ids':c['evidence_ids'],'reason':'Use exact tested graph, manual quality review',
                       'steps':[{'title':'Concrete graph nodes','node_classes':c['node_classes']}]}
    def check(self,**kw):return self.s.validate_proposal({'text':'参考图换装','proposal':{**self.proposal,**kw}})
    def envelope(self,content=None,finish='stop',**message):
        return json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(self.proposal) if content is None else content,**message}}]}).encode()
    def test_import_concrete(self):
        r=self.check();self.assertEqual(r['status'],'grounded-proposal-not-executed');self.assertFalse(r['execution_authorized']);self.assertFalse(r['quality_approved'])
    def test_no_filenames_exported(self):
        prompt=self.context['manual_prompt'];self.assertNotIn('person.png',prompt);self.assertNotIn('garment.png',prompt);self.assertNotIn('node_signatures',prompt)
    def test_no_compatible_no_plan(self):
        c=self.s.planning_context({'text':'Wan视频生成'});self.assertEqual(c['context']['candidates'],[])
        with self.assertRaises(InputError):build_chat_request(c,{'endpoint':'https://example.invalid/v1/chat/completions','model':'explicit-id','max_tokens':1024})
    def test_digest_mismatch(self):
        with self.assertRaises(InputError):self.check(context_digest='x'*64)
    def test_new_registry_stales_plan(self):
        self.info['LoadImage']['input']['required']['new_required']=['STRING']
        self.s.refresh(self.info)
        with self.assertRaises(InputError):self.check()
    def test_identical_refresh_keeps_plan(self):
        self.s.refresh(self.info);self.check()
    def test_new_image_choice_keeps_plan(self):
        self.info['LoadImage']['input']['required']['image'][0].append('new-upload.png')
        self.s.refresh(self.info);self.check()
    def test_different_query_stales_plan(self):
        with self.assertRaises(InputError):self.s.validate_proposal({'text':'换装低腰','proposal':self.proposal})
    def test_invented_template(self):
        with self.assertRaises(InputError):self.check(template_id='invented')
    def test_bad_sources(self):
        for ids in ([],['invented'],['S03','S03'],[{}]):
            with self.assertRaises(InputError):self.check(evidence_ids=ids)
    def test_abstract_steps_rejected(self):
        for steps in ([],[{'title':'想办法','node_classes':[]}],[{'title':'run','node_classes':['FakeNode']}],
                      [{'title':'load','node_classes':['LoadImage']}]):
            with self.assertRaises(InputError):self.check(steps=steps)
    def test_extra_commands_rejected(self):
        with self.assertRaises(InputError):self.check(execute=True)
    def test_reason_bound(self):
        for r in ('',None,'x'*1001):
            with self.assertRaises(InputError):self.check(reason=r)
    def test_no_compile(self):
        with patch.object(self.s,'compile',side_effect=AssertionError()):self.check()
    def test_adapter_no_send(self):
        request=build_chat_request(self.context,{'endpoint':'https://example.invalid/v1/chat/completions','model':'explicit-model-id','max_tokens':1024})
        self.assertFalse(request['sent']);self.assertFalse(request['cost_authorized']);self.assertEqual(request['body']['model'],'explicit-model-id')
        self.assertFalse(request['body']['stream']);self.assertNotIn('Authorization',str(request))
    def test_endpoint_rejection(self):
        for url in ('http://x/v1/chat/completions','https://user:pass@x/v1/chat/completions','https://x/v1/chat/completions?key=secret','https://x/other'):
            with self.assertRaises(InputError):build_chat_request(self.context,{'endpoint':url,'model':'id','max_tokens':1024})
    def test_config_bounds(self):
        for kw in ({'api_key':'secret'},{'model':''},{'max_tokens':True},{'max_tokens':99999}):
            with self.assertRaises(InputError):build_chat_request(self.context,{'endpoint':'https://example.invalid/v1/chat/completions','model':'id','max_tokens':1024,**kw})
    def test_complete_chat_response(self):
        self.assertEqual(parse_chat_response(self.envelope()),self.proposal)
    def test_bad_chat_envelopes(self):
        for raw in (b'[]',b'{}',b'{"choices":[1]}',b'{"choices":[{"finish_reason":"stop","message":2}]}',b'x',b'\xff',b'x'*131073):
            with self.assertRaises(InputError):parse_chat_response(raw)
    def test_truncation_and_tools(self):
        for raw in (self.envelope(finish='length'),self.envelope(tool_calls=[{'name':'run'}]),self.envelope(refusal='no')):
            with self.assertRaises(InputError):parse_chat_response(raw)
    def test_markdown_and_duplicate(self):
        for content in ('```json\n{}\n```','{"a":1,"a":2}','{"a":NaN}'):
            with self.assertRaises(InputError):parse_chat_response(self.envelope(content))
    def test_roundtrip_no_network(self):
        parsed=parse_chat_response(self.envelope());self.assertEqual(self.s.validate_proposal({'text':'参考图换装','proposal':parsed})['proposal'],self.proposal)
