// Offline source/behavior test. Does not launch or automate a browser.
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const root = path.resolve(__dirname,'..');
const target = path.resolve(root,process.argv[2]||'outputs/replay.html');
if (!target.startsWith(root+path.sep)) throw Error('File must stay in project');
const html = fs.readFileSync(target,'utf8');
const blocks=[...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
const payload=blocks.find(b=>b[1].includes('application/json'));
const rows=JSON.parse(payload[2]);
let operations=0;
const ctx=new Proxy({}, {get:(obj,key)=>obj[key]||(()=>{operations++;}),set:(obj,key,val)=>{obj[key]=val;return true;}});
const elements={};
for(const match of html.matchAll(/id="([^"]+)"/g)) {
  if(elements[match[1]])throw Error('Duplicate DOM id');
  elements[match[1]]={textContent:'',style:{},getContext:()=>ctx,replaceChildren:(...kids)=>{},append:(...kids)=>{}};
}
elements.data.textContent=payload[2];
const sandbox={document:{getElementById:id=>{if(!elements[id])throw Error('Missing id '+id);return elements[id];},
createElement:()=>({append:()=>{}})},setInterval:()=>1,clearInterval:()=>{},JSON,Math};
vm.createContext(sandbox);
for(const b of blocks)if(!b[1].includes('application/json'))vm.runInContext(b[2],sandbox,{timeout:1000});
if(!elements.clock.textContent.endsWith(' s')||operations<10)throw Error('Initial draw failed');
elements.slider.value=45;elements.slider.oninput();
elements.play.onclick();if(elements.play.textContent!=='暂停')throw Error('Play toggle failed');
elements.play.onclick();if(elements.play.textContent!=='播放')throw Error('Pause toggle failed');
console.log(JSON.stringify({status:'pass',frames:rows.length,canvas_operations:operations,
  checked:'data parsing, first render, frame selection, play/pause; visual layout unverified'}));
