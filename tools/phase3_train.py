"""Train/evaluate the phase3 small sequence policies without native control."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure, local, read, reference, write
configure()
from royale_phase3.learning import StatefulPolicy, evaluate, load_dataset, train

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('train');p.add_argument('--metadata',required=True);p.add_argument('--output',required=True)
    p.add_argument('--variant',choices=list('ABCDE'),default='C');p.add_argument('--epochs',type=int,default=5)
    p.add_argument('--seed',type=int,default=31001);p.add_argument('--hidden',type=int,default=32)
    p.add_argument('--sequence-length',type=int,default=32);p.add_argument('--burnin',type=int,default=8);p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--learning-rate',type=float,default=.001);p.add_argument('--world-weight',type=float,default=.1)
    p.add_argument('--play-weight',type=float,default=1.)
    p.add_argument('--opponent',action='store_true');p.add_argument('--hazard',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after-epochs',type=int)
    p=sub.add_parser('evaluate');p.add_argument('--metadata',required=True);p.add_argument('--checkpoint',required=True)
    p.add_argument('--split',choices=['train','validation','test'],default='validation');p.add_argument('--output',required=True)
    args=vars(parser.parse_args());command=args.pop('command')
    if command=='train':train(**args)
    else:
        output=args.pop('output')
        if local(output).exists():raise FileExistsError('Evaluation reports are immutable')
        data,meta=load_dataset(args['metadata']);model=StatefulPolicy.load(args['checkpoint'],meta['version_fingerprint_sha256'],meta['observation_profile'])
        report=evaluate(model.model,data,args['split']);report['checkpoint']=reference(args['checkpoint']);report['dataset']=reference(args['metadata']);write(output,report)

if __name__=='__main__':main()
