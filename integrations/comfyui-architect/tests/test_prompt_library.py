import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workflow_architect import prompt_library as lib
from workflow_architect.errors import InputError
from workflow_architect.knowledge import KnowledgeBase, bundled_documents
from workflow_architect.registry import CapabilityRegistry

class PromptLibraryTests(unittest.TestCase):
    def setUp(self):
        self.catalog=lib.catalog()

    def payload(self, ident='tryon_full_set'):
        card=next(c for c in self.catalog['cards'] if c['id']==ident)
        return {'id':ident,'expected_sha256':self.catalog['content_sha256'],'variables':copy.deepcopy(card['variables'])}

    def test_all_cards_render_and_no_placeholders_or_execution(self):
        self.assertEqual(len(self.catalog['cards']),80)
        for card in self.catalog['cards']:
            with self.subTest(card=card['id']):
                result=lib.render(self.payload(card['id']))
                self.assertNotIn('{',result['card']['positive'])
                self.assertFalse(result['execution_authorized'])
                self.assertFalse(result['api_called'])
                self.assertFalse(result['runtime_verified'])
                self.assertTrue(result['card']['inputs']);self.assertTrue(result['card']['checks'])

    def test_chinese_search_and_category(self):
        result=lib.search({'query':'换装','category':'人物与换装'})
        self.assertIn('tryon_full_set',[c['id'] for c in result['cards']])
        self.assertTrue(all(c['category']=='人物与换装' for c in result['cards']))
        self.assertEqual(lib.search({'query':'notpresentxyz'})['cards'],[])

    def test_engine_filter_not_invented_support(self):
        result=lib.search({'engine':'layout-only'})
        self.assertEqual(len(result['cards']),9)
        self.assertEqual(len([c for c in result['cards'] if c['category']=='详情页卖点']),3)
        self.assertTrue(all(c['negative']=='' for c in result['cards']))

    def test_unknown_fields_and_types(self):
        for payload in ([],{'path':'secret'},{'query':True},{'query':'x'*201},{'engine':'magic'},{'category':'unknown'}):
            with self.subTest(payload=payload),self.assertRaises(InputError):lib.search(payload)

    def test_stale_library_rejected(self):
        payload=self.payload();payload['expected_sha256']='0'*64
        with self.assertRaises(InputError):lib.render(payload)

    def test_variables_exact_bounded_plain_values(self):
        for value in ('', '  ', True, [], 'x'*241, '{extra}', 'line\nbreak', '\x00'):
            payload=self.payload();payload['variables']['garment']=value
            with self.subTest(value=value),self.assertRaises(InputError):lib.render(payload)
        for values in ({}, {'garment':'shirt','protected':'hands','extra':'x'}):
            payload=self.payload();payload['variables']=values
            with self.assertRaises(InputError):lib.render(payload)

    def test_wrong_id_or_extra_command_rejected(self):
        payload=self.payload();payload['id']='../../file'
        with self.assertRaises(InputError):lib.render(payload)
        payload=self.payload();payload['execute']=True
        with self.assertRaises(InputError):lib.render(payload)

    def test_filled_values_copied_and_exact(self):
        payload=self.payload();payload['variables']['garment']='the blue two-piece swimsuit in image 2'
        result=lib.render(payload)
        self.assertIn(payload['variables']['garment'],result['card']['positive'])
        payload['variables']['garment']='changed'
        self.assertNotEqual(result['card']['variables']['garment'],'changed')
        self.assertIn('not merely recolor',result['card']['positive'])

    def test_library_copies_and_deterministic(self):
        before=lib.catalog();after=lib.catalog();self.assertEqual(before,after)
        before['cards'].clear();self.assertEqual(len(lib.catalog()['cards']),80)

    def test_every_recipe_is_retrieval_only_not_template(self):
        docs=bundled_documents();prompts=[d for d in docs if d['kind']=='prompt']
        self.assertEqual(len(prompts),80)
        self.assertEqual(len([d for d in docs if d['kind']!='prompt']),82)
        self.assertTrue(all(d['template'] is None and d['task'] is None and not d['runtime_verified'] for d in prompts))
        kb=KnowledgeBase();self.addCleanup(kb.close);registry=CapabilityRegistry()
        result=kb.search('整套替换',registry.current(),limit=20)
        item=next(d for d in result['results'] if d['id']=='prompt:tryon_full_set')
        self.assertEqual(item['compatibility']['status'],'reference-only')
        with self.assertRaises(InputError):kb.template('tryon_full_set')

    def test_malformed_bundled_cards_fail_closed(self):
        clean={k:v for k,v in self.catalog.items() if k in {'schema_version','revision','authorship','cards'}}
        for case in ('duplicate','placeholder','missing','engine','unknown'):
            data=copy.deepcopy(clean)
            if case=='duplicate':data['cards'].append(copy.deepcopy(data['cards'][0]))
            elif case=='placeholder':data['cards'][0]['positive']+=' {unbound}'
            elif case=='missing':del data['cards'][0]['checks']
            elif case=='engine':data['cards'][0]['engine']='magic'
            else:data['cards'][0]['execute']=True
            with self.subTest(case=case),tempfile.TemporaryDirectory() as owned:
                path=Path(owned);(path/'prompt_library.json').write_text(json.dumps(data),encoding='utf-8')
                with patch.object(lib,'TEMPLATE_DIR',path),self.assertRaises(InputError):lib.catalog()

    def test_no_unverified_v3_ad_claim_in_hero(self):
        card=lib.render(self.payload('hero_living_room'))['card']
        for word in ('Android 14','4K 60','Netflix certified','Google certified'):
            self.assertNotIn(word,card['positive'])

    def test_seven_dimensions_have_eight_cards_each(self):
        for category in ('环境空间','镜头表现','产品角度','纹理材质','人物模特','灯光质感','文字排版'):
            self.assertEqual(len(lib.search({'category':category})['cards']),8)

    def test_references_attached_to_render_and_no_negative_for_new_cards(self):
        for card in self.catalog['cards'][24:]:
            result=lib.render(self.payload(card['id']))
            self.assertTrue(result['card']['references'])
            self.assertEqual(result['card']['negative'],'')
            self.assertTrue(all(s['url'].startswith('https://') for s in result['card']['references']))

    def test_source_change_invalidates_previous_library_digest(self):
        path=lib.TEMPLATE_DIR
        with tempfile.TemporaryDirectory() as owned:
            root=Path(owned)
            (root/'prompt_library.json').write_bytes((path/'prompt_library.json').read_bytes())
            sources=json.loads((path/'prompt_sources.json').read_bytes());sources['sources'][0]['summary']+=' updated'
            (root/'prompt_sources.json').write_text(json.dumps(sources),encoding='utf-8')
            with patch.object(lib,'TEMPLATE_DIR',root),self.assertRaises(InputError):lib.render(self.payload())

    def test_unknown_reference_fails_closed(self):
        path=lib.TEMPLATE_DIR
        with tempfile.TemporaryDirectory() as owned:
            root=Path(owned);(root/'prompt_library.json').write_bytes((path/'prompt_library.json').read_bytes())
            sources=json.loads((path/'prompt_sources.json').read_bytes());sources['card_sources']['env_living']=['UNKNOWN']
            (root/'prompt_sources.json').write_text(json.dumps(sources),encoding='utf-8')
            with patch.object(lib,'TEMPLATE_DIR',root),self.assertRaises(InputError):lib.catalog()

    def test_text_generation_and_edit_are_not_only_layout_briefs(self):
        self.assertEqual(lib.render(self.payload('type_hierarchy'))['card']['engine'],'text-to-image')
        result=lib.render(self.payload('type_textedit'))['card']
        self.assertEqual(result['engine'],'reference-edit')
        self.assertIn('"Bring Movie Night Home"',result['positive'])

if __name__=='__main__':unittest.main()
