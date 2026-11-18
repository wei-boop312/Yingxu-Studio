"""Compile owned selected inputs using real local registry, never submit prompt."""
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.service import ArchitectService
from workflow_architect.tryon import MODELS

def main():
    target=Path(sys.argv[1])
    if target.exists():raise SystemExit('Preserve existing output directory')
    target.mkdir()
    with urllib.request.urlopen('http://127.0.0.1:8188/object_info',timeout=20) as response:info=json.loads(response.read(32*1024*1024))
    s=ArchitectService();s.refresh(info)
    query='参考图换装';context=s.planning_context({'text':query});c=context['context']['candidates'][0]
    # Same human-reviewed steps; fresh local registry context, not an API call.
    proposal={'context_digest':context['context_digest'],'template_id':c['template_id'],'evidence_ids':c['evidence_ids'],
              'reason':'User-reviewed Klein dual-reference structure, fresh local compile context',
              'steps':[{'title':'Concrete required graph classes','node_classes':c['node_classes']}]}
    prefix='klein_compare_6e5721bedc204c3bb05f898f20c63808'
    result=s.compile_plan({'text':query,'proposal':proposal,'build':True,'authorized':True,'models':MODELS,
        'images':{'person_image':prefix+'_person.png','garment_image':prefix+'_garment.png'},
        'prompt':'Replace original outfit with reference black lace two-piece, matching seams, straps, floral pattern and bows. Fully opaque lining. Preserve adult person and scene.',
        'width':576,'height':768})
    if result['status']!='workflow-compiled-not-executed':raise SystemExit('Compilation rejected')
    ui=result['compiled']['ui_workflow'];api=result['compiled']['api_prompt']
    by_id={n['id']:n for n in ui['nodes']}
    assert len(by_id)==23 and len(ui['links'])>0
    for link_id,src,slot,dst,input_slot,kind in ui['links']:
        assert link_id in by_id[src]['outputs'][slot]['links']
        assert by_id[dst]['inputs'][input_slot]['link']==link_id
        assert by_id[src]['outputs'][slot]['type']==by_id[dst]['inputs'][input_slot]['type']
    assert api['6']['inputs']['image'].endswith('_person.png') and api['7']['inputs']['image'].endswith('_garment.png')
    assert api['14']['inputs']['conditioning']==['12',0] and api['14']['inputs']['latent']==['11',0]
    assert api['15']['inputs']['conditioning']==['13',0] and api['15']['inputs']['latent']==['11',0]
    for name,value in [('comfyui-workflow.json',ui),('comfyui-api-prompt.json',api),('acceptance.json',{
        'phase':3,'static_compilation_passed':True,'node_count':len(ui['nodes']),'link_count':len(ui['links']),
        'workflow_sha256':result['workflow_sha256'],'ui_link_integrity_verified':True,'actual_browser_loaded':False,
        'api_called':False,'inference_submitted':False,'quality_approved':False,'repair_scope':'per-action UI limit, not global persisted quota'})]:
        (target/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'directory':str(target),'nodes':len(ui['nodes']),'links':len(ui['links']),'inference_submitted':False}))
if __name__=='__main__':main()
