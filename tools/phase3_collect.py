import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure
configure()
from royale_phase3.collection import collect,configuration
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/phase3/collection/main-001')
    p.add_argument('--games',type=int,default=50);p.add_argument('--seed',type=int,default=310000)
    p.add_argument('--max-tick',type=int,default=7200);p.add_argument('--stop-after',type=int)
    a=p.parse_args()
    if not 1<=a.games<=1000 or not 91<=a.max_tick<=100000: p.error('Invalid bounded collection size')
    collect(a.output,configuration(a.games,a.seed,a.max_tick),a.stop_after)
