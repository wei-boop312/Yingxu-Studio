"""Own procedural fixture to six concrete workflows; no prompt submission."""
import argparse
import json
from pathlib import Path
import sys
import urllib.request
import uuid
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.service import ArchitectService

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise SystemExit('Do not overwrite artifacts')
    data=a.source.read_bytes()
    if len(data)>2*1024*1024:raise SystemExit('Owned fixture too big')
    with Image.open(a.source) as image:dimensions=image.size
    filename='wa_detail_owned_'+uuid.uuid4().hex+'.png';boundary='Owned'+uuid.uuid4().hex
    raw=f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode()+data+f'\r\n--{boundary}--\r\n'.encode()
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8188/upload/image',data=raw,headers={'Content-Type':'multipart/form-data; boundary='+boundary}),timeout=30) as r:uploaded=json.load(r)
    if uploaded['name']!=filename:raise SystemExit('Input identity mismatch')
    with urllib.request.urlopen('http://127.0.0.1:8188/object_info',timeout=30) as r:info=json.load(r)
    service=ArchitectService();service.refresh(info)
    product={'name':'自有蓝色测试瓶','facts':['蓝色瓶身','白色OWNED测试标签','自有程序绘制素材，非真实商品广告']}
    context=service.detail_context({'product':product})
    with patch('workflow_architect.plan_compile.source_image_dimensions',return_value=dimensions),patch('workflow_architect.service.source_image_dimensions',return_value=dimensions):
        bundle=service.detail_compile({'product':product,'plan':context['default_plan'],'models':{'checkpoint':'Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors','background_removal_model':'birefnet.safetensors'},'image':filename,'confirmed':True})
    a.output.mkdir();(a.output/'bundle.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')
    for job in bundle['jobs']:(a.output/(job['id']+'-workflow.json')).write_text(json.dumps(job['ui_workflow'],ensure_ascii=False,indent=2),encoding='utf-8')
    print('Six actual-registry workflows compiled. No prompt submitted.')
if __name__=='__main__':main()
