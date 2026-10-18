"""Evidence dimensions stay separate: parse, execution, repeatability and fidelity."""
from dataclasses import asdict,dataclass,field
import math
from .versioning import canonical_hash

STATUSES={'pass','fail','unknown','not_available','not_run'}

@dataclass
class EpisodeQuality:
    source_episode_id:str
    parse_valid:str='not_run'
    runtime_replayable:str='not_run'
    deterministic:str='not_run'
    outcome_consistent:str='not_run'
    trajectory_validated:str='not_available'
    reasons:list=field(default_factory=list)
    evidence:dict=field(default_factory=dict)
    def to_dict(self):
        row=asdict(self)
        for key in ('parse_valid','runtime_replayable','deterministic','outcome_consistent','trajectory_validated'):
            if row[key] not in STATUSES:raise ValueError(f'Unknown quality status: {key}')
        if row['runtime_replayable']=='pass' and not self.evidence.get('native_execution'):
            raise ValueError('Successful parsing is not native execution evidence')
        if row['trajectory_validated']=='pass' and not self.evidence.get('independent_reference'):
            raise ValueError('Independent trajectory reference required')
        return row

def compare_outcome(source,observed,tolerances=None):
    """Only compare actual source-provided fields; missing data are never zeros."""
    tolerances=tolerances or {};rows={}
    for name in ('winner','crowns','end_tick','tower_hp'):
        expected=source.get(name);actual=observed.get(name)
        if expected is None or actual is None:
            rows[name]={'status':'unknown','expected':expected,'actual':actual};continue
        tolerance=tolerances.get(name,0)
        if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or tolerance<0:
            raise ValueError('Tolerance must be a finite, nonnegative number')
        def equal(a,b):
            if isinstance(a,bool) or isinstance(b,bool):return type(a) is type(b) and a==b
            if isinstance(a,(int,float)) and isinstance(b,(int,float)):
                return math.isfinite(a) and math.isfinite(b) and abs(a-b)<=tolerance
            if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
            return type(a) is type(b) and a==b
        rows[name]={'status':'pass' if equal(expected,actual) else 'fail','expected':expected,'actual':actual,'tolerance':tolerance}
    available=[r['status'] for r in rows.values() if r['status']!='unknown']
    status='unknown' if not available else ('fail' if 'fail' in available else 'pass')
    return {'status':status,'fields':rows,'compared_fields':len(available),
            'scope':'Outcome fields only; trajectory fidelity is assessed separately.'}

def compare_sequences(expected,actual):
    """Sequences must already be explicit semantic states; no hidden field dropping."""
    for index,(a,b) in enumerate(zip(expected,actual)):
        if canonical_hash(a)!=canonical_hash(b):
            return {'status':'fail','first_divergence_index':index,'tick':a.get('tick'),
                    'expected_hash':canonical_hash(a),'actual_hash':canonical_hash(b)}
    if len(expected)!=len(actual):
        return {'status':'fail','first_divergence_index':min(len(expected),len(actual)),
                'reason':'sequence_length','expected_length':len(expected),'actual_length':len(actual)}
    if not expected:return {'status':'unknown','reason':'empty_sequences'}
    return {'status':'pass','state_points':len(expected),'sequence_sha256':canonical_hash(expected)}
