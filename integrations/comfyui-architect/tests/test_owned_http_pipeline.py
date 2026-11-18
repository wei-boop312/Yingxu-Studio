"""Actual owned loopback HTTP, synthetic responses; never a supplier call."""
import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from test_tryon import fixture
from test_knowledge import node_fixture
from test_provider_support import profile
from workflow_architect.service import ArchitectService
from workflow_architect.provider_support import PlannerProvider
from workflow_architect.knowledge import _sha

class OwnedHttpPipelineTests(unittest.TestCase):
    def run_case(self,query):
        info=fixture() if '换装' in query else node_fixture()
        info['LoadImage']['input']['required']['image'][0]=['person.png','garment.png']
        info.update({
            'LoadBackgroundRemovalModel':{'input':{'required':{'bg_removal_name':[['owned-birefnet.safetensors']]}},'output':['BACKGROUND_REMOVAL_MODEL']},
            'RemoveBackground':{'input':{'required':{'bg_removal_model':['BACKGROUND_REMOVAL_MODEL'],'image':['IMAGE']}},'output':['MASK']},
            'VAEEncode':{'input':{'required':{'pixels':['IMAGE'],'vae':['VAE']}},'output':['LATENT']},
            'VAEEncodeForInpaint':{'input':{'required':{'pixels':['IMAGE'],'vae':['VAE'],'mask':['MASK'],'grow_mask_by':['INT']}},'output':['LATENT']},
            'ImageCompositeMasked':{'input':{'required':{'destination':['IMAGE'],'source':['IMAGE'],'x':['INT'],'y':['INT'],'resize_source':['BOOLEAN']},'optional':{'mask':['MASK']}},'output':['IMAGE']},
        })
        service=ArchitectService();service.refresh(info)
        context=service.planning_context({'text':query});self.assertTrue(context['context']['candidates'])
        c=context['context']['candidates'][0]
        proposal={'context_digest':context['context_digest'],'template_id':c['template_id'],
                  'evidence_ids':c['evidence_ids'],'reason':'Owned synthetic fixture, not model intelligence proof',
                  'steps':[{'title':'Concrete registered nodes','node_classes':c['node_classes']}]}
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(proposal)}}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def transport(p,key,raw,cancel):
            self.assertEqual(key,'owned-fake-key-only')
            request=urllib.request.Request('http://127.0.0.1:%s/'%server.server_port,data=raw,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=5) as response:return response.read(131073)
        try:
            service.api_provider=PlannerProvider(profile(),lambda _: 'owned-fake-key-only',lambda _:True,transport)
            accepted=service.propose_api({'text':query,'profile':'stable','context_digest':context['context_digest'],
                'profile_digest':_sha(profile()),'confirm_external_text':True,'confirm_one_paid_call':True})
            # A harmless rescan between planning and compilation must work.
            service.refresh(info)
            preview=service.compile_plan({'text':query,'proposal':accepted['proposal']})
            models={item['binding']:item['choices'][0] for item in preview['selection']['models']}
            images={role:('garment.png' if role=='garment_image' else 'person.png') for role in preview['selection']['image_roles']}
            with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
                result=service.compile_plan({'text':query,'proposal':accepted['proposal'],'build':True,'authorized':True,
                    'models':models,'images':images,'prompt':'Owned studio product image test','width':512,'height':512})
            self.assertTrue(result['validation']['valid']);self.assertTrue(result['compiled']['ui_workflow']['nodes'])
            self.assertTrue(result['compiled']['ui_workflow']['links']);self.assertFalse(result['execution_authorized'])
            self.assertEqual(len(received),1);self.assertNotIn('owned-fake-key-only',json.dumps(result))
            self.assertNotIn('person.png',json.dumps(received))
            return result
        finally:server.shutdown();server.server_close();thread.join(5)
    def test_tryon(self):self.run_case('参考图换装')
    def test_main_product(self):self.run_case('制作电商主图详情页')
    def test_refinement(self):
        result=self.run_case('局部重绘精修图片');graph=result['compiled']['api_prompt']
        save=next(n for n in graph.values() if n['class_type']=='SaveImage')
        composite=graph[save['inputs']['images'][0]]
        self.assertEqual(composite['class_type'],'ImageCompositeMasked')
        self.assertFalse(composite['inputs']['resize_source'])
        source=graph[composite['inputs']['destination'][0]]
        self.assertEqual(source['class_type'],'LoadImage')
        self.assertEqual(composite['inputs']['mask'],[composite['inputs']['destination'][0],1])
