"""Build a standalone offline viewer from size-bounded public evaluation traces."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any


def safe_script_json(value: Any) -> str:
    """JSON remains inert inside an HTML script element, even for hostile names."""
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))


def load_public_trace(path: str | Path, *, maximum_bytes: int = 16_000_000) -> list[dict[str, Any]]:
    frames = []
    consumed = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            consumed += len(line.encode("utf-8"))
            if consumed > maximum_bytes:
                raise ValueError("Public trace exceeds viewer decompression budget")
            row = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON: {value}")))
            if row.get("kind") != "actor_observation":
                continue
            dimension = row.get("entity_dim")
            if type(dimension) is not int or not 1 <= dimension <= 4096:
                raise ValueError("Invalid public entity dimension")
            entities = row.get("visible_entities_sparse", [])
            if len(entities) > 512 or len(frames) >= 5000:
                raise ValueError("Public trace exceeds frame/entity budget")
            for entity in entities:
                indices, values = entity["indices"], entity["values"]
                if len(indices) != len(values) or len(set(indices)) != len(indices):
                    raise ValueError("Invalid sparse entity structure")
                if any(type(index) is not int or not 0 <= index < dimension for index in indices):
                    raise ValueError("Sparse entity index outside observation schema")
                if any(type(value) not in {int, float} for value in values):
                    raise ValueError("Entity features must be numeric")
            frames.append(row)
    if not frames:
        raise ValueError("Trace contains no public observation frames (possibly limited by trace budget)")
    return frames


def render_replay(
    evaluation_dir: str | Path, output_html: str | Path, *, variant: str = "model", episode_index: int = 0,
) -> dict[str, Any]:
    directory, output_html = Path(evaluation_dir), Path(output_html)
    if output_html.exists():
        raise FileExistsError("Choose a new replay HTML path to preserve prior artifacts")
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    episodes = [json.loads(line) for line in (directory / "episodes.jsonl").read_text(encoding="utf-8").splitlines() if line]
    matching = [row for row in episodes if row["variant"] == variant]
    if not 0 <= episode_index < len(matching):
        raise ValueError("No matching evaluated episode")
    episode = matching[episode_index]
    trace_path = directory / "traces" / Path(episode["trace"]["path"]).name
    if not trace_path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Public trace must stay inside the evaluation artifact")
    frames = load_public_trace(trace_path)
    identity = plan["environment_identity"]
    if identity.get("entity_extra_fields") or identity.get("visual_projection"):
        raise ValueError("This viewer requires the Royale public entity layout; native extra fields and unknown HP require a separately validated renderer")
    final_tick = episode.get("final_tick", 0)
    seconds_per_tick = episode.get("simulated_seconds", 0) / final_tick if final_tick else None
    if not seconds_per_tick or seconds_per_tick <= 0:
        # Current engine uses 50 ms; never guess for a foreign or fault-only trace.
        raise ValueError("Episode lacks a measured tick-to-time mapping")
    payload = {
        "schema": "arena-public-replay-view-v1", "frames": frames,
        "episode": {key: episode.get(key) for key in ("variant", "case_id", "candidate_side", "seed", "status", "outcome",
                    "winner", "final_tick", "simulated_seconds", "candidate_deploy_attempts", "candidate_accepted", "candidate_refused")},
        "trained": plan.get("checkpoint_metadata", {}).get("trained") is True,
        "backend": identity.get("backend"), "observation_version": identity.get("observation_version"),
        "entity_fields": identity["entity_base_fields"], "global_fields": identity["global_base_fields"],
        "unit_types": identity.get("unit_types", []), "cards": identity.get("cards", []),
        "grid_w": identity["obs_spec"]["grid_w"], "grid_h": identity["obs_spec"]["grid_h"],
        "seconds_per_tick": seconds_per_tick, "trace_every": plan.get("trace_every"),
        "trace_size_limited": episode["trace"].get("size_limited", False),
        "checkpoint_sha256": plan.get("checkpoint_sha256"),
    }
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(_TEMPLATE.replace("__SAFE_REPLAY_JSON__", safe_script_json(payload)), encoding="utf-8")
    return {"path": str(output_html), "frames": len(frames), "bytes": output_html.stat().st_size,
            "variant": variant, "trained_checkpoint": payload["trained"],
            "scope": "simulator actor-public state; not official game footage; no interpolation of unknown states"}


_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arena Lab · 模拟器公开战场回放</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Noto Sans",sans-serif;background:#101723;color:#edf2f8}
*{box-sizing:border-box}body{margin:0;padding:24px;max-width:1150px;margin-inline:auto}h1{font-size:24px;margin:0 0 10px}p{line-height:1.65;color:#bdcce0;margin:8px 0}main{display:grid;grid-template-columns:minmax(300px,520px) 1fr;gap:22px;margin-top:20px}.panel{background:#182435;border:1px solid #304158;border-radius:14px;padding:18px}canvas{display:block;width:100%;height:auto;background:#14382f;border-radius:10px}button,select{background:#263b56;color:#edf2f8;border:1px solid #526d91;border-radius:7px;padding:9px;font:inherit}button{cursor:pointer}input[type=range]{width:100%;margin:16px 0}.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.pill{padding:4px 9px;border-radius:20px;background:#263b56;font-size:13px}.hand{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}.card{padding:13px;background:#21334a;border:1px solid #3d5878;border-radius:9px;overflow-wrap:anywhere}.elixir{height:12px;border-radius:6px;background:#354258;overflow:hidden}.elixir>div{height:100%;background:#cb77e5;width:0}dl{display:grid;grid-template-columns:110px 1fr;gap:10px;font-size:14px}dt{color:#a5b8cf}dd{margin:0;overflow-wrap:anywhere}.status{white-space:pre-line;line-height:1.6;font-size:14px}.legend{font-size:13px}.blue{color:#65adff}.red{color:#ff8c8b}footer{margin-top:16px;font-size:12px;color:#9aaec7;overflow-wrap:anywhere}@media(max-width:760px){body{padding:14px}main{grid-template-columns:1fr}}
</style></head><body>
<h1>Arena Lab · 模拟器公开战场回放</h1>
<p>显示策略当时允许观察的实体与己方信息。此图由采样状态绘制，不是官方游戏画面；采样帧之间的移动与操作过程没有被补造。</p>
<div class="controls"><span id="training" class="pill"></span><span id="sampling" class="pill"></span><span id="result" class="pill"></span></div>
<main><section class="panel"><canvas id="arena" width="540" height="900" aria-label="模拟器公开战场"></canvas>
<input id="timeline" type="range" min="0" max="0" value="0" aria-label="回放帧">
<div class="controls"><button id="play">播放</button><button id="back">上一帧</button><button id="next">下一帧</button><label>速度 <select id="speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="5">5×</option><option value="20">20×</option></select></label><span id="clock"></span></div>
<p class="legend"><span class="blue">● 己方</span>　<span class="red">● 对方可见实体</span>　方框为建筑或塔，圆环为法术，十字为弹体。点击实体查看公开字段。</p></section>
<aside class="panel"><h2 style="font-size:18px;margin-top:0">当前观察与行动</h2><p id="elixir-label"></p><div class="elixir"><div id="elixir-fill"></div></div><div id="hand" class="hand"></div><p id="action" class="status"></p><hr style="border:0;border-top:1px solid #304158;margin:20px 0"><p id="selected" class="status">点击战场实体查看其公开位置与血量比例。</p><dl id="details"></dl><p id="limits" class="status"></p></aside></main>
<footer id="fingerprint"></footer>
<script type="application/json" id="replay-data">__SAFE_REPLAY_JSON__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById("replay-data").textContent);
const el=id=>document.getElementById(id),canvas=el("arena"),ctx=canvas.getContext("2d");
const fields=Object.create(null),globals=Object.create(null);
data.entity_fields.forEach((name,index)=>fields[name]=index);data.global_fields.forEach((name,index)=>globals[name]=index);
let index=0,playing=false,timer=null,painted=[];
const pad=27,aw=canvas.width-2*pad,ah=canvas.height-2*pad;
const point=(x,y)=>[pad+x*aw,pad+(1-y)*ah];
const feature=(row,name,fallback=0)=>fields[name]===undefined?fallback:row[fields[name]];
const healthLabel=row=>(feature(row,"spell")>.5||feature(row,"projectile")>.5)?"血量：不适用":`血量比例：${(feature(row,"hp_ratio")*100).toFixed(1)}%`;
const cardName=id=>id>0?(data.cards[id-1]||`未知卡牌 ${id}`):"空槽";
function entityName(row){
 let base=data.entity_fields.length;
 for(let i=0;i<data.unit_types.length;i++)if(row[base+i+1]>.5)return String(data.unit_types[i]);
 base+=data.unit_types.length+1;
 for(let i=0;i<data.cards.length;i++)if(row[base+i+1]>.5)return String(data.cards[i]);
 return feature(row,"king")?"国王塔":feature(row,"princess")?"公主塔":feature(row,"projectile")?"弹体":feature(row,"spell")?"法术":"未知可见实体";
}
function decode(frame){return frame.visible_entities_sparse.map(sparse=>{const row=new Float32Array(frame.entity_dim);sparse.indices.forEach((i,k)=>row[i]=sparse.values[k]);return row;});}
function text(node,value){node.textContent=String(value);}
function describeAction(frame){const a=frame.action;if(a.card===0)return "动作：等待";if(a.card>4)return `动作：触发己方技能按钮 ${a.card-4}`;const x=a.position%data.grid_w,y=Math.floor(a.position/data.grid_w);return `动作：槽位 ${a.card} · ${cardName(frame.hand_ids[a.card-1])}\n目标格 (${x}, ${y}) · 玩家视角`+(Number.isFinite(a.card_probability)?`\n动作概率 ${(100*a.card_probability).toFixed(1)}%`:"");}
function draw(){
 const frame=data.frames[index];ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle="#14382f";ctx.fillRect(0,0,canvas.width,canvas.height);
 ctx.strokeStyle="rgba(220,255,225,.08)";ctx.lineWidth=1;
 for(let x=0;x<=data.grid_w;x++){const sx=pad+x/data.grid_w*aw;ctx.beginPath();ctx.moveTo(sx,pad);ctx.lineTo(sx,pad+ah);ctx.stroke();}
 for(let y=0;y<=data.grid_h;y++){const sy=pad+y/data.grid_h*ah;ctx.beginPath();ctx.moveTo(pad,sy);ctx.lineTo(pad+aw,sy);ctx.stroke();}
 ctx.fillStyle="#28556d";ctx.fillRect(pad,pad+ah*.48,aw,ah*.04);ctx.fillStyle="#b58b57";[.25,.75].forEach(x=>ctx.fillRect(pad+aw*x-23,pad+ah*.473,46,ah*.054));
 painted=[];
 for(const row of decode(frame)){
  const own=feature(row,"own_team")>.5,[x,y]=point(feature(row,"x"),feature(row,"y"));
  const tower=feature(row,"king")>.5||feature(row,"princess")>.5,building=feature(row,"building")>.5;
  const spell=feature(row,"spell")>.5,projectile=feature(row,"projectile")>.5;
  const r=tower?18:building?13:projectile?4:spell?18:9;
  ctx.strokeStyle=own?"#65adff":"#ff8c8b";ctx.fillStyle=own?"#397ad1":"#c85e64";ctx.lineWidth=2;
  if(projectile){ctx.beginPath();ctx.moveTo(x-r,y);ctx.lineTo(x+r,y);ctx.moveTo(x,y-r);ctx.lineTo(x,y+r);ctx.stroke();}
  else if(tower||building){ctx.fillRect(x-r,y-r,2*r,2*r);ctx.strokeRect(x-r,y-r,2*r,2*r);}
  else{ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);if(!spell)ctx.fill();ctx.stroke();}
  if(!spell&&!projectile){const hp=Math.max(0,Math.min(1,feature(row,"hp_ratio")));ctx.fillStyle="#182129";ctx.fillRect(x-16,y-r-9,32,4);ctx.fillStyle=own?"#6ee3a1":"#ffc174";ctx.fillRect(x-16,y-r-9,32*hp,4);}
  const name=entityName(row);ctx.fillStyle="#f2f6ff";ctx.font="10px system-ui";ctx.textAlign="center";if(!projectile)ctx.fillText(name.slice(0,22),x,y+r+12);painted.push({x,y,r,row,name});
 }
 if(frame.action.card>=1&&frame.action.card<=4){const pos=frame.action.position,[x,y]=point((pos%data.grid_w+.5)/data.grid_w,(Math.floor(pos/data.grid_w)+.5)/data.grid_h);ctx.strokeStyle="#ffe56f";ctx.lineWidth=3;ctx.beginPath();ctx.arc(x,y,22,0,2*Math.PI);ctx.stroke();}
 el("timeline").value=String(index);text(el("clock"),`${(frame.tick*data.seconds_per_tick).toFixed(1)} 秒 · ${index+1}/${data.frames.length}`);
 const value=Number(frame.global_features[globals.own_elixir])*10;text(el("elixir-label"),`己方圣水：${Number.isFinite(value)?value.toFixed(2):"未知"}`);el("elixir-fill").style.width=`${Number.isFinite(value)?Math.max(0,Math.min(100,value*10)):0}%`;
 el("hand").replaceChildren();frame.hand_ids.forEach((id,slot)=>{const node=document.createElement("div");node.className="card";const field=globals[`hand${slot}_cost`],cost=field===undefined?null:frame.global_features[field]*10;text(node,`${slot+1}. ${cardName(id)}${cost===null?"":` · ${cost.toFixed(0)} 费`}`);el("hand").appendChild(node);});
 text(el("action"),describeAction(frame));
}
function stop(){playing=false;if(timer!==null)clearTimeout(timer);timer=null;text(el("play"),"播放");}
function schedule(){if(!playing)return;if(index>=data.frames.length-1){stop();return;}const gap=(data.frames[index+1].tick-data.frames[index].tick)*data.seconds_per_tick*1000/Number(el("speed").value);timer=setTimeout(()=>{index++;draw();schedule();},Math.max(1,gap));}
el("play").addEventListener("click",()=>{if(playing){stop();return;}if(index>=data.frames.length-1)index=0;playing=true;text(el("play"),"暂停");draw();schedule();});
el("back").addEventListener("click",()=>{stop();index=Math.max(0,index-1);draw();});el("next").addEventListener("click",()=>{stop();index=Math.min(data.frames.length-1,index+1);draw();});
el("timeline").max=String(data.frames.length-1);el("timeline").addEventListener("input",event=>{stop();index=Number(event.target.value);draw();});el("speed").addEventListener("change",()=>{if(playing){clearTimeout(timer);schedule();}});
canvas.addEventListener("click",event=>{const rect=canvas.getBoundingClientRect(),x=(event.clientX-rect.left)*canvas.width/rect.width,y=(event.clientY-rect.top)*canvas.height/rect.height;const found=painted.map(item=>({...item,d:Math.hypot(item.x-x,item.y-y)})).sort((a,b)=>a.d-b.d)[0];if(!found||found.d>30)return;const row=found.row;text(el("selected"),`${feature(row,"own_team")>.5?"己方":"对方可见"} ${found.name}\n坐标：(${feature(row,"x").toFixed(3)}, ${feature(row,"y").toFixed(3)})\n${healthLabel(row)}${feature(row,"out_of_arena")>.5?"\n位置已裁到边界，出界幅度未知":""}`);});
text(el("training"),data.trained?"检查点：已完成训练":"检查点：未训练／接口验证");text(el("sampling"),`每 ${data.trace_every} 次联合决策采样${data.trace_size_limited?" · 已达 trace 体积上限":""}`);text(el("result"),`结束：${data.episode.status} / ${data.episode.outcome||"无有效胜负"}`);
const detailPairs=[["后端",data.backend],["观察协议",data.observation_version],["策略",data.episode.variant],["案例",data.episode.case_id],["玩家座位",data.episode.candidate_side],["种子",data.episode.seed],["实际接收／拒绝",`${data.episode.candidate_accepted??"未知"} / ${data.episode.candidate_refused??"未知"}`]];
for(const [key,value]of detailPairs){const dt=document.createElement("dt"),dd=document.createElement("dd");text(dt,key);text(dd,value);el("details").append(dt,dd);}
text(el("limits"),"仅显示该玩家可观察的信息；没有敌方手牌、圣水或隐藏单位。位置和血量来自固定版本模拟器。技能与进化规则的覆盖、校准误差以环境报告为准。"+(data.trace_size_limited?"\n回放只含体积上限内保存的采样帧，可能早于对局终点停止。":""));
text(el("fingerprint"),`Checkpoint SHA256: ${data.checkpoint_sha256||"未记录"}`);draw();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--variant", default="model")
    parser.add_argument("--episode", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(render_replay(args.evaluation, args.output, variant=args.variant, episode_index=args.episode), ensure_ascii=False))


if __name__ == "__main__":
    main()
