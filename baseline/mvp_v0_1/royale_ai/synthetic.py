"""Generate synthetic screenshot fixtures. These are NOT real-game vision data."""
import json
from .cards import CARDS
from .paths import output_path, inside
from .perception import vision_libs, write_image
from .simulator import Arena
from .state import Entity

def build_demo(directory="outputs/vision_demo"):
    cv2,np = vision_libs()
    root = inside(directory)
    image = np.zeros((960,540,3),dtype=np.uint8)
    image[:]=(22,25,30)
    image[100:740,90:450]=(60,90,55)
    image[416:424,90:450]=(140,100,50)
    anchor = np.zeros((35,130,3),dtype=np.uint8)
    cv2.putText(anchor,"BATTLE",(3,27),cv2.FONT_HERSHEY_SIMPLEX,.8,(255,255,255),2)
    image[20:55,205:335]=anchor
    write_image(root/"anchor.png",anchor)
    digits_dir=root/"digits"
    for digit in range(10):
        glyph=np.zeros((32,24,3),dtype=np.uint8)
        cv2.putText(glyph,str(digit),(2,25),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),2)
        write_image(digits_dir/f"{digit}.png",glyph)
    def number(roi,text):
        x1,y1,x2,y2=roi
        image[y1:y2,x1:x2]=0
        for i,char in enumerate(str(text)):
            cv2.putText(image,char,(x1+2+24*i,y1+25),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),2)
    slots=[[30+125*i,820,135+125*i,920] for i in range(4)]
    templates={}
    for i,cid in enumerate(CARDS):
        rng=np.random.default_rng(i+20)
        texture=rng.integers(15,180,(100,105,3),dtype=np.uint8)
        cv2.putText(texture,cid[:6],(5,50),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
        path=root/"cards"/f"{cid}.png"
        write_image(path,texture)
        templates[cid]=path.relative_to(inside('.')).as_posix()
    hand=["mini_pekka","musketeer","arrows","knight"]
    for roi,cid in zip(slots,hand):
        from .perception import read_image
        image[roi[1]:roi[3],roi[0]:roi[2]]=read_image(templates[cid])
    elixir_roi=[440,770,530,806]
    number(elixir_roi,7)
    hp_rois={}
    for t in Arena(shuffle=False).towers:
        # Fixture HP values are deliberately placed in a separate synthetic HUD.
        index=len(hp_rois)
        roi=[5+(index%3)*178,57+(index//3)*36,125+(index%3)*178,89+(index//3)*36]
        hp_rois[t.id]=roi
        number(roi,int(t.hp))
    units=[("giant",1,4,23,(20,30,240)),("minions",1,14,21,(230,30,230)),
           ("knight",0,4,26,(240,150,30))]
    markers=[]
    for cid,team,x,y,color in units:
        px,py=round(90+x*20),round(100+y*20)
        cv2.circle(image,(px,py),7,color,-1)
        markers.append({"card":cid,"team":team,"bgr":list(color)})
    config={"purpose":"synthetic fixture only; do not use this calibration on real game screenshots",
        "image_size":[540,960],"arena":[90,100,450,740],"slots":slots,
        "battle_anchor":{"roi":[205,20,335,55],"template":(root/"anchor.png").relative_to(inside('.')).as_posix()},
        "digit_templates":digits_dir.relative_to(inside('.')).as_posix(),
        "card_templates":templates,"elixir_roi":elixir_roi,"tower_hp_rois":hp_rois,
        "units":{"backend":"synthetic","markers":markers},"template_threshold":.9}
    config_path=output_path(root/"calibration.json")
    config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    write_image(root/"frame.png",image)
    return root/"frame.png",config_path
