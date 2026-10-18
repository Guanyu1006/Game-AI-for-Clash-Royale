import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from .paths import ROOT,inside,output_path

def dump(obj):
    print(json.dumps(obj,ensure_ascii=False,indent=2))

def main(argv=None):
    os.environ.setdefault("PYTHONUTF8","1")
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description="Royale AI Lab · research MVP")
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("simulate",help="Run a complete sandbox match and export replay")
    p.add_argument("--seed",type=int,default=0)
    p.add_argument("--policy",default="lookahead")
    p.add_argument("--opponent",default="greedy")
    p.add_argument("--seconds",type=float,default=180)
    p.add_argument("--horizon",type=float,default=8)
    p.add_argument("--log",default="outputs/demo.jsonl")
    p.add_argument("--html",default="outputs/replay.html")
    p=sub.add_parser("evaluate")
    p.add_argument("--games",type=int,default=8)
    p.add_argument("--seed",type=int,default=10)
    p.add_argument("--seconds",type=float,default=180)
    p.add_argument("--horizon",type=float,default=8)
    p.add_argument("--output",default="outputs/evaluation.json")
    p=sub.add_parser("scenario")
    p.add_argument("name",choices=["giant","air","finish","save"])
    p.add_argument("--output",default="outputs/scenario.json")
    p=sub.add_parser("decide")
    p.add_argument("state")
    p.add_argument("--output",default="outputs/decision.json")
    p=sub.add_parser("replay")
    p.add_argument("log")
    p.add_argument("--html",default="outputs/replay.html")
    p=sub.add_parser("vision-demo",help="Synthetic pixels -> observation -> decision -> ADB dry-run")
    p.add_argument("--directory",default="outputs/vision_demo")
    p=sub.add_parser("capture")
    p.add_argument("--adb",default="adb")
    p.add_argument("--serial")
    p.add_argument("--output",default="data/screenshots/frame.png")
    p=sub.add_parser("observe")
    p.add_argument("image")
    p.add_argument("--config",required=True)
    p.add_argument("--elapsed",type=float,default=0)
    p.add_argument("--output",default="outputs/observation.json")
    p=sub.add_parser("live")
    p.add_argument("--config",required=True)
    p.add_argument("--adb",default="adb")
    p.add_argument("--serial")
    p.add_argument("--elapsed",type=float,default=0,help="Actual battle elapsed seconds at launch")
    p.add_argument("--frames",type=int,default=30)
    p.add_argument("--interval",type=float,default=1)
    p.add_argument("--execute",action="store_true",help="Send taps instead of recommendations")
    p.add_argument("--horizon",type=float,default=8)
    p=sub.add_parser("train")
    p.add_argument("logs",nargs="+")
    p.add_argument("--output",default="models/linear_policy.json")
    p.add_argument("--epochs",type=int,default=80)
    args=parser.parse_args(argv)
    try:
        if args.command=="simulate":
            from .experiments import play_game
            from .replay import render_replay
            metrics=play_game(args.seed,args.policy,args.opponent,args.seconds,horizon=args.horizon,log=args.log)
            render_replay(args.log,args.html)
            output_path("outputs/last_match_metrics.json").write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
            dump(metrics)
        elif args.command=="evaluate":
            from .experiments import evaluate
            report=evaluate(args.games,args.seed,args.seconds,args.horizon,args.output)
            dump(report["summary"])
        elif args.command in ("scenario","decide"):
            from .experiments import scenario
            from .policy import LookaheadPolicy
            from .state import Observation
            if args.command=="scenario":
                obs=scenario(args.name)
                output_path(f"examples/{args.name}.json").write_text(json.dumps(obs.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
            else:
                obs=Observation.from_dict(json.loads(inside(args.state).read_text(encoding="utf-8")))
            decision=LookaheadPolicy().decide(obs).to_dict()
            output_path(args.output).write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding="utf-8")
            dump(decision)
        elif args.command=="replay":
            from .replay import render_replay
            print(render_replay(args.log,args.html))
        elif args.command=="vision-demo":
            from .synthetic import build_demo
            from .perception import ScreenObserver
            from .policy import LookaheadPolicy
            from .controller import ADBController
            frame,config=build_demo(args.directory)
            observer=ScreenObserver(config)
            obs=observer.observe(frame,30)
            decision=LookaheadPolicy().decide(obs)
            controller=ADBController(layout=observer.layout)
            result={"fixture":"synthetic; not real game vision", "observation":obs.to_dict(),
                "decision":decision.to_dict(),"controller":controller.dispatch(obs,decision.action)}
            output_path(inside(args.directory)/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            dump(result)
        elif args.command=="capture":
            from .controller import ADBController
            print(ADBController(args.adb,args.serial).capture(args.output))
        elif args.command=="observe":
            from .perception import ScreenObserver
            obs=ScreenObserver(args.config).observe(args.image,args.elapsed)
            output_path(args.output).write_text(json.dumps(obs.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")
            dump(obs.to_dict())
        elif args.command=="live":
            live(args)
        elif args.command=="train":
            from .learning import train
            dump(train(args.logs,args.output,args.epochs))
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as error:
        parser.exit(2,f"Error: {error}\n")

def live(args):
    from .controller import ADBController
    from .perception import ScreenObserver,PerceptionError
    from .policy import LookaheadPolicy
    if args.frames <= 0 or args.interval <= 0 or args.elapsed < 0:
        raise ValueError("Invalid live loop parameters")
    observer=ScreenObserver(args.config)
    if args.execute and (observer.config["units"]["backend"] != "yolo" or not observer.config.get("calibrated",False)):
        raise ValueError("Execution requires a real YOLO backend and calibrated=true")
    ctrl=ADBController(args.adb,args.serial,observer.layout,args.execute)
    policy=LookaheadPolicy(args.horizon)
    started=time.monotonic()
    with output_path("outputs/live.jsonl").open("w",encoding="utf-8") as log:
        for _ in range(args.frames):
            tick=time.monotonic()
            try:
                frame=ctrl.capture("data/screenshots/latest.png")
                observed_at=time.monotonic()
                obs=observer.observe(frame,args.elapsed+observed_at-started)
                decision=policy.decide(obs)
                result=ctrl.dispatch(obs,decision.action,observed_at)
                row={"observation":obs.to_dict(),"decision":decision.to_dict(),"controller":result}
            except PerceptionError as error:
                row={"status":"waiting","reason":str(error)}
            log.write(json.dumps(row,ensure_ascii=False)+"\n")
            log.flush()
            dump(row)
            time.sleep(max(0,args.interval-(time.monotonic()-tick)))

if __name__=="__main__": main()
