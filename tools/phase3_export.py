import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure,write
configure()
from royale_phase3.data import audit_collection,export
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['audit','export']);p.add_argument('--source',default='outputs/phase3/collection/main-002')
    p.add_argument('--output',required=True);p.add_argument('--profile',choices=['fair','research_battlefield'],default='fair');p.add_argument('--limit',type=int)
    a=p.parse_args()
    if a.action=='audit':result=audit_collection(a.source);write(a.output,result)
    else:result=export(a.source,a.output,a.profile,a.limit)
    print(json.dumps({key:result.get(key) for key in ('schema','qualified_games','confirmed_plays','counts','gates','task_id')},ensure_ascii=False),flush=True)
