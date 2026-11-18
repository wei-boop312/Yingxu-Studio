"""Owned fixed-case phase-one acceptance against current localhost registry; no queue."""
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.service import ArchitectService
from workflow_architect.tryon import MODELS

def main():
    target=Path(sys.argv[1])
    if target.exists():raise SystemExit('Preserve existing acceptance report; choose a new output')
    with urllib.request.urlopen('http://127.0.0.1:8188/object_info',timeout=20) as response:
        info=json.loads(response.read(32*1024*1024))
    s=ArchitectService();s.refresh(info)
    cases=[('电商产品图生成主图详情页','core_ecommerce_detail_hybrid_v1'),('局部重绘精修','core_inpaint_v2'),
           ('参考图换装','klein_dual_reference_tryon_experimental_v1'),('把图片调整尺寸为192x96','core_image_resize_v2'),
           ('Wan首尾帧视频生成',None),('请写一封邮件',None)]
    rows=[]
    for query,expected in cases:
        r=s.foundation_report({'text':query})
        ids=[c['id'].removeprefix('template:') for c in r['candidates']]
        passed=expected in ids if expected else not ids
        rows.append({'query':query,'expected':expected,'routing_passed':passed,'report':r})
    # Simulate missing dependency in an isolated registry only. Do not change disk/models.
    altered=json.loads(json.dumps(info));altered.pop('ReferenceLatent',None)
    isolated=ArchitectService();isolated.refresh(altered)
    missing=isolated.foundation_report({'text':'参考图换装'})
    missing_pass=any(i['code']=='missing-node' for i in missing['candidates'][0]['audit']['issues'])
    result={'phase':1,'fixed_cases':rows,'isolated_missing_node_passed':missing_pass,
            'all_passed':all(r['routing_passed'] for r in rows) and missing_pass,
            'api_called':False,'inference_submitted':False,'browser_verified':False}
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'all_passed':result['all_passed'],'cases':len(rows),'report':str(target)},ensure_ascii=False))
    if not result['all_passed']:raise SystemExit(1)

if __name__=='__main__':main()
