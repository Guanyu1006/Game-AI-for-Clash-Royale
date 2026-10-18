"""Public deployment history and conservative opponent resource intervals."""
from dataclasses import dataclass,field

def regeneration(start,end):
    return sum(max(0,min(end,hi)-max(start,lo))*rate/2.8
               for lo,hi,rate in ((0,120,1),(120,240,2),(240,float('inf'),3)))

@dataclass
class Belief:
    low: float=0
    high: float=10
    time: float|None=None
    seen: set[int]=field(default_factory=set)
    events: set[str]=field(default_factory=set)
    last_play: dict[int,int]=field(default_factory=dict)
    count: int=0
    contradictions: int=0

    def update(self,obs,catalog):
        if self.time is None:
            self.time=0 if obs.get('history_complete',False) or obs['time']==0 else obs['time']
            if self.time==0:self.low=self.high=obs.get('starting_elixir',5)
        if obs['time'] < self.time:
            raise ValueError('Policy memory must reset between games')
        for e in sorted(obs.get('events',[]),key=lambda e:e['time']):
            if e['team'] != 1:continue
            cid=e['card_id']
            if cid in catalog:self.seen.add(cid)
            key=str(e['id'])
            if key in self.events:continue
            self.events.add(key)
            if e.get('evidence') != 'confirmed':continue
            et=e['time']
            if et < self.time:
                self.low,self.high=0,10;self.contradictions+=1;continue
            r=regeneration(self.time,et)
            self.low,self.high=min(10,self.low+r),min(10,self.high+r)
            self.time=et
            cost=e.get('cost',catalog.get(cid,{}).get('cost',0))
            if cost > self.high+1e-5:
                self.low,self.high=0,10-cost;self.contradictions+=1
            else:self.low,self.high=max(0,self.low-cost),max(0,self.high-cost)
            if cid in catalog:self.count+=1;self.last_play[cid]=self.count
        r=regeneration(self.time,obs['time'])
        self.low,self.high=min(10,self.low+r),min(10,self.high+r)
        if obs.get('source')=='screen':self.low,self.high=0,10
        self.time=obs['time']
        return self

    def available(self,cid):return cid not in self.last_play or self.count-self.last_play[cid]>=4

    def to_dict(self):
        return {'elixir_interval':[round(self.low,2),round(self.high,2)],'seen_cards':sorted(self.seen),
                'cycle_blocked':[c for c in self.last_play if not self.available(c)],
                'confirmed_plays':self.count,'contradictions':self.contradictions,
                'note':'base four-card approximation; champion cycle needs separate modeling'}
