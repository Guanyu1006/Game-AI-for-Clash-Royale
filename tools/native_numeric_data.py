"""Export real native numeric factory inputs and a strict versioned JSON schema."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--module-dir',default='third_party/ClashRoyaleAi/python_ai')
    args=p.parse_args()
    sys.path.insert(0,str(ROOT/args.module_dir))
    import clash_royale_env as native
    data=native.export_numeric_stats(defaults=True)
    numeric=lambda key:{k:v for k,v in data['field_schema'][key].items() if k!='unit'}
    rows={}
    for key,row in data['cards'].items():
        phases={}
        for phase,values in row.items():
            phases[phase]=({'type':'null'} if values is None else
                {'type':'object','properties':{k:numeric(k) for k in values},
                 'required':list(values),'additionalProperties':False})
        if not data['card_metadata'][key]['numeric_overrides_supported']:
            phases={phase:{'const':values} for phase,values in row.items()}
        rows[key]={'type':'object','properties':phases,'required':['base','evolved'],'additionalProperties':False}
    schema={'$schema':'https://json-schema.org/draft/2020-12/schema',
        '$id':'urn:royale-lab:balance-numeric-v1','title':'Full native primary factory numeric configuration',
        'description':'A full 173-row file. The native API also accepts a sparse cards object; effect-object internals, child units, tower troops and special paths remain code.',
        'type':'object','properties':{
            'format':{'const':data['format']},'schema_version':{'const':1},
            'engine_base':{'const':data['engine_base']},'source_commit':{'const':data['source_commit']},
            'ticks_per_second':{'const':10},
            'cards':{'type':'object','properties':rows,'required':list(rows),'additionalProperties':False},
            'field_schema':{'type':'object'},'card_metadata':{'type':'object'},
            'limitations':{'type':'array','items':{'type':'string'}},
            'label':{'type':'string'},'notes':{'type':'string'}},
        'required':['format','schema_version','engine_base','source_commit','ticks_per_second','cards'],
        'additionalProperties':False}
    for name,payload in [('balance_v2.json',data),('balance_v2.schema.json',schema)]:
        path=ROOT/'data/platform'/name
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    engine=ROOT/'third_party/ClashRoyaleAi'
    stats=(engine/'include/core/CardStats.h').read_text(encoding='utf-8')
    exposed_cpp={'cost','hp','damage','attackRange','speed','attackCooldown','deployTicks','shieldHp',
        'splashRadius','sightRange','initialCooldownTicks','minAttackRange','buildingLifetimeTicks',
        'spellRadius','spellDelayTicks','spellRemainingHits','spellTickInterval','spellCrownTowerDamage',
        'spellKnockback','spellRollRange','spellRollWidth','spellRollSpeed','spellRollKnockback','spellSpawnRingRadius'}
    # Declarations only, anchored to a complete member line; method locals do
    # not have initializer syntax matching these struct-level declarations.
    numeric=[{'type':m.group(1),'name':m.group(2),'line':stats[:m.start()].count('\n')+1}
        for m in re.finditer(r'^    (int|float) ([A-Za-z_]\w*) = [^;]+;',stats,re.M)
        if m.group(2) not in exposed_cpp]
    effects=[]
    for path in sorted((engine/'include').rglob('*.h')):
        text=path.read_text(encoding='utf-8')
        match=re.search(r'class\s+(\w+)\s*:\s*public\s+(IOnHitEffect|IOnDamageTakenEffect|IDeathEffect|IPeriodicEffect|IAbilityEffect)\s*\{',text)
        if not match:continue
        # Actual private data before the first public method. This describes
        # explicitly excluded effect storage; it is not an inferred JSON API.
        prefix=text[match.end():].split('public:',1)[0]
        members=[{'type':m.group(1),'name':m.group(2)} for m in re.finditer(
            r'^    (int|float|CardStats|std::vector<[^>]+>)\s+(\w+)(?:\s*=\s*[^;]+)?;',prefix,re.M)]
        effects.append({'class':match.group(1),'interface':match.group(2),
            'source':str(path.relative_to(ROOT)).replace('\\','/'),'private_parameters':members,
            'json_editable':False})
    exclusions={'source_commit':data['source_commit'],
        'unsupported_CardStats_numeric_members':numeric,'effect_object_storage_exclusions':effects,
        'other_exclusions':['secondaryUnit and child CardStats embedded in effect objects',
            'TowerTroops numeric presets, building decay algorithm, hardcoded collision/projectile constants',
            'SpiritEmpressForms 165, Mirror 164 dynamic fee/level behavior',
            'evolutionCycleThreshold/evolutionUsesBeforeCycle, IDs/archetypes/flags/spawnOffsets'],
        'interpretation':'This source inventory names parameters that remain code. It does not claim every listed member is used by every card; member-less effects may still have constants inside methods.'}
    (ROOT/'data/platform/unsupported_numeric_parameters.json').write_text(json.dumps(exclusions,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cards':len(rows),'evolutions':sum(r['evolved'] is not None for r in data['cards'].values()),
                      'fields':len(data['field_schema']),'numeric_supported_rows':sum(r['numeric_overrides_supported'] for r in data['card_metadata'].values())}))

if __name__=='__main__':main()
