"""Real local registry plus explicitly synthetic model response, no API calls."""
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.service import ArchitectService
from workflow_architect.api_planner import build_chat_request,parse_chat_response

def main():
    output=Path(sys.argv[1])
    if output.exists():raise SystemExit('Preserve existing result')
    with urllib.request.urlopen('http://127.0.0.1:8188/object_info',timeout=20) as response:info=json.loads(response.read(32*1024*1024))
    s=ArchitectService();s.refresh(info)
    context=s.planning_context({'text':'参考图换装，匹配服装结构和花纹，保留人物背景'})
    candidate=context['context']['candidates'][0]
    proposal={'context_digest':context['context_digest'],'template_id':candidate['template_id'],
              'evidence_ids':candidate['evidence_ids'],'reason':'Owned synthetic contract fixture, not an API-model quality result',
              'steps':[{'title':'Original core graph, bind inputs in phase3','node_classes':candidate['node_classes']}]}
    request=build_chat_request(context,{'endpoint':'https://example.invalid/v1/chat/completions','model':'explicit-fixture-id-not-real-model','max_tokens':1024})
    parsed=parse_chat_response(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(proposal)}}]}).encode())
    accepted=s.validate_proposal({'text':context['context']['query'],'proposal':parsed})
    result={'phase':2,'scope':'offline-contract-only','context':context,'synthetic_proposal':proposal,'accepted':accepted,
            'serializer_sent':request['sent'],'real_provider_verified':False,'credential_configured':False,
            'budget_enforcement_verified':False,'inference_submitted':False,'browser_verified':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'offline_contract_passed':accepted['status']=='grounded-proposal-not-executed','real_provider_verified':False,'output':str(output)},ensure_ascii=False))
if __name__=='__main__':main()
