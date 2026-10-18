/* Synthetic DOM/control contract tests. No browser, sockets, VM, SSH or RPC. */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict'),crypto=require('crypto');
const ROOT=path.resolve(__dirname,'..');
const sourcePath=path.join(ROOT,'royale_phase2','human_ui.html');
const html=fs.readFileSync(sourcePath,'utf8');
const match=html.match(/<script>([\s\S]*?)<\/script>/);
assert(match,'inline script missing');
const code=match[1].replace('__TOKEN__',JSON.stringify('synthetic-token'));
new vm.Script(code,{filename:'human_ui.inline.js'});
const clone=value=>JSON.parse(JSON.stringify(value));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){return {
 mode:'human',ready:true,terminal:false,playing:false,tick:90,result:null,error:null,folder:'synthetic/session',jobs:{},
 human:{owner:1,generation:7,tick:90,elixir_raw:60000,hand:[
 {slot:0,card:28000000,name:'火球',cost:4,affordable:true},
 {slot:1,card:28000001,name:'万箭',cost:3,affordable:true},
 {slot:2,card:26000003,name:'高费卡',cost:7,affordable:false},
 {slot:3,card:26000000,name:'近战卡',cost:3,affordable:true}],last_action:null},
 ai:{owner:0,kind:'python_rules',policy_path:'examples/platform_battlefield_policy.py',last_decision:{tick:90,policy_reason:'敌方对象触发防守分支',model_action:{kind:'wait'},policy_input:{schema:'royale-platform-battlefield-observation.v1',information_mode:'battlefield',entities:[{owner:1,entity_id:77},{owner:0,entity_id:88}],history:[{tick:89}]}}},
 feedback:[],stream:{available:true,running:true,effective_fps:12.4},last_frame:{sha256:'a'.repeat(64),tick:90},
 placement:{source_width:1080,source_height:1920,hit_rect:[54,282,1026,1666],world_rect:[111,436,967,1666],world_width:18000,world_height:32000,basis:'pinned_measured_hud_grid',exact_camera_projection_verified:false}
};}
function element(id){
 let markup='';
 const classes=new Set();
 const e={id,textContent:'',style:{},hidden:false,disabled:false,open:false,value:id==='nativeX'?'9000':id==='nativeY'?'24000':'',dataset:{},attributes:{},listeners:{},markupWrites:0,srcWrites:[],naturalWidth:1080,naturalHeight:1920,
  rect:{left:0,top:0,width:108,height:192},
  classList:{toggle(name,enabled){if(enabled===undefined)enabled=!classes.has(name);if(enabled)classes.add(name);else classes.delete(name);},add(name){classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);}},
  setAttribute(name,value){this.attributes[name]=String(value);},
  addEventListener(name,handler){this.listeners[name]=handler;},
  getBoundingClientRect(){return this.rect;}
 };
 Object.defineProperty(e,'innerHTML',{get:()=>markup,set:value=>{markup=value;e.markupWrites++;}});
 Object.defineProperty(e,'src',{get:()=>e.srcWrites.at(-1)||'',set:value=>e.srcWrites.push(value)});
 return e;
}
async function harness(initial=fixture()){
 const els=new Map(),posts=[],gets=[];
 let state=clone(initial),postBehavior='success',sequence=0;
 const get=id=>{if(!els.has(id))els.set(id,element(id));return els.get(id);};
 const context=vm.createContext({document:{getElementById:get},console,setInterval:()=>1,clearInterval:()=>{},
  fetch:async(url,options={})=>{
   if(options.method==='POST'){
    const body=JSON.parse(options.body);posts.push({url,headers:options.headers,body});
    if(postBehavior==='network_error')throw Error('synthetic network failure');
    if(postBehavior==='reject')return {ok:false,status:400,json:async()=>({error:'stale card or generation'})};
    if(postBehavior==='forbidden')return {ok:false,status:403,json:async()=>({error:'old page token'})};
    if(postBehavior==='missing_ticket')return {ok:true,json:async()=>({})};
    return {ok:true,json:async()=>({ticket:'ticket-'+(++sequence)})};
   }
   gets.push(url);return {ok:true,json:async()=>clone(state)};
  }});
 vm.runInContext(code,context,{timeout:1000});await flush();await flush();
 return {get,posts,gets,context,eval:expression=>vm.runInContext(expression,context,{timeout:1000}),
  state:()=>state,setState:async value=>{state=clone(value);await vm.runInContext('poll()',context);await flush();},
  setPostBehavior:value=>postBehavior=value};
}
const cases=[];
async function test(name,body){try{await body();cases.push({name,status:'pass'});}catch(error){cases.push({name,status:'fail',error:error.stack});}}
(async()=>{
 await test('syntax_and_four_named_cards',async()=>{const h=await harness();assert.equal((h.get('hand').innerHTML.match(/data-slot=/g)||[]).length,4);assert.match(h.get('hand').innerHTML,/火球/);assert.match(h.get('elixir').textContent,/6\.00/);assert.match(html,/上方：AI（红方）/);assert.match(html,/下方：你（蓝方）/);assert.match(h.get('clock').textContent,/游戏时间 4\.50 秒/);assert.match(h.get('mediaStatus').textContent,/采集约 12\.4 帧\/秒/);assert.equal(h.posts.length,0);});
 await test('initial_page_never_submits_actions',async()=>{const h=await harness();await h.eval('poll()');assert(h.gets.every(url=>url==='/api/state'));assert.equal(h.posts.length,0);});
 await test('unaffordable_card_cannot_select',async()=>{const h=await harness();assert.equal(h.eval('selectCard(2)'),false);assert.equal(h.eval('selected'),null);assert.equal(h.posts.length,0);});
 await test('image_hit_area_and_letterboxing',async()=>{const h=await harness();assert.equal(h.eval('normalizedPoint(2,2)'),null);const center=clone(h.eval('normalizedPoint(54,96)'));assert.equal(center.u,.5);assert.equal(center.v,.5);h.get('gameImage').rect={left:10,top:20,width:100,height:200};assert.equal(h.eval('normalizedPoint(60,21)'),null);const boxed=h.eval('normalizedPoint(60,120)');assert(Math.abs(boxed.u-.5)<1e-9);assert(Math.abs(boxed.v-.5)<1e-9);});
 await test('teaching_submission_exact_contract_and_one_pending',async()=>{const h=await harness();h.eval('selectCard(0)');await h.eval('submitCard({u:0.5,v:0.6})');assert.equal(h.posts.length,1);assert.deepEqual(h.posts[0].body,{command:'human_turn',slot:0,card:28000000,generation:7,u:.5,v:.6});assert.equal(h.posts[0].headers['X-Platform-Token'],'synthetic-token');await h.eval('submitCard({u:0.4,v:0.5})');assert.equal(h.posts.length,1);assert.equal(h.get('nativeSubmit').disabled,true);});
 await test('continuous_submission_only_registers',async()=>{const h=await harness();h.get('continuousMode').onclick();h.eval('selectCard(1)');await h.eval('submitCard({u:0.5,v:0.6})');assert.equal(h.posts[0].body.command,'human_play');assert.equal(h.posts.length,1);});
 await test('switching_to_teaching_pauses_a_running_match',async()=>{const state=fixture();state.playing=true;const h=await harness(state);h.get('teachMode').onclick();await flush();await flush();assert.equal(h.posts.length,1);assert.equal(h.posts[0].body.command,'pause');});
 await test('job_completion_releases_selection_without_claiming_consumption',async()=>{const h=await harness();h.eval('selectCard(0)');await h.eval('submitCard({u:0.5,v:0.6})');const state=h.state();state.tick=110;state.jobs['ticket-1']={status:'done'};state.feedback=[{owner:1,card_consumed:false,tick:91}];await h.setState(state);assert.equal(h.eval('pending'),null);assert.equal(h.eval('selected'),null);assert.match(h.get('humanReceipt').textContent,/尚未确认/);assert.match(h.get('message').textContent,/已结束并暂停/);});
 await test('rejected_action_is_not_retried',async()=>{const h=await harness();h.setPostBehavior('reject');h.eval('selectCard(0)');await h.eval('submitCard({u:0.5,v:0.6})');await h.eval('poll()');assert.equal(h.posts.length,1);assert.equal(h.eval('selected'),null);assert.match(h.get('message').textContent,/被拒绝/);});
 await test('old_page_token_requires_page_refresh',async()=>{const h=await harness();h.setPostBehavior('forbidden');h.eval('selectCard(0)');await h.eval('submitCard({u:0.5,v:0.6})');assert.match(h.get('message').textContent,/刷新整个页面/);assert.equal(h.posts.length,1);});
 await test('unknown_network_outcome_blocks_and_never_retries',async()=>{const h=await harness();h.setPostBehavior('network_error');h.eval('selectCard(0)');await h.eval('submitCard({u:0.5,v:0.6})');await h.eval('poll()');assert.equal(h.posts.length,1);assert.equal(h.eval('pending.uncertain'),true);assert.equal(h.eval('selectCard(1)'),false);assert.match(h.get('message').textContent,/执行结果未知/);});
 await test('missing_ticket_is_not_treated_as_success',async()=>{const h=await harness();h.setPostBehavior('missing_ticket');h.eval('selectCard(0)');await h.eval('submitCard({u:0.5,v:0.6})');assert.equal(h.eval('pending.uncertain'),true);assert.equal(h.posts.length,1);});
 await test('changed_card_or_generation_clears_stale_selection',async()=>{const h=await harness();h.eval('selectCard(0)');const state=h.state();state.human.generation=8;await h.setState(state);assert.equal(h.eval('selected'),null);await h.eval('submitCard({u:0.5,v:0.6})');assert.equal(h.posts.length,0);});
 await test('drag_keeps_source_dom_until_dragend',async()=>{const h=await harness();const writes=h.get('hand').markupWrites;const data={};const event={target:{closest:()=>({dataset:{slot:'0'},disabled:false})},preventDefault(){throw Error('unexpected prevented drag');},dataTransfer:{setData:(key,value)=>data[key]=value}};h.get('hand').listeners.dragstart(event);assert.equal(h.get('hand').markupWrites,writes);await h.eval('poll()');assert.equal(h.get('hand').markupWrites,writes);assert.equal(JSON.parse(data['text/plain']).generation,7);h.get('hand').listeners.dragend();assert(h.get('hand').markupWrites>writes);});
 await test('drop_submits_once_and_rejects_external_payload',async()=>{const h=await harness();h.eval('selectCard(0)');const event={clientX:54,clientY:96,preventDefault(){},dataTransfer:{getData:()=>JSON.stringify({slot:0,card:28000000,generation:7})}};h.get('arena').listeners.drop(event);await flush();await flush();assert.equal(h.posts.length,1);h.get('arena').listeners.drop(event);await flush();assert.equal(h.posts.length,1);const other=await harness();other.eval('selectCard(0)');event.dataTransfer.getData=()=>JSON.stringify({slot:1,card:999,generation:7});other.get('arena').listeners.drop(event);await flush();assert.equal(other.posts.length,0);});
 await test('same_sha_png_is_restored_after_stream_failure',async()=>{const state=fixture();state.stream.available=false;const h=await harness(state);assert.equal(h.get('gameImage').src,'/api/frame?v='+'a'.repeat(64));state.stream.available=true;await h.setState(state);assert.equal(h.get('gameImage').src,'/api/stream.mjpg');h.get('gameImage').onerror();assert.equal(h.get('gameImage').src,'/api/frame?v='+'a'.repeat(64));assert.equal(h.get('streamWarning').style.display,'block');assert.match(h.get('mediaStatus').textContent,/游戏时间 4\.50 秒/);});
 await test('ai_summary_uses_actual_last_input_and_reason',async()=>{const h=await harness();assert.match(h.get('aiInputTick').textContent,/游戏时间 4\.50 秒/);assert.match(h.get('aiInputs').innerHTML,/敌方对象 1/);assert.match(h.get('aiInputs').innerHTML,/已发生下牌 1/);assert.equal(h.get('aiReason').textContent,'敌方对象触发防守分支');assert.equal(h.get('researchJSON').textContent,'');h.get('research').open=true;h.get('research').ontoggle();assert.match(h.get('researchJSON').textContent,/policy_input/);assert.match(h.get('researchJSON').textContent,/"tick": 90/);assert.match(h.get('researchJSON').textContent,/"human_owner": 1/);});
 await test('untrusted_card_name_is_escaped',async()=>{const state=fixture();state.human.hand[0].name='<img src=x onerror=alert(1)>';const h=await harness(state);assert.match(h.get('hand').innerHTML,/&lt;img/);assert(!h.get('hand').innerHTML.includes('<img'));});
 await test('terminal_stops_play_but_allows_evidence_save',async()=>{const state=fixture();state.terminal=true;state.result=1;const h=await harness(state);assert.equal(h.eval('selectCard(0)'),false);assert.equal(h.get('waitButton').disabled,true);assert.equal(h.get('reportButton').disabled,false);assert.equal(h.get('captureButton').disabled,false);assert.match(h.get('endNotice').textContent,/你获胜/);const other=fixture();other.mode='archive';await h.setState(other);assert.equal(await h.eval("sendCommand('human_wait')"),false);assert.equal(h.posts.length,0);assert.match(h.get('message').textContent,/不是人机演示/);});
 await test('native_coordinate_request_is_explicit',async()=>{const h=await harness();h.eval('selectCard(0)');await h.eval('submitCard({x:9000,y:24000})');assert.deepEqual(h.posts[0].body,{command:'human_turn',slot:0,card:28000000,generation:7,x:9000,y:24000});assert.match(h.get('requestText').textContent,/最终部署位置未验证/);});
 const report={schema:'phase2-human-ui-synthetic-tests.v1',status:cases.every(c=>c.status==='pass')?'pass':'fail',
  html:'royale_phase2/human_ui.html',html_sha256:crypto.createHash('sha256').update(html).digest('hex'),node_version:process.version,
  test_count:cases.length,tests:cases,browser_used:false,vm_used:false,ssh_used:false,native_rpc_used:false,real_http_used:false,
  scope:'Synthetic DOM, fake fetch and supplied contract only; not a browser or native battle acceptance'};
 const folder=path.join(ROOT,'outputs/platform_next/human/ui_synthetic');fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report,null,2));if(report.status!=='pass')process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
