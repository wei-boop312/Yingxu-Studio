import copy
import json
import unittest
from unittest.mock import patch
from test_tryon import fixture
from workflow_architect.service import ArchitectService
from workflow_architect.foundation import audit_template
from workflow_architect.settings import TEMPLATE_DIR
from workflow_architect.tryon import MODELS
from workflow_architect.errors import InputError

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.s=ArchitectService();self.info=fixture();self.s.refresh(self.info)
        self.template=json.loads((TEMPLATE_DIR/'experimental/klein_tryon.json').read_text())
    def report(self,text='参考图换装',**kw):return self.s.foundation_report({'text':text,**kw})
    def test_concrete_owned_candidate_with_primary_references(self):
        r=self.report();self.assertEqual(len(r['candidates']),1)
        c=r['candidates'][0];self.assertTrue(c['experimental']);self.assertTrue(c['audit']['compatible'])
        self.assertEqual(set(c['provenance']['upstream_reference_ids']),{'S01','S03','S04'})
        self.assertTrue(set(c['provenance']['upstream_reference_ids'])<={s['id'] for s in r['sources']})
        self.assertFalse(r['api_called']);self.assertFalse(r['execution_authorized']);self.assertFalse(r['small_model_loaded'])
        self.assertEqual(len(c['audit']['signature_sha256']),64)
    def test_fixed_named_model_not_any_weight(self):
        self.info['UNETLoader']['input']['required']['unet_name']=[['another.safetensors']];self.s.refresh(self.info)
        r=self.report();a=r['candidates'][0]['audit'];self.assertFalse(a['compatible'])
        self.assertIn({'code':'missing-model','binding':'diffusion','required_name':MODELS['diffusion']},a['issues'])
    def test_missing_node(self):
        del self.info['ReferenceLatent'];self.s.refresh(self.info)
        self.assertTrue(any(i['code']=='missing-node' for i in self.report()['candidates'][0]['audit']['issues']))
    def test_required_signature_drift(self):
        self.info['Flux2Scheduler']['input']['required']['new_required']=['INT'];self.s.refresh(self.info)
        self.assertTrue(any(i['code']=='missing-input' for i in self.report()['candidates'][0]['audit']['issues']))
    def test_enum_drift(self):
        self.info['CLIPLoader']['input']['required']['type']=[['other']];self.s.refresh(self.info)
        self.assertTrue(any(i['code']=='unsupported-enum' for i in self.report()['candidates'][0]['audit']['issues']))
    def test_link_type_drift(self):
        self.info['VAEEncode']['output']=['IMAGE'];self.s.refresh(self.info)
        self.assertTrue(any(i['code']=='link-type-mismatch' for i in self.report()['candidates'][0]['audit']['issues']))
    def test_invalid_link(self):
        template=copy.deepcopy(self.template);template['nodes'][3]['inputs']['clip']={'link':['missing',0]}
        self.assertTrue(any(i['code']=='invalid-link' for i in audit_template(template,self.s.registry.current())['issues']))
    def test_unknown_field(self):
        with self.assertRaises(InputError):self.report(execute=True)
    def test_query_bounds(self):
        for q in ('',None,'x'*4001):
            with self.assertRaises(InputError):self.report(q)
    def test_profile(self):
        self.assertFalse(self.report(profile='low_vram')['candidates'][0]['audit']['compatible'])
        with self.assertRaises(InputError):self.report(profile='arbitrary')
    def test_video_never_becomes_executable(self):
        r=self.report('Wan首尾帧视频生成');self.assertEqual(r['candidates'],[])
        self.assertEqual(r['status'],'reference-only-no-executable-template')
    def test_no_compile_or_models_loaded(self):
        with patch.object(self.s,'compile',side_effect=AssertionError('must not compile')):
            self.assertTrue(self.report()['candidates'])
    def test_main_image_and_refinement_sources(self):
        for text in ('电商产品图生成主图详情页','局部重绘精修'):
            r=self.report(text);self.assertTrue(r['candidates']);self.assertTrue(r['sources'])
    def test_unscanned_rejected(self):
        with self.assertRaises(InputError):ArchitectService().foundation_report({'text':'换装'})
    def test_weight_integrity_not_claimed(self):
        for c in self.report()['candidates']:
            self.assertFalse(c['audit']['file_integrity_verified']);self.assertFalse(c['audit']['runtime_verified'])
