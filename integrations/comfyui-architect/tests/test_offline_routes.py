import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_research_routes import FakeRoutes
from workflow_architect import routes,offline_ai

class OfflineRouteTests(unittest.TestCase):
    def call(self,**changes):
        registry=FakeRoutes()
        async def body():return {'text':'test','mode':'knowledge','confirmed':True}
        request=SimpleNamespace(remote='127.0.0.1',headers={'Origin':'http://127.0.0.1:8188'},content_type='application/json',content_length=100,json=body)
        for key,value in changes.items():setattr(request,key,value)
        web=SimpleNamespace(json_response=lambda value,status=200,**_: (status,value))
        server=SimpleNamespace(instance=SimpleNamespace(routes=registry))
        with patch.dict(sys.modules,{'aiohttp':SimpleNamespace(web=web),'server':SimpleNamespace(PromptServer=server)}),patch.object(routes,'_REGISTERED',False),patch.object(routes.SERVICE,'refresh',return_value={}),patch.object(offline_ai,'assist',return_value={'execution_authorized':False}) as infer:
            routes.register_routes();result=asyncio.run(registry.handlers[('POST','/workflow-architect/offline/assist')](request))
        return result,infer.call_count
    def test_local_explicit_dispatch(self):
        (status,_),count=self.call();self.assertEqual(status,200);self.assertEqual(count,1)
    def test_cross_origin_and_remote_rejected(self):
        for changes in ({'remote':'8.8.8.8'},{'headers':{'Origin':'https://example.invalid'}}):
            (status,_),count=self.call(**changes);self.assertEqual(status,403);self.assertEqual(count,0)
    def test_request_bounds(self):
        for changes in ({'content_length':None},{'content_length':9000},{'content_type':'text/plain'}):
            (status,_),count=self.call(**changes);self.assertEqual(status,400);self.assertEqual(count,0)
