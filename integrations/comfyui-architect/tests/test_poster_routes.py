import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_research_routes import FakeRoutes
from workflow_architect import routes
from workflow_architect.service import ArchitectService

class PosterRouteTests(unittest.TestCase):
    def call(self,**kw):
        registry=FakeRoutes();service=ArchitectService();calls=[]
        service.poster_prepare=lambda payload,cancel:calls.append(payload) or {'executed':False}
        async def body():return {'confirmed':True}
        request=SimpleNamespace(remote='127.0.0.1',headers={'Origin':'http://127.0.0.1:8188'},content_type='application/json',content_length=100,json=body,**{})
        for key,value in kw.items():setattr(request,key,value)
        web=SimpleNamespace(json_response=lambda value,status=200,**_: (status,value))
        server=SimpleNamespace(instance=SimpleNamespace(routes=registry))
        with patch.dict(sys.modules,{'aiohttp':SimpleNamespace(web=web),'server':SimpleNamespace(PromptServer=server)}),patch.object(routes,'SERVICE',service),patch.object(routes,'_REGISTERED',False),patch.object(service,'refresh',return_value={}):
            routes.register_routes();response=asyncio.run(registry.handlers[('POST','/workflow-architect/poster/prepare')](request))
        return response,calls
    def test_local_prepare_no_execution(self):
        (status,value),calls=self.call();self.assertEqual(status,200);self.assertEqual(calls,[{'confirmed':True}]);self.assertFalse(value['data']['executed'])
    def test_remote_rejected(self):
        (status,_),calls=self.call(remote='8.8.8.8');self.assertEqual(status,403);self.assertEqual(calls,[])
    def test_cross_origin_rejected(self):
        (status,_),calls=self.call(headers={'Origin':'https://example.invalid'});self.assertEqual(status,403);self.assertEqual(calls,[])
    def test_bounded_json(self):
        for values in ({'content_length':20000},{'content_length':None},{'content_type':'text/plain'}):
            (status,_),calls=self.call(**values);self.assertEqual(status,400);self.assertEqual(calls,[])
