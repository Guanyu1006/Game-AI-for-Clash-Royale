"""One-controller ordinary-eight-card environment over the preserved native backend."""
from copy import deepcopy
import math
import time
import uuid
from .actions import DECK,STEP_TICKS,PublicTeacher,match,mask,validate
from .common import append,local,reference,write
from .observation import ObservationProjector,encode_observation
from .belief import PublicBelief,encode_belief

class NativePhase3Env:
    """Research proposal masks; final legality is assessed by actual consumption.

    This class never spawns a VM, retries a mutation, uses a future replay, or
    implements battle physics. Caller owns the process-wide native lease.
    """
    def __init__(self,output,owner=0,opponent='cycle',max_tick=7200,profile='fair',rendered=False):
        from royale_phase2.backend import NativeBattleBackend
        if owner not in (0,1) or profile not in ('fair','research_battlefield'):raise ValueError('Bad actor scope')
        if type(max_tick) is not int or not 91<=max_tick<=1000000:raise ValueError('Invalid native watchdog tick')
        self.root=local(output);self.folder=self.root;self.folder.mkdir(parents=True,exist_ok=False)
        self._episode_count=0
        self.owner=owner;self.opponent_kind=opponent;self.max_tick=max_tick;self.profile=profile
        self.backend=NativeBattleBackend(rendered=rendered,timeout=60,trace_path=self.folder/'wire.jsonl')
        self.previous_action=0;self.previous_feedback=[0.,0.,0.,0.];self.state=None
        self.projector=ObservationProjector(profile)
        connection=getattr(self.backend,'connection_configuration',None) or {}
        self.belief=PublicBelief(version_manifest=connection.get('version_manifest','data/phase2/version_manifest.json'))
        self.battlefield=None
        self.feedback=[];self.decisions=[];self.done=True
    def _observe(self):
        from royale_phase2.battlefield import build_battlefield_observation
        self.actor=self.backend.observe_visible(self.owner)
        journal=self.backend.export_journal()
        self.battlefield=build_battlefield_observation(self.state,self.actor,journal['actual_card_events'],journal,self.battlefield)
        obs=self.projector.project(self.battlefield);self.public_observation=obs
        result={'episode_id':self.episode_id,'native_owner':self.owner,'split':'deployment','tick':obs['tick'],
            'dt_ticks':round(obs['dt_seconds']*20) if obs['dt_seconds'] is not None else 0,
            'hud':encode_observation(obs,cards=DECK,include_entities=False,include_history=False),
            'observation':encode_observation(obs,cards=DECK,include_entities=True,include_history=False),
            'history':encode_observation(obs,cards=DECK,include_entities=True,include_history=True),
            'belief':encode_belief(self.belief.update(obs),cards=DECK),'feedback':self.previous_feedback,
            'previous_action':self.previous_action,'mask':mask(self.actor)}
        # Deliberately no training labels, next-state, rewards or opponent targets.
        return result
    def reset(self,seed=1):
        if type(seed) is not int or seed<0:raise ValueError('Invalid episode seed')
        # Keep the first episode at the historical artifact paths. A later reset
        # seals that episode and uses new files, even if the earlier run failed.
        if self.state is not None:
            from royale_phase2.backend import NativeStateInvalid
            try:self.save()
            except NativeStateInvalid as exc:
                # A failed mutation invalidates backend.result(). Keep the
                # partial journal and explicit failure before caller's reset.
                write(self.folder/'reset_recovery.json',{'status':'native_state_invalid',
                    'error':str(exc),'next_action':'caller_requested_explicit_reset'})
        self._episode_count+=1
        if self._episode_count>1:
            self.folder=self.root/f'episode-{self._episode_count:04d}'
            self.folder.mkdir(exist_ok=False)
        self.backend.trace_path=self.folder/'wire.jsonl'
        self.feedback=[];self.decisions=[];self.state=None;self.row=None;self.actor=None
        self.public_observation=None;self.done=True
        self.episode_id=str(uuid.uuid4());self.projector.reset();self.belief.reset();self.battlefield=None
        self.previous_action=0;self.previous_feedback=[0.,0.,0.,0.]
        self.opponent=PublicTeacher(self.opponent_kind,seed*2+1-self.owner)
        write(self.folder/'configuration.json',{'schema':'phase3-native-episode-config.v1',
            'episode_id':self.episode_id,'seed':seed,'owner':self.owner,'opponent':self.opponent_kind,
            'max_tick':self.max_tick,'control_ticks':STEP_TICKS,'profile':self.profile})
        initialization=match(seed);write(self.folder/'initialization.json',initialization)
        self.state=self.backend.reset(initialization)
        if self.state['finalized'] or self.state['tick']>=self.max_tick:
            raise RuntimeError('Native reset did not produce an active state before the watchdog tick')
        self.done=False
        append(self.folder/'states.jsonl',self.state);self.row=self._observe();return deepcopy(self.row)
    def step(self,proposed):
        if self.done:raise RuntimeError('Reset required after terminal/truncation')
        before=self.state['tick'];started=time.monotonic();rejection=None
        try:
            if not isinstance(proposed,dict):raise ValueError('Expected an action object')
            if 'action_index' in proposed:
                index=proposed['action_index']
                if type(index) is not int or not 0<=index<=4:raise ValueError('Invalid action index')
                if index==0:action={'kind':'wait'}
                else:
                    point=proposed.get('xy_native')
                    if not isinstance(point,(list,tuple)) or len(point)!=2:raise ValueError('Expected two native coordinates')
                    action={'kind':'play_card','slot':index-1,'x':point[0],'y':point[1]}
            else:action=proposed
            if action.get('kind') not in ('wait','play_card'):raise ValueError('Unsupported action kind')
            action=validate(self.actor,action)
        except (ValueError,TypeError,KeyError) as exc:rejection=str(exc);action={'kind':'wait'}
        opponent=self.opponent.decide(self.backend.observe_visible(1-self.owner));registrations=[]
        for controller,owner,choice in (('model',self.owner,action),('opponent',1-self.owner,opponent)):
            if choice['kind']=='play_card':
                receipt=self.backend.schedule_card(owner,choice['card'],choice['x'],choice['y'],before+1,
                    request_id=f'{self.episode_id}:{controller}:{before}')
                registrations.append({'controller':controller,'registration':receipt})
        row={'before_tick':before,'actor_input':self.row,'proposed':deepcopy(proposed),'action':action,
            'boundary_rejection':rejection,'opponent':opponent,'registrations':registrations}
        self.state=self.backend.advance_exact_ticks(1);append(self.folder/'states.jsonl',self.state)
        remaining=min(STEP_TICKS-1,self.max_tick-self.state['tick'])
        if remaining>0 and not self.state['finalized']:
            self.state=self.backend.advance_exact_ticks(remaining);append(self.folder/'states.jsonl',self.state)
        feedback=self.backend.execution_feedback([r['registration']['request_id'] for r in registrations])
        self.feedback.extend(feedback)
        own=[f for f in feedback if f['owner']==self.owner]
        confirmed=bool(own and own[0]['card_consumed']);self.previous_action=action['slot']+1 if confirmed else 0
        self.previous_feedback=[float(action['kind']=='play_card'),float(confirmed),float(rejection is not None),float(bool(own) and not confirmed)]
        terminated=self.state['finalized'];truncated=not terminated and self.state['tick']>=self.max_tick;self.done=terminated or truncated
        reward=0.
        if terminated and self.state['result'] in (0,1):reward=1. if self.state['result']==self.owner else -1.
        dt=(self.state['tick']-before)/20
        row.update(after_tick=self.state['tick'],actual_dt_seconds=dt,feedback=feedback,reward=reward,
            terminated=terminated,truncated=truncated,discount=math.exp(math.log(.99)*dt),wall_seconds=time.monotonic()-started)
        append(self.folder/'decisions.jsonl',row);self.decisions.append(row)
        self.row=self._observe()
        if self.done:self.save()
        return deepcopy(self.row),reward,terminated,truncated,row
    def save(self):
        write(self.folder/'journal.json',self.backend.export_journal());write(self.folder/'execution_feedback.json',self.feedback)
        write(self.folder/'native_result.json',self.backend.result())
    def close(self):
        if self.state:
            try:self.save()
            except Exception:pass
        self.backend.close()
        self.done=True
