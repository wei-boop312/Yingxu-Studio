"""Source HTTP acceptance using daily read-only registry and an actual UI proposal."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.request
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web,ClientSession
from workflow_architect import routes
from workflow_architect.service import ArchitectService

async def main(proposal_path,output):
    if output.exists():raise RuntimeError('Preserve previous evidence')
    ui=json.loads(proposal_path.read_text(encoding='utf-8'));proposal=ui['accepted']['proposal']
    with urllib.request.urlopen('http://127.0.0.1:8188/object_info',timeout=15) as response:info=json.load(response)
    service=ArchitectService();service.refresh(info);table=web.RouteTableDef()
    with patch.dict(sys.modules,{'server':SimpleNamespace(PromptServer=SimpleNamespace(instance=SimpleNamespace(routes=table)))}),patch.object(routes,'SERVICE',service),patch.object(routes,'_REGISTERED',False),patch.object(service,'refresh',return_value={}):
        routes.register_routes();app=web.Application();app.add_routes(table);runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start();port=site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as client:
                async def post(payload):
                    async with client.post(f'http://127.0.0.1:{port}/workflow-architect/planner/compile',json=payload) as response:
                        data=await response.json()
                        if response.status!=200 or not data['ok']:raise RuntimeError(str(data.get('error')))
                        return data['data']
                base={'text':'调整图片尺寸为256x256','proposal':proposal}
                preview=await post(base)
                assert preview['selection']['output']=={'width':256,'height':256}
                assert preview['selection']['task']=='image_resize' and 'garment' not in preview['selection']['prompt_default']
                owned=next(n for n in preview['selection']['images'] if n.startswith('owned_smoke_') and n.endswith('_sample_0.png'))
                built=await post({**base,'build':True,'authorized':True,'models':{},'images':{'source_image':owned},
                                  'width':preview['selection']['output']['width'],'height':preview['selection']['output']['height'],
                                  'prompt':preview['selection']['prompt_default']})
                scaler=next(n for n in built['compiled']['api_prompt'].values() if n['class_type']=='ImageScale')
                assert (scaler['inputs']['width'],scaler['inputs']['height'])==(256,256)
                report={'scope':'source-2.15.2-actual-HTTP-daily-registry-and-actual-local-LLM-UI-proposal-not-daily-hotload',
                        'preview_dimensions':[256,256],'actual_compiled_dimensions':[256,256],'nodes':len(built['compiled']['ui_workflow']['nodes']),
                        'no_wrong_garment_default':True,'execution_submitted':False,'paid_api_calls':0}
                output.mkdir()
                for name,data in [('report.json',report),('preview.json',preview),('workflow.json',built['compiled']['ui_workflow'])]:
                    with (output/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
                print(json.dumps(report,indent=2))
        finally:await runner.cleanup()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--proposal',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    asyncio.run(main(a.proposal,a.output))
