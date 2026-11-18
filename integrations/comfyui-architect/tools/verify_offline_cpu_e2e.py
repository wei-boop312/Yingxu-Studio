"""Real local LLM -> HTTP validation/compiler -> one owned CPU resize job."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web,ClientSession,ClientTimeout
from PIL import Image
from workflow_architect import routes
from workflow_architect.service import ArchitectService

async def main(root,output):
    if output.exists():raise RuntimeError('Preserve previous E2E evidence')
    if root.parent.resolve()!=Path('E:/AI/WorkflowArchitect-Offline').resolve() or not root.name.startswith('cpu-acceptance-') or not (root/'launch.json').is_file():raise RuntimeError('Not an owned acceptance root')
    receipt=json.loads((root/'launch.json').read_text())
    if receipt['scope']!='isolated-CPU-acceptance-not-daily-8188' or Path(receipt['root']).resolve()!=root.resolve():raise RuntimeError('Acceptance scope mismatch')
    output.mkdir(parents=True)
    source=root/'input'/'owned-offline-probe.png'
    if source.exists():raise RuntimeError('Preserve prior input')
    Image.new('RGB',(64,32),(30,90,160)).save(source)
    async with ClientSession(timeout=ClientTimeout(total=125)) as client:
        async def get(path):
            async with client.get('http://127.0.0.1:18188'+path) as r:r.raise_for_status();return await r.json()
        stats=await get('/system_stats');assert all(d['type']=='cpu' for d in stats['devices'])
        queue=await get('/queue');assert not queue['queue_running'] and not queue['queue_pending']
        info=await get('/object_info');service=ArchitectService();service.refresh(info)
        table=web.RouteTableDef();server=SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=table)))
        with patch.dict(sys.modules,{'server':server}),patch.object(routes,'SERVICE',service),patch.object(routes,'_REGISTERED',False),patch.object(service,'refresh',return_value={}):
            routes.register_routes();app=web.Application();app.add_routes(table);runner=web.AppRunner(app);await runner.setup()
            site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
            async def post(path,payload):
                async with client.post(f'http://127.0.0.1:{port}/workflow-architect/'+path,json=payload) as response:
                    value=await response.json()
                    if response.status!=200 or not value['ok']:raise RuntimeError(json.dumps(value,ensure_ascii=False))
                    return value['data']
            try:
                text='调整图片尺寸为256x256';start=time.monotonic()
                result=await post('offline/assist',{'text':text,'mode':'plan','confirmed':True})
                proposal=result['accepted']['proposal']
                compiled=await post('planner/compile',{'text':text,'proposal':proposal,'build':True,'authorized':True,
                              'images':{'source_image':source.name},'models':{},'prompt':'Resize only.','width':256,'height':256})
                assert compiled['status']=='workflow-compiled-not-executed' and compiled['validation']['valid']
                graph=compiled['compiled']['api_prompt'];assert {n['class_type'] for n in graph.values()}=={'LoadImage','ImageScale','SaveImage'}
                for node in graph.values():
                    if node['class_type']=='SaveImage':node['inputs']['filename_prefix']='owned_offline_acceptance'
                async with client.post('http://127.0.0.1:18188/prompt',json={'prompt':graph}) as response:
                    response.raise_for_status();submitted=await response.json()
                ident=submitted['prompt_id']
                deadline=time.monotonic()+30
                while True:
                    history=await get('/history/'+ident)
                    if ident in history:break
                    if time.monotonic()>deadline:raise RuntimeError('Owned CPU job not completed; retain handle '+ident)
                    await asyncio.sleep(0.25)
                record=history[ident];assert record['status']['status_str']=='success'
                refs=[r for n in record['outputs'].values() for r in n.get('images',[])]
                assert len(refs)==1
                target=(root/'output'/refs[0]['subfolder']/refs[0]['filename']).resolve();assert target.is_relative_to((root/'output').resolve())
                with Image.open(target) as image:
                    image.load();assert image.size==(256,256)
                    assert image.convert('RGB').getextrema()==((30,30),(90,90),(160,160))
                    image.save(output/'actual-resize-output.png')
                report={'scope':'real-local-LLM-plus-source-HTTP-plus-isolated-CPU-Comfy-not-daily-browser',
                        'small_model_called':True,'live_registry_nodes':len(info),'actual_prompt_id':ident,'actual_status':'success',
                        'dimensions':[256,256],'pixels_match_expected':True,'nodes':len(graph),'seconds':round(time.monotonic()-start,3),
                        'gpu_jobs':0,'external_inference_calls':0,'user_state_directory_used':False,'retries':0}
                for name,data in [('e2e.json',report),('local-result.json',result),('workflow.json',compiled['compiled']['ui_workflow']),('api-prompt.json',graph),('history.json',history)]:
                    with (output/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
                print(json.dumps(report,indent=2))
            finally:await runner.cleanup()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cpu-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    asyncio.run(main(args.cpu_root,args.output))
