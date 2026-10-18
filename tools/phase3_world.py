import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure
configure()
from royale_phase3.world import train
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--metadata',required=True);p.add_argument('--output',required=True);p.add_argument('--epochs',type=int,default=20);p.add_argument('--seed',type=int,default=1)
    a=p.parse_args();print(train(a.metadata,a.output,a.seed,a.epochs))
