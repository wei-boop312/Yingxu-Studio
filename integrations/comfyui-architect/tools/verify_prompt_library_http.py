"""Owned ephemeral HTTP integration; never queues or contacts a model provider."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web, ClientSession, ClientTimeout
from workflow_architect import routes
from workflow_architect.service import ArchitectService

async def main(output):
    if output.exists():raise RuntimeError('Preserve prior evidence; choose a new owned directory')
    table=web.RouteTableDef();service=ArchitectService()
    server=SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=table)))
    with patch.dict(sys.modules,{'server':server}),patch.object(routes,'SERVICE',service),patch.object(routes,'_REGISTERED',False),patch.object(service,'refresh',return_value={}):
        routes.register_routes()
    app=web.Application();app.add_routes(table);runner=web.AppRunner(app)
    await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1]
    base=f'http://127.0.0.1:{port}/workflow-architect/prompts/'
    report={'scope':'source-2.14.0-owned-ephemeral-http-not-daily-browser',
            'external_api_calls':0,'queued_prompts':0}
    try:
        async with ClientSession(timeout=ClientTimeout(total=30)) as client:
            async def post(path,body):
                async with client.post(base+path,json=body) as response:
                    data=await response.json()
                    return response.status,data,response.headers.get('Cache-Control','')
            status,envelope,cache=await post('search',{})
            assert status==200 and envelope['ok'] and 'no-store' in cache
            catalog=envelope['data'];assert len(catalog['cards'])==80
            filled=[]
            for card in catalog['cards']:
                status,value,_=await post('render',{'id':card['id'],'expected_sha256':catalog['content_sha256'],'variables':card['variables']})
                assert status==200 and value['ok']
                data=value['data'];assert data['execution_authorized'] is False and data['api_called'] is False
                assert '{' not in data['card']['positive'];filled.append(data)
            status,found,_=await post('search',{'query':'换装','category':'人物与换装'})
            assert status==200 and any(c['id']=='tryon_full_set' for c in found['data']['cards'])
            status,stale,_=await post('render',{'id':'white_background','expected_sha256':'0'*64,'variables':{}})
            assert status==400 and not stale['ok']
            for payload in ({'execute':True},{'path':'private.json'},{'query':'x'*201}):
                status,value,_=await post('search',payload);assert status==400 and not value['ok']
            async with client.post(base+'search',data='{}',headers={'Content-Type':'text/plain'}) as response:
                assert response.status==400
            report.update(cards=80,actual_http_rendered=80,chinese_search=True,stale_digest_rejected=True,
                          invalid_payloads_rejected=True,wrong_mime_rejected=True,no_store=True,
                          content_sha256=catalog['content_sha256'])
            output.mkdir(parents=True)
            for name,data in [('http-verification.json',report),('catalog.json',catalog),('filled-examples.json',filled)]:
                with (output/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
            print(json.dumps(report,indent=2))
    finally:await runner.cleanup()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args().output))
