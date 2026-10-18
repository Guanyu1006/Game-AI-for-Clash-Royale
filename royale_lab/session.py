"""A single real backend session shared by browser, agents and API clients."""
import json
import time
import uuid
from .contracts import Action
from .runtime import target,local

class Session:
    def __init__(self,seed=0,decks=None,controllers=None,max_ticks=3000,backend=None):
        from .backend import NativeBackend
        from .policies import make_policy
        self.id=uuid.uuid4().hex[:12]
        self.backend=backend or NativeBackend(seed=seed,decks=decks,max_ticks=max_ticks)
        self.controllers=controllers or ['human','greedy']
        if len(self.controllers)!=2:raise ValueError('Two controllers required')
        self.policies=[None if name=='human' else make_policy(name,self.backend.catalog,seed+i)
                       for i,name in enumerate(self.controllers)]
        self.decisions=[None,None]
        self.rejections=0
        self.log=[]
        self.snapshots={}
        self.decision_interval=10
        self.last_policy_tick=-1

    def advance(self,ticks=1):
        if not isinstance(ticks,int) or not 1 <= ticks <= 5000:raise ValueError('ticks must be 1..5000')
        for _ in range(ticks):
            if self.backend.done:break
            tick=self.backend.tick
            if tick % self.decision_interval == 0 and tick != self.last_policy_tick:
                # Both observations are taken before either commits an action.
                observations=[self.backend.observe(p) for p in (0,1)]
                proposed=[]
                for p,policy in enumerate(self.policies):
                    if policy is None:continue
                    obs=observations[p]
                    decision=policy.decide(obs)
                    self.decisions[p]=decision.to_dict()
                    proposed.append((p,decision,obs))
                for p,decision,obs in proposed:
                    feedback=self.backend.apply(p,decision.action,command_id=f'agent-{tick}-{p}')
                    self.rejections+=feedback['status']=='rejected'
                    self.log.append({'tick':tick,'player':p,'observation':obs,
                                     'decision':decision.to_dict(),'feedback':feedback})
                self.last_policy_tick=tick
            self.backend.advance(1)
        return self.state()

    def act(self,player,action,command_id=None):
        if player not in (0,1):raise ValueError('player must be 0 or 1')
        action=Action(**action) if isinstance(action,dict) else action
        feedback=self.backend.apply(player,action,command_id=command_id)
        self.rejections+=feedback['status']=='rejected'
        self.log.append({'tick':self.backend.tick,'player':player,'action':action.to_dict(),'feedback':feedback})
        return {'feedback':feedback,'state':self.state()}

    def state(self):
        from .memory import Belief
        viewer_belief=Belief().update(self.backend.observe(0),self.backend.catalog).to_dict()
        return {'session':self.id,'engine':self.backend.version,'state':self.backend.oracle(),
                'controllers':self.controllers,'decisions':self.decisions,'rejections':self.rejections,
                'snapshots':list(self.snapshots),'done':self.backend.done,
                'result':self.backend.result(),'debug_oracle':True,
                'viewer_belief':viewer_belief,
                'notice':'UI shows debug state; policy receives separate filtered observation'}

    def save(self,label=None):
        label=label or f'tick-{self.backend.tick}-{len(self.snapshots)}'
        if len(label)>80:raise ValueError('Snapshot label too long')
        self.snapshots[label]=self.backend.snapshot()
        path=target(f'outputs/sessions/{self.id}/{len(self.snapshots)}.json')
        path.write_text(json.dumps(self.backend.export_snapshot(),ensure_ascii=False,indent=2),encoding='utf-8')
        return {'label':label,'path':str(path),'tick':self.backend.tick}

    def restore(self,label):
        if label not in self.snapshots:raise ValueError('Unknown snapshot')
        self.backend.restore(self.snapshots[label])
        self.reset_policies()
        return self.state()

    def reset_policies(self):
        from .policies import make_policy
        self.policies=[None if name=='human' else make_policy(name,self.backend.catalog,i)
                       for i,name in enumerate(self.controllers)]
        self.last_policy_tick=-1;self.decisions=[None,None]

    def export(self,path=None):
        path=target(path or f'outputs/sessions/{self.id}/replay.json')
        path.write_text(json.dumps({'version':self.backend.version,'actions':self.backend.export_snapshot(),
                                  'decisions':self.log,'final':self.state()},ensure_ascii=False),encoding='utf-8')
        return str(path)
