import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_research_routes import FakeRoutes
from workflow_architect import routes
from workflow_architect.service import ArchitectService

class PromptRouteTests(unittest.TestCase):
    def call(self, payload, path='search', **changes):
        registry=FakeRoutes();service=ArchitectService()
        async def body():return payload
        request=SimpleNamespace(content_length=200,content_type='application/json',json=body)
        for key,value in changes.items():setattr(request,key,value)
        web=SimpleNamespace(json_response=lambda value,status=200,**_: (status,value))
        server=SimpleNamespace(instance=SimpleNamespace(routes=registry))
        with patch.dict(sys.modules,{'aiohttp':SimpleNamespace(web=web),'server':SimpleNamespace(PromptServer=server)}),patch.object(routes,'SERVICE',service),patch.object(routes,'_REGISTERED',False),patch.object(service,'refresh',return_value={}):
            routes.register_routes()
            return asyncio.run(registry.handlers[('POST','/workflow-architect/prompts/'+path)](request))

    def test_search_actual_library_no_execution(self):
        status,value=self.call({});self.assertEqual(status,200)
        self.assertEqual(len(value['data']['cards']),80)
        self.assertFalse(value['data']['execution_authorized']);self.assertFalse(value['data']['api_called'])

    def test_fill_actual_card(self):
        _,value=self.call({});library=value['data'];card=library['cards'][0]
        status,result=self.call({'id':card['id'],'expected_sha256':library['content_sha256'],'variables':card['variables']},'render')
        self.assertEqual(status,200);self.assertNotIn('{',result['data']['card']['positive'])

    def test_unknown_or_execution_payload_rejected(self):
        for payload in ({'execute':True},{'path':'private-file'},[],{'query':'x'*201}):
            status,value=self.call(payload);self.assertEqual(status,400);self.assertFalse(value['ok'])

    def test_old_digest_rejected(self):
        status,value=self.call({'id':'white_background','expected_sha256':'0'*64,'variables':{}},'render')
        self.assertEqual(status,400);self.assertFalse(value['ok'])

    def test_prompt_request_bounds(self):
        for changes in ({'content_length':None},{'content_length':20000},{'content_type':'text/plain'}):
            status,value=self.call({},**changes);self.assertEqual(status,400);self.assertFalse(value['ok'])
