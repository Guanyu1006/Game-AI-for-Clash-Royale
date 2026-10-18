"""Interactive ROI wizard. Run manually when a real screenshot is available."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from royale_ai.cards import CARDS
from royale_ai.paths import inside,output_path,ROOT
from royale_ai.perception import read_image,write_image,vision_libs,crop

def main():
    p=argparse.ArgumentParser(description="Select ROIs on your actual game screenshot")
    p.add_argument("image")
    p.add_argument("--hand",nargs=4,required=True,choices=list(CARDS))
    p.add_argument("--output",default="config/live.json")
    p.add_argument("--templates",default="data/templates/live")
    p.add_argument("--update-cards-only",action="store_true")
    args=p.parse_args()
    cv2,_=vision_libs()
    image=read_image(args.image)
    dest=inside(args.output)
    config=json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else {}
    def select(label):
        print(label,flush=True)
        x,y,w,h=cv2.selectROI(label,image,showCrosshair=True,fromCenter=False)
        cv2.destroyWindow(label)
        if not w or not h: raise ValueError("ROI selection cancelled")
        return [int(x),int(y),int(x+w),int(y+h)]
    config["image_size"]=[image.shape[1],image.shape[0]]
    if not args.update_cards_only:
        config["arena"]=select("Arena: playable board rectangle")
        config["slots"]=[select(f"Hand slot {i+1}: card art (exclude changing elixir overlays)") for i in range(4)]
        roi=select("Battle anchor: stable battle-only icon")
        template=inside(args.templates)/"battle_anchor.png"
        write_image(template,crop(image,roi))
        config["battle_anchor"]={"roi":roi,"template":template.relative_to(ROOT).as_posix()}
        config["elixir_roi"]=select("Own elixir: numeric text only")
        config["tower_hp_rois"]={key:select(f"Tower HP digits: {key}") for key in
            ["0_left","0_right","0_king","1_left","1_right","1_king"]}
    if "slots" not in config:
        raise ValueError("Run full calibration first")
    templates=config.setdefault("card_templates",{})
    for cid,roi in zip(args.hand,config["slots"]):
        template=inside(args.templates)/"cards"/f"{cid}.png"
        write_image(template,crop(image,roi))
        templates[cid]=template.relative_to(ROOT).as_posix()
    config.setdefault("digit_templates",str(Path(args.templates)/"digits").replace('\\','/'))
    config.setdefault("units",{"backend":"yolo","model":"models/clash_detector.pt","confidence":.8,
        "classes":{"enemy_giant":{"card":"giant","team":1},"friendly_giant":{"card":"giant","team":0}}})
    config.setdefault("template_threshold",.88)
    # Manual review is needed for actual tower HP levels, all detector labels and digit font.
    config["calibrated"]=False
    config["purpose"]="Real screenshot geometry; complete digits, tower stats and YOLO classes before live use"
    output_path(dest).write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Saved {dest}. Complete numeric glyph templates and detector class mapping.")

if __name__=="__main__":main()
