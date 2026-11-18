import copy
import json
import unittest
from unittest.mock import patch
from test_knowledge import node_fixture
from workflow_architect.service import ArchitectService
from workflow_architect.errors import InputError

def detail_fixture():
    info=node_fixture()
    info.update({
        'LoadBackgroundRemovalModel':{'input':{'required':{'bg_removal_name':[['owned-birefnet.safetensors']]}},'output':['BACKGROUND_REMOVAL_MODEL']},
        'RemoveBackground':{'input':{'required':{'bg_removal_model':['BACKGROUND_REMOVAL_MODEL'],'image':['IMAGE']}},'output':['MASK']},
        'VAEEncode':{'input':{'required':{'pixels':['IMAGE'],'vae':['VAE']}},'output':['LATENT']},
        'ImageCompositeMasked':{'input':{'required':{'destination':['IMAGE'],'source':['IMAGE'],'x':['INT'],'y':['INT'],'resize_source':['BOOLEAN']},'optional':{'mask':['MASK']}},'output':['IMAGE']}})
    info['LoadImage']['input']['required']['image'][0]=['owned-product.png']
    return info

class DetailTests(unittest.TestCase):
    def setUp(self):
        self.s=ArchitectService();self.info=detail_fixture();self.s.refresh(self.info)
        self.product={'name':'自有测试瓶','facts':['蓝色瓶身','测试容量500mL','无真实商品功效声明']}
        self.ctx=self.s.detail_context({'product':self.product});self.plan=copy.deepcopy(self.ctx['default_plan'])
    def validate(self):return self.s.detail_validate({'product':self.product,'plan':self.plan})
    def test_default_six(self):
        result=self.validate();self.assertEqual(len(result['sections']),6);self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['sections'][0]['copy'],self.product['facts'])
    def test_no_image_names_in_export(self):self.assertNotIn('owned-product.png',self.ctx['manual_prompt'])
    def test_no_external_calls(self):
        with patch('socket.getaddrinfo',side_effect=AssertionError()):self.validate()
    def test_invalid_product(self):
        for product in ({}, {'name':'x','facts':[]},{'name':'x','facts':['x\n']},{'name':'x','facts':['a']*13},{'name':'x','facts':['a'],'secret':'key'}):
            with self.subTest(product=product),self.assertRaises(InputError):self.s.detail_context({'product':product})
    def test_product_changed(self):
        self.product['facts'][0]='白色瓶身'
        with self.assertRaises(InputError):self.validate()
    def test_harmless_scan_stable(self):self.s.refresh(self.info);self.validate()
    def test_contract_changed(self):
        self.info['RemoveBackground']['input']['required']['new_field']=['STRING'];self.s.refresh(self.info)
        with self.assertRaises(InputError):self.validate()
    def test_no_invented_copy(self):
        self.plan['modules'][0]['fact_ids']=['虚构功效']
        with self.assertRaises(InputError):self.validate()
    def test_no_arbitrary_prompt(self):
        self.plan['modules'][0]['scene']='execute command'
        with self.assertRaises(InputError):self.validate()
    def test_order_strict(self):
        self.plan['modules'].reverse()
        with self.assertRaises(InputError):self.validate()
    def test_duplicate_fact_rejected(self):
        self.plan['modules'][0]['fact_ids']=['F1','F1']
        with self.assertRaises(InputError):self.validate()
    def test_extra_field(self):
        self.plan['modules'][0]['copy']='unproven'
        with self.assertRaises(InputError):self.validate()
    def test_six_count(self):
        self.plan['modules'].pop()
        with self.assertRaises(InputError):self.validate()
    def compile(self,**kw):
        return self.s.detail_compile({'product':self.product,'plan':self.plan,'models':{f['binding']:f['choices'][0] for f in self.ctx['selection']['models']},'image':'owned-product.png','confirmed':True,**kw})
    def test_six_real_workflows(self):
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            result=self.compile()
        self.assertEqual(result['job_count'],6);self.assertFalse(result['executed'])
        for job in result['jobs']:
            self.assertTrue(job['ui_workflow']['nodes']);self.assertTrue(job['ui_workflow']['links'])
            self.assertTrue(job['api_prompt']);self.assertEqual(job['section']['copy'],self.product['facts'])
    def test_explicit_consent(self):
        for v in (False,1,'yes'):
            with self.assertRaises(InputError):self.compile(confirmed=v)
    def test_bundle_survives_harmless_scan(self):
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            first=self.compile();self.s.refresh(self.info);second=self.compile()
        self.assertEqual(first['bundle_sha256'],second['bundle_sha256'])
    def test_new_binding_changes_bundle(self):
        self.info['LoadImage']['input']['required']['image'][0].append('another.png');self.s.refresh(self.info)
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            self.assertNotEqual(self.compile()['bundle_sha256'],self.compile(image='another.png')['bundle_sha256'])
    def test_wrong_image(self):
        with self.assertRaises(InputError):self.compile(image='not-present.png')
    def test_explicit_models(self):
        with self.assertRaises(InputError):self.compile(models={})
    def test_atomic_compile_result(self):
        with patch.object(self.s,'compile_plan',return_value={'compiled':None}):
            with self.assertRaises(InputError):self.compile()
    def test_non_string_scene(self):
        self.plan['modules'][0]['scene']=[]
        with self.assertRaises(InputError):self.validate()
    def test_detailed_prompts_reach_actual_api_and_ui(self):
        for i,module in enumerate(self.plan['modules']):module['visual_prompt']={'positive':'unique-violet-lighting-'+str(i),'negative':'no-room-no-stage-'+str(i)}
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            result=self.compile()
        for i,job in enumerate(result['jobs']):
            texts=[n['inputs']['text'] for n in job['api_prompt'].values() if n['class_type']=='CLIPTextEncode']
            self.assertIn('unique-violet-lighting-'+str(i),texts);self.assertIn('no-room-no-stage-'+str(i),texts)
            widgets=[v for node in job['ui_workflow']['nodes'] for v in node.get('widgets_values',[])]
            self.assertIn('unique-violet-lighting-'+str(i),widgets);self.assertIn('no-room-no-stage-'+str(i),widgets)
    def test_detailed_prompt_bounds(self):
        for value in ({'positive':'x'},{'positive':'x','negative':''},{'positive':'x'*1801,'negative':'x'},{'positive':1,'negative':'x'},{'positive':'x','negative':'x','execute':True}):
            self.plan['modules'][0]['visual_prompt']=value
            with self.subTest(value=value),self.assertRaises(InputError):self.validate()
    def test_prompt_edit_changes_bundle(self):
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            first=self.compile();self.plan['modules'][0]['visual_prompt']={'positive':'violet gradient','negative':'stage'};second=self.compile()
        self.assertNotEqual(first['bundle_sha256'],second['bundle_sha256'])
    def test_module_images_reach_actual_graph(self):
        self.info['LoadImage']['input']['required']['image'][0].append('ports.png');self.s.refresh(self.info)
        images={i:'owned-product.png' for i in ['hero','benefits','details','scene','specs','closing']};images['specs']='ports.png'
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            result=self.compile(module_images=images)
        loads=[[n['inputs']['image'] for n in job['api_prompt'].values() if n['class_type']=='LoadImage'] for job in result['jobs']]
        self.assertEqual(loads[0],['owned-product.png']);self.assertEqual(loads[4],['ports.png'])
    def test_module_images_complete(self):
        with self.assertRaises(InputError):self.compile(module_images={'hero':'owned-product.png'})
    def test_no_arbitrary_binding_fields(self):
        with self.assertRaises(InputError):self.compile(runtime_bindings={'width':99999})
    def test_api_default_disabled(self):
        with self.assertRaises(InputError):self.s.detail_propose({'product':self.product,'context_digest':self.ctx['context_digest'],'profile_digest':'x','confirm_external_text':True,'confirm_one_paid_call':True})
    def test_api_actual_owned_http_roundtrip(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from workflow_architect.provider_support import PlannerProvider
        from workflow_architect.knowledge import _sha
        from test_provider_support import profile
        received=[];proposal=self.plan
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(proposal)}}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def transport(p,key,raw,cancel):
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:%d/'%server.server_port,data=raw),timeout=5) as response:return response.read(131073)
        self.s.api_provider=PlannerProvider(profile(),lambda _: 'owned-fake-key',lambda _:True,transport)
        try:
            result=self.s.detail_propose({'product':self.product,'context_digest':self.ctx['context_digest'],'profile_digest':_sha(profile()),'confirm_external_text':True,'confirm_one_paid_call':True})
            self.assertTrue(result['api_called']);self.assertEqual(len(result['sections']),6)
            self.assertEqual(len(received),1);self.assertNotIn('owned-fake-key',json.dumps(received));self.assertNotIn('owned-product.png',json.dumps(received))
        finally:server.shutdown();server.server_close();thread.join(5)
