"""Actual local model evaluation; fixture compilation scope explicitly recorded."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'tests')]
from workflow_architect.offline_ai import status,assist
from workflow_architect.service import ArchitectService
from test_knowledge import node_fixture

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise SystemExit('Preserve existing evaluation artifacts')
    state=status({})
    if not state['ready']:raise SystemExit('Actual local model not ready')
    args.output.mkdir(parents=True)
    service=ArchitectService();service.refresh(node_fixture())
    runs=[]
    for index,question in enumerate(['参考图换装怎样保护手脚，为什么不能只改颜色？','V3主图如何保留真实产品和Logo并安排英文标题？','没有产品背面照片，可以直接生成背面当实拍证据吗？']):
        start=time.monotonic()
        try:
            result=assist(service,{'text':question,'mode':'knowledge','confirmed':True})
            row={'question':question,'seconds':round(time.monotonic()-start,3),'result':result,'success':True}
        except Exception as error:
            row={'question':question,'seconds':round(time.monotonic()-start,3),'success':False,'error_type':type(error).__name__,'message':str(error)}
        with (args.output/f'answer-{index+1}.json').open('x',encoding='utf-8') as stream:json.dump(row,stream,ensure_ascii=False,indent=2)
        runs.append(row);print(json.dumps({k:v for k,v in row.items() if k!='result'},ensure_ascii=False),flush=True)
    query='调整图片尺寸为256x256'
    start=time.monotonic()
    try:
        result=assist(service,{'text':query,'mode':'plan','confirmed':True})
        proposal=result['accepted']['proposal']
        compiled=service.compile_plan({'text':query,'proposal':proposal,'build':True,'authorized':True,'models':{},
                    'images':{'source_image':'owned-probe.png'},'prompt':'Preserve the source; resize only.','width':256,'height':256})
        assert compiled['validation']['valid'] and compiled['compiled']['ui_workflow']['nodes'] and compiled['compiled']['ui_workflow']['links']
        with (args.output/'local-plan.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
        with (args.output/'compiled-workflow.json').open('x',encoding='utf-8') as stream:json.dump(compiled['compiled']['ui_workflow'],stream,ensure_ascii=False,indent=2)
        plan={'success':True,'seconds':round(time.monotonic()-start,3),'scope':'actual-local-LLM-plus-existing-compiler-with-source-shaped-fixture-not-daily-Comfy-execution',
              'nodes':len(compiled['compiled']['ui_workflow']['nodes']),'links':len(compiled['compiled']['ui_workflow']['links'])}
    except Exception as error:
        plan={'success':False,'seconds':round(time.monotonic()-start,3),'error_type':type(error).__name__,'message':str(error)}
    report={'actual_local_model':state,'knowledge_successes':sum(r['success'] for r in runs),'knowledge_attempts':len(runs),'plan':plan,
            'external_inference_calls':0,'gpu_jobs':0,'retries':0,'automatic_cloud_fallback':False}
    with (args.output/'evaluation.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    if not all(r['success'] for r in runs) or not plan['success']:raise SystemExit(1)

if __name__=='__main__':main()
