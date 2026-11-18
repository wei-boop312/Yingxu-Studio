import copy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image
from workflow_architect.brand_poster import DEFAULT,validate_style,cutout,create_overlay,compose,tabletop_remote
from workflow_architect.poster_service import prepare,resolve,package_path,options
from workflow_architect.errors import InputError
from workflow_architect.service import ArchitectService
from test_detail_page import detail_fixture

FONT_PATH=os.environ.get('WORKFLOW_ARCHITECT_POSTER_FONT','C:/Windows/Fonts/arialbd.ttf')

class BrandPosterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.input=self.root/'input';self.output=self.root/'output';self.input.mkdir();self.output.mkdir()
        self.folders=SimpleNamespace(get_input_directory=lambda:str(self.input),get_output_directory=lambda:str(self.output))
        self.refs={}
        for role in ['scene','table','product','product_mask','remote','remote_mask']:
            Image.new('RGB',(160,160),'white' if 'mask' in role else '#223344').save(self.input/(role+'.png'))
            self.refs[role]={'filename':role+'.png','subfolder':'','type':'input'}
        self.payload={'assets':self.refs,'style':copy.deepcopy(DEFAULT),'confirmed':True}
        self.service=ArchitectService();self.info=detail_fixture();self.info['InvertMask']={'input':{'required':{'mask':['MASK']}},'output':['MASK']}
        def refresh():
            self.info['LoadImage']['input']['required']['image'][0]=[p.name for p in self.input.glob('*.png')]
            return ArchitectService.refresh(self.service,self.info)
        self.service.refresh=refresh;refresh()
    def call(self,**kw):return prepare(self.service,{**self.payload,**kw},folders=self.folders)
    def test_style_valid(self):self.assertEqual(validate_style(DEFAULT),DEFAULT)
    def test_unknown_style(self):
        with self.assertRaises(InputError):validate_style({**DEFAULT,'execute':True})
    def test_style_limits(self):
        for key,value in [('headline','x'*25),('accent','url(secret)'),('subtitle','bad\n'),('model',12)]:
            with self.subTest(key=key),self.assertRaises(InputError):validate_style({**DEFAULT,key:value})
    def test_badges_strict(self):
        for badges in ([],[{'icon':'unknown','lines':['x']}]*4,[{'icon':'chip','lines':['x'*15]}]*4):
            with self.assertRaises(InputError):validate_style({**DEFAULT,'badges':badges})
    def test_no_arbitrary_paths(self):
        for ref in ({'filename':'../secret.png','subfolder':'','type':'input'},{'filename':'scene.png','subfolder':'../','type':'input'},{'filename':'scene.png','subfolder':'','type':'vault'}):
            with self.assertRaises(InputError):resolve(ref,self.folders)
    def test_valid_ref(self):self.assertEqual(resolve(self.refs['scene'],self.folders),self.input/'scene.png')
    def test_wrong_mask_size(self):
        with self.assertRaises(InputError):cutout(Image.new('RGB',(100,100)),Image.new('L',(99,100),255))
    def test_empty_mask(self):
        with self.assertRaises(InputError):cutout(Image.new('RGB',(100,100)),Image.new('L',(100,100)))
    def test_crop_bounds(self):
        with self.assertRaises(InputError):cutout(Image.new('RGB',(100,100)),Image.new('L',(100,100),255),crop=(0,0,2,1))
    def test_remote_alpha(self):
        image=tabletop_remote(Image.new('RGBA',(100,400),'white'))
        self.assertEqual(image.size,(330,560));self.assertEqual(image.getpixel((0,0))[3],0)
    def test_overlay_and_text_layout(self):
        source=Image.new('RGB',(160,160),'#223344');mask=Image.new('L',(160,160),255)
        overlay,report=create_overlay(source,mask,source,mask,DEFAULT,FONT_PATH)
        self.assertEqual(overlay.size,(1600,1600));self.assertFalse(report['product_redrawn_by_diffusion'])
        for key in ('product_box','remote_box'):
            x,y,r,b=report[key];self.assertTrue(0<=x<r<=1600 and 0<=y<b<=1600)
        self.assertEqual(compose(source,overlay,source).size,(1600,1600))
    def test_missing_explicit_font_is_not_silently_replaced(self):
        with patch.dict(os.environ,{'WORKFLOW_ARCHITECT_POSTER_FONT':str(self.root/'missing.ttf')}):
            with self.assertRaisesRegex(InputError,'WORKFLOW_ARCHITECT_POSTER_FONT'):self.call()
        self.assertEqual(list(self.output.iterdir()),[])
    def test_explicit_confirm(self):
        for value in (False,1,'true'):
            with self.assertRaises(InputError):self.call(confirmed=value)
    def test_asset_roles(self):
        with self.assertRaises(InputError):self.call(assets={'scene':self.refs['scene']})
    def test_cancel_before_decode(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InputError):prepare(self.service,self.payload,cancel,self.folders)
        self.assertEqual(list(self.output.iterdir()),[])
    def test_full_prepare_and_portable_package(self):
        before={p.name:p.read_bytes() for p in self.input.glob('*.png')}
        with patch('socket.getaddrinfo',side_effect=AssertionError('No network')):result=self.call()
        self.assertFalse(result['executed']);self.assertFalse(result['commercial_quality_approved'])
        self.assertTrue(result['validation']['valid']);self.assertEqual(len(result['compiled']['ui_workflow']['nodes']),5)
        native=result['compiled']['api_prompt'];self.assertEqual(native['3']['class_type'],'InvertMask')
        self.assertFalse(any(n['class_type']=='KSampler' for n in native.values()))
        with zipfile.ZipFile(package_path(result['package_id'],self.folders)) as archive:
            self.assertEqual(len([n for n in archive.namelist() if n.startswith('input/')]),2)
            self.assertTrue(json.loads(archive.read('workflow.json'))['links']);self.assertNotIn(b'OPENAI_API_KEY',archive.read('receipt.json'))
        for name,data in before.items():self.assertEqual((self.input/name).read_bytes(),data)
    def test_source_mutation_detected(self):
        import workflow_architect.poster_service as module
        original=module.create_overlay
        def mutate(*args,**kw):
            result=original(*args,**kw);Image.new('RGB',(160,160),'red').save(self.input/'scene.png');return result
        with patch.object(module,'create_overlay',side_effect=mutate):
            with self.assertRaises(InputError):self.call()
        self.assertEqual(list(self.output.iterdir()),[])
    def test_package_id_validation(self):
        for ident in ('../secret','x'*32,'a'*32):
            with self.assertRaises(InputError):package_path(ident,self.folders)
    def test_reserved_capacity_checked_before_writes(self):
        def budget(folders,additional_bytes=0,additional_files=0):
            if additional_bytes:raise InputError('fixture capacity full')
        with patch('workflow_architect.poster_service.check_budget',side_effect=budget):
            with self.assertRaises(InputError):self.call()
        self.assertFalse(list(self.input.glob('wa_brand_*')));self.assertEqual(list(self.output.iterdir()),[])
    def test_final_source_recheck_after_refresh(self):
        original=self.service.refresh
        def mutate():
            value=original();Image.new('RGB',(160,160),'red').save(self.input/'scene.png');return value
        self.service.refresh=mutate
        with self.assertRaises(InputError):self.call()
    def test_header_dimension_guard(self):
        Image.new('RGB',(8193,2),'red').save(self.input/'scene.png')
        with self.assertRaises(InputError):self.call()
        self.assertEqual(list(self.output.iterdir()),[])
    def test_options_no_decode_or_write(self):
        with patch('PIL.Image.Image.load',side_effect=AssertionError()):result=options(self.service,self.folders)
        self.assertEqual(len(result['assets']),6);self.assertFalse(result['execution_authorized']);self.assertEqual(list(self.output.iterdir()),[])
