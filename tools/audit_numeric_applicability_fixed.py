"""Retest reviewed numeric-field exclusions against the repaired native binary."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--module-dir',default='.cache/native_numeric_stage')
    p.add_argument('--output',default='outputs/audit_candidates/numeric_config_review_fixed.json');args=p.parse_args()
    sys.path.insert(0,str(ROOT/args.module_dir))
    import clash_royale_env as n
    exported=n.export_numeric_stats(defaults=True)
    def cid(name):return next(k for k,v in exported['card_metadata'].items() if v['name'].lower()==name.lower())
    fingerprint=lambda:hashlib.sha256(json.dumps(n.export_numeric_stats()['cards'],sort_keys=True).encode()).hexdigest()
    invalid=[('The Log','spell_radius',49),('The Log','spell_applications',9),('The Log','spell_interval_ticks',1),
             ('The Log','spell_knockback',30),('The Log','spell_roll_knockback',-5),
             ('Barbarian Barrel','spell_spawn_ring_radius',20),('Rage','spell_knockback',30),('Clone','spell_knockback',30)]
    checks=[]
    for name,field,value in invalid:
        ident=cid(name);before=fingerprint();error=None
        try:n.apply_numeric_overrides({'cards':{ident:{'base':{field:value}}}})
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        checks.append({'card':name,'field':field,'value':value,'rejected':bool(error),'unchanged':before==fingerprint(),
                       'exported':field in exported['cards'][ident]['base'],'error':error})
    allowed=[]
    for name,field,value in [('The Log','spell_delay_ticks',3),('The Log','spell_roll_knockback',0),
                             ('Fireball','spell_radius',3),('Graveyard','spell_spawn_ring_radius',2)]:
        ident=cid(name);feedback=n.apply_numeric_overrides({'cards':{ident:{'base':{field:value}}}})
        active=n.export_numeric_stats()['cards'][ident]['base'][field]
        allowed.append({'card':name,'field':field,'value':value,'accepted':active==value,'feedback':feedback})
    header_bool=None
    try:n.apply_numeric_overrides({'cards':{},'schema_version':True});header_bool='accepted'
    except Exception as exc:header_bool=type(exc).__name__+': '+str(exc)
    report={'module_path':str(Path(n.__file__).relative_to(ROOT)),'module_sha256':hashlib.sha256(Path(n.__file__).read_bytes()).hexdigest(),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':checks,'allowed_controls':allowed,
            'all_applicability_rejections_passed':all(x['rejected'] and x['unchanged'] for x in checks),
            'all_allowed_controls_passed':all(x['accepted'] for x in allowed),'schema_version_boolean_result':header_bool,
            'scope':'Only repaired applicability/type API behavior; previous original binary combat trajectories are preserved separately.'}
    path=ROOT/args.output;path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    assert report['all_applicability_rejections_passed'] and report['all_allowed_controls_passed']


if __name__=='__main__':main()
