"""One real CPU answer using the explicitly built project SQLite database."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.offline_ai import open_knowledge,assist
from workflow_architect.service import ArchitectService

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--question',default='严格保留商品Logo和非编辑区文字需要什么核验，不能只靠提示词保证吧？');a=p.parse_args()
    if a.output.exists():raise SystemExit('Preserve previous evidence')
    kb=open_knowledge()
    try:
        actual=kb._db.execute('PRAGMA database_list').fetchone()[2]
        if Path(actual).resolve()!=Path('E:/AI/WorkflowArchitect-Offline/commerce-knowledge.sqlite3').resolve():raise RuntimeError('Not persistent project database')
        before=kb._db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
    finally:kb.close()
    question=a.question
    start=time.monotonic();result=assist(ArchitectService(),{'text':question,'mode':'knowledge','confirmed':True})
    kb=open_knowledge()
    try:after=kb._db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
    finally:kb.close()
    report={'question':question,'seconds':round(time.monotonic()-start,3),'sqlite_path':actual,'documents_before':before,'documents_after':after,
            'result':result,'external_inference_calls':0,'model_weights_trained':False}
    with a.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'seconds':report['seconds'],'answer':result['answer'],'persistent_sqlite':True},ensure_ascii=False))

if __name__=='__main__':main()
