"""Real aiohttp route integration on owned ephemeral loopback, not daily UI."""
import argparse
import asyncio
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.request
import zipfile
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),'E:/AI/ComfyUI_windows_portable/ComfyUI']
import folder_paths
from aiohttp import web,ClientSession
from workflow_architect import routes
from workflow_architect.service import ArchitectService

def daily_info():
    with urllib.request.urlopen('http://127.0.0.1:8188/object_info',timeout=20) as response:return json.load(response)

async def main(directory):
    target=directory/'poster-http-integration.json'
    if target.exists():raise RuntimeError('Preserve previous route evidence')
    service=ArchitectService();service.refresh=lambda:ArchitectService.refresh(service,daily_info())
    table=web.RouteTableDef();prior=sys.modules.get('server')
    sys.modules['server']=SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=table)))
    routes.SERVICE=service;routes._REGISTERED=False
    try:routes.register_routes()
    finally:
        if prior is None:sys.modules.pop('server',None)
        else:sys.modules['server']=prior
    app=web.Application();app.add_routes(table);runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1];base=f'http://127.0.0.1:{port}/workflow-architect'
    headers={'Origin':'http://127.0.0.1:8188'}
    payload=json.loads((directory/'poster-prepare-request.json').read_text(encoding='utf-8'))
    results={'scope':'owned-ephemeral-aiohttp-source-2.12.0-not-daily-server-or-browser','external_paid_api_calls':0,'queued_prompts':0}
    try:
        async with ClientSession(timeout=__import__('aiohttp').ClientTimeout(total=35)) as client:
            async with client.get(base+'/health') as response:
                health=await response.json();results['source_runtime_version']=health['data']['version']
            async with client.post(base+'/poster/options',json={},headers=headers) as response:
                options=await response.json();results['options_status']=response.status;results['actual_asset_count']=len(options['data']['assets'])
            for name,request_headers,body in [('cross_origin',{'Origin':'https://example.invalid'},payload),('wrong_mime',headers,None)]:
                kwargs={'json':body} if body is not None else {'data':'invalid','headers':{**request_headers,'Content-Type':'text/plain'}}
                if body is not None:kwargs['headers']=request_headers
                async with client.post(base+'/poster/prepare',**kwargs) as response:results[name+'_status']=response.status
            async with client.post(base+'/poster/prepare',json=payload,headers=headers) as response:
                result=await response.json();results['prepare_status']=response.status
                if response.status!=200 or not result.get('ok'):raise RuntimeError('Actual owned-route prepare failed: '+str(result.get('error',{}).get('code')))
                prepared=result['data'];results['prepared_nodes']=len(prepared['compiled']['ui_workflow']['nodes']);results['executed']=prepared['executed']
            async with client.get(base+'/poster/package/'+prepared['package_id'],headers=headers) as response:
                data=await response.read();results['package_status']=response.status;results['package_mime']=response.headers.get('Content-Type');results['package_no_store']='no-store' in response.headers.get('Cache-Control','')
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    results['package_input_count']=sum(name.startswith('input/') for name in archive.namelist());results['package_ui_links']=len(json.loads(archive.read('workflow.json'))['links'])
            async with client.get(base+'/poster/package/'+prepared['package_id']+'?path=secret',headers=headers) as response:results['package_query_rejected']=response.status==400
            async with client.get(base+'/poster/package/'+prepared['package_id'],headers={'Origin':'https://example.invalid'}) as response:results['package_cross_origin_rejected']=response.status==403
            async with client.get(base+'/poster/package/not-an-id',headers=headers) as response:results['package_bad_id_rejected']=response.status==400
            results['all_checks_passed']=results['options_status']==200 and results['prepare_status']==200 and results['cross_origin_status']==403 and results['wrong_mime_status']==400 and results['prepared_nodes']==5 and results['executed'] is False and results['package_status']==200 and results['package_input_count']==2 and results['package_ui_links']==5 and all(results[k] for k in ['package_no_store','package_query_rejected','package_cross_origin_rejected','package_bad_id_rejected'])
            if not results['all_checks_passed']:raise RuntimeError('An owned HTTP route assertion failed')
            target.write_text(json.dumps(results,indent=2),encoding='utf-8');print(json.dumps(results,indent=2))
    finally:await runner.cleanup()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args();asyncio.run(main(args.directory))
