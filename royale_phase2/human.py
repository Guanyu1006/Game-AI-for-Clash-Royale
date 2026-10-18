"""Single-worker human-vs-policy demo on the existing pinned native engine."""
from copy import deepcopy
import math
import time

from .platform import LiveSession, research_action
from .observability import build_observation
from .battlefield import build_battlefield_observation
from .viewer import append_json, write_json


DEFAULT_HUMAN_POLICY = 'examples/human_ai_policy.py'
CARD_NAMES = {26000000: '骑士', 26000001: '弓箭手', 26000005: '亡灵',
              28000001: '万箭齐发', 28000000: '火球', 26000003: '巨人',
              26000014: '火枪手', 26000018: '迷你皮卡'}
PLACEMENT = {'source_width':1080, 'source_height':1920,
             'hit_rect':[54,282,1026,1666], 'world_rect':[111,436,967,1666],
             'world_width':18000, 'world_height':32000,
             'basis':'pinned_measured_hud_grid', 'exact_camera_projection_verified':False,
             'source':'third_party/phase2/firstlight/native_runner/probe/native_touch_layout.h'}


def placement(data):
    """Use the pinned overlay's measured grid, not a claimed camera transform."""
    if 'u' in data or 'v' in data:
        for key in ('u','v'):
            if type(data.get(key)) not in (int,float) or not math.isfinite(data[key]) or not 0<=data[key]<=1:
                raise ValueError('画面落点必须在有效图像内')
        px=data['u']*1080; py=data['v']*1920
        if not 54<=px<=1026 or not 282<=py<=1666:
            raise ValueError('请把卡牌放到战场内，不能放在手牌或按钮上')
        x=(max(0,min(17,int((px-111)*18/(967-111))))*1000+500)
        y=(max(0,min(31,int((py-436)*32/(1666-436))))*1000+500)
        return x,y
    for key,maximum in (('x',17999),('y',31999)):
        if type(data.get(key)) is not int or not 0<=data[key]<=maximum:
            raise ValueError('原生坐标超出范围')
    return data['x'],data['y']


class HumanSession(LiveSession):
    def __init__(self, mode, output, **kwargs):
        if mode!='human': raise ValueError('HumanSession requires human mode')
        if kwargs.get('information_mode','auto') not in ('auto','battlefield'):
            raise ValueError('人机演示使用 battlefield 输入；其他研究模式请用 Agent')
        kwargs['policy_file']=kwargs.get('policy_file') or DEFAULT_HUMAN_POLICY
        self.stream=None; self.human_actions=[]; self.semantic_sha=None; self.timings=[]
        super().__init__(mode, output, **kwargs)

    def _prepare_match(self, match):
        # Preserve the original ordinary eight-card fixture and its hand order.
        match['battle']['avatar0']['name']='Rule-AI'
        match['battle']['avatar1']['name']='You-Human'
        return match

    def public(self):
        result=super().public()
        with self.lock:
            actor=deepcopy(self.actors.get(1)); last=deepcopy(self.human_actions[-1]) if self.human_actions else None
        hand=[]
        if actor:
            hand=[{**card,'name':CARD_NAMES.get(card['card'],str(card['card'])),
                   'affordable':card['cost']*10000<=actor['own_elixir_raw']} for card in actor['hand']]
        result['human']={'owner':1,'elixir_raw':actor['own_elixir_raw'] if actor else None,
                         'generation':result.get('battlefield',{}).get('generation') if result.get('battlefield') else None,
                         'tick':result.get('tick'),'hand':hand,'last_action':last}
        result['ai']={'owner':0,'kind':'python_rules' if self.policy_file==DEFAULT_HUMAN_POLICY else 'python_plugin',
                      'policy_path':self.policy_file,'last_decision':result['decisions'][-1] if result['decisions'] else None}
        result['placement']=deepcopy(PLACEMENT)
        result['stream']=self.stream.status() if self.stream else {'available':False,'running':False,'reason':'等待原生画面'}
        result['stream']['available']=bool(result['stream'].get('running') and result['stream'].get('counts_received',0)>0
                                           and (result['stream'].get('last_frame_age_seconds') or 0)<5)
        result['card_names']={str(key):value for key,value in CARD_NAMES.items()}
        result['demo_scope']='Human owner1 vs Python policy owner0. Live frames are asynchronous, not exact-tick training labels.'
        return result

    def submit(self, command, **data):
        if command not in ('human_turn','human_play','human_wait'): return super().submit(command,**data)
        # Submission only queues; native state is rechecked by its sole worker.
        import secrets
        ticket=secrets.token_hex(6)
        with self.lock:
            if self.closed.is_set(): raise ValueError('本次对局已关闭，请重新启动')
            if not self.status.get('ready'): raise ValueError('原生对局还在准备')
            self.status['jobs'][ticket]={'command':command,'status':'queued'}
        self.queue.put((ticket,command,data)); return ticket

    def _accept(self):
        started=time.monotonic()
        sha=self.anchors.accept(self.engine,self.state); self.semantic_sha=sha
        observation=build_observation(self.state,self.observation['raw'] if self.observation else None)
        actors={owner:self.engine.observe_visible(owner) for owner in (0,1)}
        events=[deepcopy(self.engine._plays[key]) for key in sorted(self.engine._plays)]
        battlefield=build_battlefield_observation(self.state,actors[0],events,self.engine.export_journal(),self.battlefield)
        append_json(self.folder/'observations.jsonl',observation)
        append_json(self.folder/'battlefield_observations.jsonl',battlefield)
        with self.lock:
            self.observation=observation; self.actors=actors; self.events=events; self.battlefield=battlefield
            self.policy_info.update(owner=0,opponent='human_owner1',spell_first_deck=False)
            self.status.update(ready=True,tick=self.state['tick'],terminal=self.state['finalized'],
                               result=self.state['result'],crowns=self.state['crowns'],playing=self.playing,
                               actual_native_events=len(events),frame_count=self.frame_index,
                               decision_count=len(self.decisions),
                               truncated=bool(not self.state['finalized'] and self.state['tick']>=self.max_tick))
        if self.latest_frame is None:
            self._snapshot()
            from .platform_stream import NativeFrameStream
            self.stream=NativeFrameStream(self.bridge.ssh,output=self.folder/'stream')
            self.stream.start()
        elapsed=time.monotonic()-started
        append_json(self.folder/'timings.jsonl',{'kind':'state_accept','tick':self.state['tick'],'wall_seconds':elapsed})

    def _snapshot(self):
        started=time.monotonic(); self._capture_frame(self.semantic_sha)
        with self.lock: self.status['frame_count']=self.frame_index
        append_json(self.folder/'timings.jsonl',{'kind':'evidence_png','tick':self.state['tick'],
                                               'wall_seconds':time.monotonic()-started})

    def _decide(self):
        actor=self.engine.observe_visible(0); tick=actor['tick']
        offered=deepcopy({'battlefield':self.battlefield,'player':actor,'oracle':self.observation}[self.information_mode])
        started=time.monotonic(); proposed=self.custom(offered)
        chosen=research_action(proposed,actor)
        reason=str(proposed.get('reason','该策略未提供说明；查看 decide() 的实际规则'))[:500]
        row={'decision_index':self.decision_index,'tick':tick,'policy_information_mode':self.information_mode,
             'policy_input':offered,'policy_reason':reason,'decision_wall_seconds':time.monotonic()-started,
             'actor_mask':{'full_legality_verified':False,'scope':'Research slot, budget and bounds only'},
             'model_action':chosen,'opponent_action':{'kind':'wait'},'registrations':[]}
        if chosen['kind']=='play_card':
            receipt=self.engine.schedule_card(0,chosen['card'],chosen['x'],chosen['y'],tick+1,
                                               request_id=f'platform:model:{self.decision_index}')
            row['registrations'].append({'controller':'model','registration':receipt})
        append_json(self.folder/'decisions.jsonl',row)
        with self.lock: self.decisions.append(row); self.status['decision_count']=len(self.decisions)
        self.decision_index+=1; self.next_decision=tick+20

    def _human_play(self,data):
        if self.state['finalized'] or self.state['tick']>=self.max_tick: raise ValueError('本局已结束')
        if type(data.get('generation')) is not int or data['generation']!=self.state['generation']:
            raise ValueError('对局身份已变化，请刷新后重新选牌')
        actor=self.engine.observe_visible(1)
        cards=[card for card in actor['hand'] if card['slot']==data.get('slot')]
        if len(cards)!=1 or type(data.get('card')) is not int or cards[0]['card']!=data['card']:
            raise ValueError('当前手牌已变化，请重新选牌')
        if self.human_actions and self.human_actions[-1]['registration']['tick']>self.state['tick']:
            raise ValueError('上一张牌还在等待执行，请先让时间推进')
        x,y=placement(data)
        if cards[0]['card']<28000000 and y<16000:
            raise ValueError('这个演示的普通部队请放在下半场；法术可以选上半场')
        chosen=research_action({'kind':'play_card','slot':data.get('slot'),'x':x,'y':y},actor)
        before=self.state['tick']; index=len(self.human_actions)
        receipt=self.engine.schedule_card(1,chosen['card'],x,y,before+1,request_id=f'platform:human:{index}')
        row={'human_action_index':index,'tick':before,'actor_before':actor,'action':chosen,
             'registration':receipt,'placement_source':{key:data[key] for key in ('u','v','x','y') if key in data}}
        append_json(self.folder/'human_actions.jsonl',row)
        with self.lock: self.human_actions.append(row)

    def _job(self,ticket,command,data):
        if command=='pause':
            before=self.state['tick']; self.playing=False; self._feedback(); self._snapshot()
            answer={'command':command,'status':'done','before_tick':before,'after_tick':self.state['tick']}
            append_json(self.folder/'controls.jsonl',{'ticket':ticket,**answer})
            with self.lock: self.status['jobs'][ticket]=answer; self.status['playing']=False
            return
        if command not in ('human_turn','human_play','human_wait'):
            super()._job(ticket,command,data)
            return
        before=self.state['tick']
        if command!='human_play': self.playing=False
        if command in ('human_play','human_turn'): self._human_play(data)
        if command=='human_turn':
            # Consume the human request before offering the new state to AI.
            self.state=self.engine.advance_exact_ticks(1); self._accept()
            if not self.state['finalized'] and self.state['tick']<self.max_tick:
                self._decide(); self._advance(19)
        elif command=='human_wait': self._advance(20)
        if command!='human_play': self._feedback(); self._snapshot(); self._report()
        answer={'command':command,'status':'done','before_tick':before,'after_tick':self.state['tick']}
        append_json(self.folder/'controls.jsonl',{'ticket':ticket,**answer})
        with self.lock: self.status['jobs'][ticket]=answer; self.status['playing']=self.playing

    def _stop_recording(self):
        super()._stop_recording()
        if self.stream: self.stream.stop()

    def _report(self):
        if self.state and (not self.status.get('last_frame') or self.status['last_frame']['tick']!=self.state['tick']):
            self._snapshot()
        report=super()._report()
        report.update(human_actions=len(self.human_actions),human_owner=1,ai_owner=0,
                      stream=self.stream.status() if self.stream else None,
                      frame_sampling='Synchronous PNG at initialization, pause, teaching turn, report and end; asynchronous live JPEG is display only',
                      strategy='Replaceable Python decide(observation); default is explained rules, not an RL-trained model')
        write_json(self.folder/'report.json',report); return report
