import unittest
from unittest.mock import patch
from test_tryon import fixture
from workflow_architect.service import ArchitectService
from workflow_architect.tryon import MODELS
from workflow_architect.errors import InputError

class PlanCompileTests(unittest.TestCase):
    def setUp(self):
        self.s=ArchitectService();self.info=fixture();self.s.refresh(self.info)
        self.query='参考图换装';self.make_proposal()
    def make_proposal(self):
        context=self.s.planning_context({'text':self.query});c=context['context']['candidates'][0]
        self.p={'context_digest':context['context_digest'],'template_id':c['template_id'],'evidence_ids':c['evidence_ids'],
                'reason':'Concrete selected graph','steps':[{'title':'All required classes','node_classes':c['node_classes']}]}
    def call(self,**kw):
        return self.s.compile_plan({'text':self.query,'proposal':self.p,'build':True,'authorized':True,
            'models':dict(MODELS),'images':{'person_image':'person.png','garment_image':'garment.png'},
            'prompt':'Match the reference outfit with opaque lining',**kw})
    def test_preview_no_compile(self):
        with patch.object(self.s,'compile',side_effect=AssertionError()):
            r=self.call(build=False)
        self.assertIsNone(r['compiled']);self.assertEqual(r['selection']['image_roles'],['person_image','garment_image'])
        self.assertEqual(r['selection']['output'],{'width':576,'height':768})
    def test_actual_graph_not_plan_record(self):
        r=self.call();self.assertTrue(r['validation']['valid']);self.assertEqual(r['phase'],3)
        ui=r['compiled']['ui_workflow'];self.assertEqual(len(ui['nodes']),23);self.assertTrue(ui['links'])
        ids={n['id'] for n in ui['nodes']}
        for link in ui['links']:self.assertIn(link[1],ids);self.assertIn(link[3],ids)
        self.assertFalse(r['execution_authorized']);self.assertFalse(r['quality_approved'])
    def test_wrong_image(self):
        for images in ({},{'person_image':'person.png','garment_image':'person.png'},{'person_image':'../secret','garment_image':'garment.png'}):
            with self.assertRaises(InputError):self.call(images=images)
    def test_explicit_models(self):
        with self.assertRaises(InputError):self.call(models={})
    def test_stale_proposal(self):
        self.info['LoadImage']['input']['required']['new_required']=['STRING']
        self.s.refresh(self.info)
        with self.assertRaises(InputError):self.call()
    def test_untrusted_accepted_wrapper_not_permission(self):
        with self.assertRaises(InputError):self.call(proposal={'proposal':self.p,'execution_authorized':True})
    def test_consent(self):
        for v in (False,1,'true'):
            with self.assertRaises(InputError):self.call(authorized=v)
    def test_dimensions(self):
        for w,h in ((1024,1024),(True,768),(575,768)):
            with self.assertRaises(InputError):self.call(width=w,height=h)
    def test_unknown_graph_rejected(self):
        with self.assertRaises(InputError):self.call(api_prompt={'fake':{}})
    def test_tryon_background_override_rejected(self):
        with self.assertRaises(InputError):self.call(background_prompt={'positive':'violet','negative':'stage'})
    def test_prompt_bounds(self):
        for v in ('',None,'x'*2001):
            with self.assertRaises(InputError):self.call(prompt=v)

    def test_resize_preview_and_actual_graph_preserve_explicit_dimensions(self):
        self.query='调整图片尺寸为256x256';self.make_proposal()
        preview=self.call(build=False)
        self.assertEqual(preview['selection']['output'],{'width':256,'height':256})
        self.assertEqual(preview['selection']['task'],'image_resize')
        self.assertNotIn('garment',preview['selection']['prompt_default'])
        result=self.call(models={},images={'source_image':'person.png'},width=256,height=256,prompt=preview['selection']['prompt_default'])
        scaler=next(n for n in result['compiled']['api_prompt'].values() if n['class_type']=='ImageScale')
        self.assertEqual((scaler['inputs']['width'],scaler['inputs']['height']),(256,256))
    def test_repair_finite(self):
        for attempt in (-1,3,True):
            with self.assertRaises(InputError):self.call(repair_attempt=attempt,feedback_code='PROMPT')
        with self.assertRaises(InputError):self.call(repair_attempt=1)
        self.assertEqual(self.call(repair_attempt=2,feedback_code='PROMPT')['repair_attempt'],2)
    def test_raw_error_not_accepted(self):
        with self.assertRaises(InputError):self.call(feedback_code='secret stack trace')
    def test_drift_during_compile(self):
        original=self.s.compile
        def drift(payload):
            r=original(payload);self.s.refresh(self.info);return r
        with patch.object(self.s,'compile',side_effect=drift),self.assertRaises(InputError):self.call()
    def test_generic_resize_concrete(self):
        self.query='把图片调整尺寸为576x768';self.make_proposal()
        r=self.call(models={},images={'source_image':'person.png'})
        self.assertTrue(r['compiled']['ui_workflow']['nodes']);self.assertTrue(r['validation']['valid'])
