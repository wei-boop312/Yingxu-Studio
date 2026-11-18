"""Explicit owned-fixture local acceptance. Two prompts only; no provider API."""
import argparse
import io
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid
from unittest.mock import patch
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.service import ArchitectService

BASE='http://127.0.0.1:8188'
def request(path,body=None):
    data=json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(BASE+path,data=data,headers={'Content-Type':'application/json'}),timeout=30) as r:return json.load(r)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--run-two-owned-prompts',action='store_true');parser.add_argument('--refine-only',action='store_true');args=parser.parse_args()
    if not args.run_two_owned_prompts:raise SystemExit('Requires explicit --run-two-owned-prompts')
    if args.output.exists():raise SystemExit('Output must be new')
    queue=request('/queue')
    if queue.get('queue_running') or queue.get('queue_pending'):raise SystemExit('User queue not idle; no changes')
    args.output.mkdir(parents=True);tag='wa_owned_'+uuid.uuid4().hex
    records=[]
    for task,query in [('main','制作电商主图详情页'),('refine','局部重绘精修图片')]:
        if args.refine_only and task!='refine':continue
        image=Image.new('RGBA',(512,512),(235,238,242,255));draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((185,130,327,430),radius=24,fill=(25,120,165,255));draw.rectangle((212,90,300,140),fill=(30,45,55,255))
        draw.rectangle((198,238,314,310),fill=(250,250,250,255));draw.text((219,265),'OWNED',fill=(0,0,0,255))
        if task=='refine':draw.rectangle((210,340,300,390),fill=(220,60,60,0))
        source=args.output/(task+'-source.png');image.save(source)
        name=tag+'_'+task+'.png';boundary='Owned'+uuid.uuid4().hex
        raw=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n'.encode()+source.read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
        with urllib.request.urlopen(urllib.request.Request(BASE+'/upload/image',data=raw,headers={'Content-Type':'multipart/form-data; boundary='+boundary}),timeout=30) as response:uploaded=json.load(response)
        if uploaded['name']!=name:raise RuntimeError('Unexpected upload identity')
        service=ArchitectService();service.refresh(request('/object_info'))
        context=service.planning_context({'text':query});candidate=context['context']['candidates'][0]
        proposal={'context_digest':context['context_digest'],'template_id':candidate['template_id'],'evidence_ids':candidate['evidence_ids'],
                  'reason':'Owned fixed acceptance, deterministic mock proposal, no external model',
                  'steps':[{'title':'Concrete template nodes','node_classes':candidate['node_classes']}]}
        preview=service.compile_plan({'text':query,'proposal':proposal})
        models={item['binding']:('Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors' if item['binding']=='checkpoint' else 'birefnet.safetensors') for item in preview['selection']['models']}
        with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=(512,512)),patch('workflow_architect.service.source_image_dimensions',return_value=(512,512)):
            result=service.compile_plan({'text':query,'proposal':proposal,'build':True,'authorized':True,'models':models,
                'images':{'source_image':name},'prompt':'Clean professional product photography, pale studio background, realistic blue bottle, no extra objects','width':512,'height':512})
        if not result.get('compiled') or not result['validation']['valid']:raise RuntimeError('Actual registry validation failed')
        compiled=result['compiled'];graph=compiled['api_prompt']
        for node in graph.values():
            if node['class_type']=='SaveImage':node['inputs']['filename_prefix']=tag+'_'+task
        (args.output/(task+'-workflow.json')).write_text(json.dumps(compiled['ui_workflow'],ensure_ascii=False,indent=2),encoding='utf-8')
        (args.output/(task+'-prompt.json')).write_text(json.dumps(graph,indent=2),encoding='utf-8')
        submitted=request('/prompt',{'prompt':graph,'client_id':tag});pid=submitted['prompt_id'];print(task,pid,flush=True)
        (args.output/(task+'-submission.json')).write_text(json.dumps(submitted),encoding='utf-8')
        deadline=time.monotonic()+600
        while True:
            history=request('/history/'+pid)
            if pid in history:break
            if time.monotonic()>deadline:raise RuntimeError('Observation deadline; no retry or interrupt user queue')
            time.sleep(2)
        record=history[pid];(args.output/(task+'-history.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
        if record.get('status',{}).get('status_str')!='success':raise RuntimeError('Comfy task failed; inspect history, no retry')
        outputs=[item for out in record['outputs'].values() for item in out.get('images',[])]
        if len(outputs)!=1:raise RuntimeError('Expected exactly one output')
        from urllib.parse import urlencode
        with urllib.request.urlopen(BASE+'/view?'+urlencode(outputs[0]),timeout=30) as response:content=response.read(20*1024*1024+1)
        outpath=args.output/(task+'-output.png');outpath.write_bytes(content)
        output=Image.open(io.BytesIO(content));output.load()
        metrics={'task':task,'prompt_id':pid,'dimensions':list(output.size),'actual_execution_success':True,'commercial_quality_approved':False}
        if task=='refine':
            original=image.convert('RGB');actual=output.convert('RGB');mask=image.getchannel('A')
            metrics['outside_mask_changed_pixels']=sum(a!=b and m==255 for a,b,m in zip(original.getdata(),actual.getdata(),mask.getdata()))
            metrics['inside_mask_changed_pixels']=sum(a!=b and m==0 for a,b,m in zip(original.getdata(),actual.getdata(),mask.getdata()))
        records.append(metrics);print(json.dumps(metrics),flush=True)
        (args.output/'results.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
if __name__=='__main__':main()
