import json
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from workflow_architect import offline_ai as local
from workflow_architect.errors import InputError
from workflow_architect.knowledge import KnowledgeBase
from workflow_architect.service import ArchitectService
from test_knowledge import node_fixture
from test_tryon import fixture

def envelope(value,finish='stop'):
    return json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(value)}}]}).encode()

class OfflineAiTests(unittest.TestCase):
    def setUp(self):
        owned=tempfile.TemporaryDirectory();self.addCleanup(owned.cleanup)
        patcher=patch.object(local,'KNOWLEDGE_PATH',Path(owned.name)/'knowledge.sqlite3');patcher.start();self.addCleanup(patcher.stop)
        self.service=ArchitectService();self.service.refresh(node_fixture())
        self.payload={'text':'参考图换装不是改色','mode':'knowledge','confirmed':True}

    def response(self,body,**kwargs):
        context=json.loads(body['messages'][1]['content'])
        if 'document_ids' in body['response_format']['schema']['properties']:
            return envelope({'document_ids':[context['documents'][0]['id']]})
        return envelope({'excerpts':[{'document_id':context['documents'][0]['id'],'quote':context['documents'][0]['text'][:80]}]})

    def test_knowledge_actual_retrieval_and_strict_local_request(self):
        with patch.object(local,'transport',side_effect=self.response) as send:
            result=local.assist(self.service,self.payload)
        self.assertFalse(result['execution_authorized']);self.assertFalse(result['external_api_called'])
        self.assertTrue(result['small_model_called']);self.assertEqual(len(send.call_args.args),1)
        body=send.call_args.args[0]
        self.assertEqual(body['model'],'wa-qwen3-4b');self.assertEqual(body['chat_template_kwargs'],{'enable_thinking':False})
        self.assertNotIn('tools',body)

    def test_consent_and_unknown_fields_never_send(self):
        for payload in ({**self.payload,'confirmed':1},{**self.payload,'endpoint':'https://example.com'},
                        {**self.payload,'text':''},{**self.payload,'text':'x'*1001},{**self.payload,'mode':'exec'}):
            with self.subTest(payload=payload),patch.object(local,'transport') as send,self.assertRaises(InputError):
                local.assist(self.service,payload)
            send.assert_not_called()

    def test_unknown_citation_rejected(self):
        with patch.object(local,'transport',return_value=envelope({'answer':'fake','citations':['unknown']})),self.assertRaises(InputError):
            local.assist(self.service,self.payload)

    def test_truncated_or_tools_response_rejected(self):
        for raw in (envelope({'answer':'a','citations':[]},'length'),b'not json',b'{"choices":[{"finish_reason":"stop","message":{"tool_calls":[{}],"content":"{}"}}]}'):
            with patch.object(local,'transport',return_value=raw),self.assertRaises(InputError):local.assist(self.service,self.payload)

    def test_cancel_before_and_after_inference(self):
        cancel=threading.Event();cancel.set()
        with patch.object(local,'transport') as send,self.assertRaises(InputError):local.assist(self.service,self.payload,cancel)
        send.assert_not_called();cancel.clear()
        def complete(body,**kwargs):cancel.set();return self.response(body)
        with patch.object(local,'transport',side_effect=complete),self.assertRaises(InputError):local.assist(self.service,self.payload,cancel)

    def test_slot_busy_no_queue(self):
        local.SLOT.acquire()
        try:
            with patch.object(local,'transport') as send,self.assertRaises(InputError):local.assist(self.service,self.payload)
            send.assert_not_called()
        finally:local.SLOT.release()

    def test_registry_drift_during_answer_rejected(self):
        def changed(body,**kwargs):
            self.service.refresh({})
            return self.response(body)
        with patch.object(local,'transport',side_effect=changed),self.assertRaises(InputError):local.assist(self.service,self.payload)

    def test_no_template_does_not_call_model(self):
        with patch.object(local,'transport') as send:
            result=local.assist(self.service,{**self.payload,'text':'生成视频','mode':'plan'})
        send.assert_not_called();self.assertEqual(result['status'],'blocked-no-compatible-template')

    def test_plan_uses_existing_full_validator(self):
        self.service.refresh(fixture())
        def choose(body):
            context=json.loads(body['messages'][1]['content']);candidate=context['candidates'][0]
            return envelope({'context_digest':body['response_format']['schema']['properties']['context_digest']['const'],
                'template_id':candidate['template_id'],'evidence_ids':candidate['evidence_ids'],
                'reason':'选择实际兼容模板，人工绑定模型与图片。','steps':[{'title':'完整实际模板节点','node_classes':candidate['node_classes']}]})
        with patch.object(local,'transport',side_effect=choose):
            result=local.assist(self.service,{**self.payload,'text':'参考图换装','mode':'plan'})
        self.assertEqual(result['status'],'grounded-local-plan-not-executed');self.assertFalse(result['accepted']['execution_authorized'])

    def test_fabricated_plan_fails_before_compilation(self):
        self.service.refresh(fixture())
        with patch.object(local,'transport',return_value=envelope({'template_id':'invented'})),self.assertRaises(InputError):
            local.assist(self.service,{**self.payload,'text':'参考图换装','mode':'plan'})

    def test_request_budget_before_network(self):
        with patch.object(local.http.client,'HTTPConnection') as connection,self.assertRaises(InputError):local.transport({'text':'x'*140000})
        connection.assert_not_called()

    def test_status_unavailable_no_cloud_fallback(self):
        with patch.object(local.http.client,'HTTPConnection',side_effect=OSError):
            self.assertFalse(local.status({})['ready'])
        with self.assertRaises(InputError):local.status({'endpoint':'anything'})

    def test_only_explicit_project_database_is_reused(self):
        kb=KnowledgeBase(local.KNOWLEDGE_PATH);revision=kb.revision;kb.close()
        reopened=local.open_knowledge()
        try:self.assertEqual(reopened.revision,revision)
        finally:reopened.close()

    def test_still_image_retrieval_does_not_require_video_prompt_cards(self):
        kb=KnowledgeBase()
        try:
            result=local.retrieve(kb,'商品Logo和非编辑区文字怎么保护',self.service.registry.current())
            self.assertTrue(result['results'])
            self.assertFalse(any(d.get('prompt_engine') in ('image-to-video','text-to-video') for d in result['results']))
        finally:kb.close()

    def test_reranking_is_bounded_to_actual_evidence(self):
        import time
        documents=[{'id':'owned:a','title':'owned','text':'test'}]
        for value in ({'document_ids':['unknown']},{'document_ids':[{}]},{'document_ids':['owned:a','owned:a']}):
            with patch.object(local,'transport',return_value=envelope(value)),self.assertRaises(InputError):
                local.rerank('test',documents,None,time.monotonic())
        with patch.object(local,'transport',return_value=envelope({'document_ids':[]})):
            self.assertEqual(local.rerank('test',documents,None,time.monotonic()),[])

    def test_excerpts_cannot_invent_contrast_advice_or_extra_fields(self):
        docs=[{'id':'owned','title':'原图保护','text':'手脚和背景对照原图；必要时区域外回贴原像素。'}]
        for value in ({'excerpts':[{'document_id':'owned','quote':'确保手脚与背景的对比度与原图一致。'}]},
                      {'excerpts':[{'document_id':'other','quote':docs[0]['text']}]},
                      {'excerpts':[{'document_id':'owned','quote':'手脚'}]},
                      {'excerpts':[{'document_id':'owned','quote':docs[0]['text']}],'answer':'额外建议'}):
            with self.subTest(value=value),self.assertRaises(InputError):local.validate_excerpts(value,docs)

    def test_exact_excerpts_are_rendered_without_freeform_generation(self):
        docs=[{'id':'owned','title':'原图保护','text':'手脚和背景对照原图；必要时区域外回贴原像素。'}]
        result=local.validate_excerpts({'excerpts':[{'document_id':'owned','quote':docs[0]['text']}]},docs)
        self.assertEqual(result['citations'],['owned']);self.assertEqual(result['answer'],'【原图保护】\n'+docs[0]['text'])
