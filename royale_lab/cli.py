import argparse
import json
from .runtime import local

def main():
    parser=argparse.ArgumentParser(description='皇室战争本地研究平台')
    commands=parser.add_subparsers(dest='command',required=True)
    server=commands.add_parser('serve');server.add_argument('--port',type=int,default=18765)
    commands.add_parser('info')
    demo=commands.add_parser('demo');demo.add_argument('--seed',type=int,default=7)
    demo.add_argument('--policies',nargs=2,default=['search','greedy']);demo.add_argument('--max-ticks',type=int,default=3000)
    replay=commands.add_parser('replay');replay.add_argument('path')
    bc=commands.add_parser('train-bc');bc.add_argument('--episodes',type=int,default=12);bc.add_argument('--epochs',type=int,default=20)
    bc.add_argument('--seed',type=int,default=23);bc.add_argument('--max-ticks',type=int,default=1200);bc.add_argument('--resume',action='store_true')
    ppo=commands.add_parser('train-ppo');ppo.add_argument('--steps',type=int,default=4096);ppo.add_argument('--seed',type=int,default=71)
    ppo.add_argument('--max-ticks',type=int,default=1800);ppo.add_argument('--resume',action='store_true')
    ev=commands.add_parser('evaluate');ev.add_argument('--policies',nargs='+');ev.add_argument('--games',type=int,default=100)
    ev.add_argument('--workers',type=int,default=4);ev.add_argument('--max-ticks',type=int,default=3000)
    ev.add_argument('--budget-ms',type=float,default=15);ev.add_argument('--output',default='outputs/evaluation/main')
    args=parser.parse_args()
    if args.command=='serve':
        from .server import serve
        serve(args.port);return
    if args.command=='info':
        from .backend import catalog_payload
        result=catalog_payload();result['card_count']=len(result.pop('cards'))
    elif args.command=='demo':
        from .session import Session
        session=Session(args.seed,controllers=args.policies,max_ticks=args.max_ticks)
        session.advance(args.max_ticks)
        result={'final':session.state(),'replay':session.export(),'accepted':len(session.backend.journal)}
    elif args.command=='replay':
        from .backend import NativeBackend
        payload=json.loads(local(args.path).read_text(encoding='utf-8'));payload=payload.get('actions',payload)
        backend=NativeBackend();backend.load_snapshot(payload)
        result={'tick':backend.tick,'result':backend.result(),'accepted':len(backend.journal),'state':backend.oracle()}
    elif args.command=='train-bc':
        from .learning import train_bc
        result=train_bc(args.episodes,args.epochs,args.seed,args.max_ticks,args.resume)
    elif args.command=='train-ppo':
        from .learning import train_ppo
        result=train_ppo(args.steps,args.seed,resume=args.resume,max_ticks=args.max_ticks)
    elif args.command=='evaluate':
        from .evaluation import evaluate
        result=evaluate(args.policies,args.games,args.workers,args.max_ticks,args.budget_ms,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
