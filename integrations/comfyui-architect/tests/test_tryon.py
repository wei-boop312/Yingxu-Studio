import json
import unittest
from unittest.mock import patch
from test_knowledge import node_fixture
from workflow_architect.service import ArchitectService
from workflow_architect.tryon import MODELS
from workflow_architect.settings import TEMPLATE_DIR
from workflow_architect.errors import InputError

def fixture():
    info = node_fixture()
    outputs = {'UNETLoader':['MODEL'],'CLIPLoader':['CLIP'],'VAELoader':['VAE'],'CLIPTextEncode':['CONDITIONING'],
               'ConditioningZeroOut':['CONDITIONING'],'LoadImage':['IMAGE','MASK'],'ImageScale':['IMAGE'],
               'VAEEncode':['LATENT'],'ReferenceLatent':['CONDITIONING'],'CFGGuider':['GUIDER'],'RandomNoise':['NOISE'],
               'KSamplerSelect':['SAMPLER'],'Flux2Scheduler':['SIGMAS'],'EmptyFlux2LatentImage':['LATENT'],
               'SamplerCustomAdvanced':['LATENT','LATENT'],'VAEDecode':['IMAGE'],'SaveImage':[]}
    types = {'model':'MODEL','clip':'CLIP','vae':'VAE','conditioning':'CONDITIONING','latent':'LATENT',
             'positive':'CONDITIONING','negative':'CONDITIONING','noise':'NOISE','guider':'GUIDER','sampler':'SAMPLER',
             'sigmas':'SIGMAS','latent_image':'LATENT','samples':'LATENT','images':'IMAGE','pixels':'IMAGE'}
    template = json.loads((TEMPLATE_DIR/'experimental/klein_tryon.json').read_text())
    for node in template['nodes']:
        fields = {}
        for name, value in node['inputs'].items():
            if isinstance(value, dict):
                fields[name] = [types.get(name, 'IMAGE')]
            elif name == 'image':
                fields[name] = [['person.png','garment.png']]
            elif name in ('unet_name','clip_name','vae_name'):
                fields[name] = [[MODELS[{'unet_name':'diffusion','clip_name':'encoder','vae_name':'vae'}[name]]]]
            else:
                kind = 'INT' if name in ('width','height','noise_seed','steps','batch_size') else 'FLOAT' if name=='cfg' else 'STRING'
                fields[name] = [kind]
        info[node['class_type']] = {'input':{'required':fields},'output':outputs[node['class_type']]}
    return info

class TryonTests(unittest.TestCase):
    def setUp(self):
        self.s = ArchitectService(); self.info = fixture(); self.s.refresh(self.info)
    def build(self, **overrides):
        payload = dict(build_draft=True, expected=self.s.prepare_tryon({})['expected'], authorized=True,
                       model_overrides=dict(MODELS), person_image='person.png',garment_image='garment.png',prompt='Match reference seams')
        return self.s.prepare_tryon({**payload, **overrides})
    def test_default_no_compile(self):
        with patch.object(self.s,'compile',side_effect=AssertionError()):
            self.assertIsNone(self.s.prepare_tryon({})['compiled'])
    def test_exact_dual_reference_graph(self):
        result=self.build(); self.assertTrue(result['validation']['valid'],result['validation'])
        graph=result['compiled']['api_prompt']; self.assertEqual(len(graph),23)
        self.assertEqual([n['inputs']['image'] for n in graph.values() if n['class_type']=='LoadImage'],['person.png','garment.png'])
        self.assertEqual(sum(n['class_type']=='ReferenceLatent' for n in graph.values()),4)
        self.assertEqual(next(n for n in graph.values() if n['class_type']=='EmptyFlux2LatentImage')['inputs']['batch_size'],1)
        self.assertFalse(result['quality_approved']);self.assertFalse(result['execution_authorized'])
        self.assertIsNone(result['compiled']['ui_workflow']['extra']['workflow_architect']['identity_strategy'])
    def test_bad_images(self):
        for change in ({'person_image':'garment.png'}, {'person_image':'../a.png'},{'garment_image':'https://x/a'},{'garment_image':None}):
            with self.subTest(change=change),self.assertRaises(InputError):self.build(**change)
    def test_consent(self):
        for value in (False,None,1,'true'):
            with self.subTest(value=value),self.assertRaises(InputError):self.build(authorized=value)
    def test_exact_models(self):
        with self.assertRaises(InputError):self.build(model_overrides={})
    def test_expected(self):
        with self.assertRaises(InputError):self.build(expected={})
    def test_prompt_bounds(self):
        for value in ('',None,'x'*2001):
            with self.assertRaises(InputError):self.build(prompt=value)
    def test_dimensions(self):
        for w,h in ((True,768),(575,768),(1024,1024),(128,256)):
            with self.subTest(size=(w,h)),self.assertRaises(InputError):self.build(width=w,height=h)
    def test_missing_model_no_fallback(self):
        self.info['UNETLoader']['input']['required']['unet_name']=[['other.safetensors']];self.s.refresh(self.info)
        with patch.object(self.s,'compile',side_effect=AssertionError()):self.assertEqual(self.build()['missing_models'],[MODELS['diffusion']])
    def test_missing_node(self):
        del self.info['ReferenceLatent'];self.s.refresh(self.info)
        self.assertIn('ReferenceLatent',self.build()['missing_nodes'])
    def test_unknown_field(self):
        with self.assertRaises(InputError):self.build(execute=True)
    def test_registry_drift(self):
        original=self.s.compile
        def drift(payload):
            result=original(payload);self.s.refresh(self.info);return result
        with patch.object(self.s,'compile',side_effect=drift),self.assertRaises(InputError):self.build()
