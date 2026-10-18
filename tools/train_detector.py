"""Optional supervised YOLO training, with project-local weights/data/cache."""
import argparse
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from royale_ai.paths import inside

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--model",required=True,help="Existing local .pt base weights; no implicit download")
    p.add_argument("--data",required=True,help="Local Ultralytics dataset YAML")
    p.add_argument("--epochs",type=int,default=50)
    p.add_argument("--device",default="cpu")
    p.add_argument("--name",default="clash12")
    args=p.parse_args()
    model_path,data_path=inside(args.model),inside(args.data)
    if not model_path.is_file() or not data_path.is_file():
        p.error("Model and data YAML must already exist inside project")
    if not args.name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.name):
        p.error("Run name must be a plain directory name")
    os.environ["YOLO_CONFIG_DIR"]=str(ROOT/".cache"/"ultralytics")
    os.environ["TORCH_HOME"]=str(ROOT/".cache"/"torch")
    os.environ["TEMP"]=os.environ["TMP"]=str(ROOT/".tmp")
    os.chdir(ROOT)
    import yaml
    spec=yaml.safe_load(data_path.read_text(encoding='utf-8'))
    if spec.get('download'):
        p.error('Dataset auto-download is disabled; prepare local files')
    dataset_root=inside(spec.get('path',data_path.parent))
    for split in ('train','val','test'):
        values=spec.get(split,[])
        for value in values if isinstance(values,list) else [values]:
            candidate=Path(value)
            inside(candidate if candidate.is_absolute() else dataset_root/candidate)
    from ultralytics import YOLO
    model=YOLO(str(model_path))
    model.train(data=str(data_path),epochs=args.epochs,device=args.device,
                project=str(ROOT/"outputs"/"detector"),name=args.name,exist_ok=True,workers=0)

if __name__=='__main__':main()
