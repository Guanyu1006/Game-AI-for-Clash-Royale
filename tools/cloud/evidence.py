"""Inventory portable derived data separately from original native evidence."""
from collections import Counter
from tools.cloud.workspace import ROOT, local, manifest, read, sha, write, output


def reference_status(reference):
    path=local(reference['path'])
    if not path.is_file():return {'path':reference['path'],'status':'missing'}
    actual=sha(path)
    return {'path':reference['path'],'status':'match' if actual==reference['sha256'] else 'hash_mismatch',
            'expected_sha256':reference['sha256'],'actual_sha256':actual}


def audit(destination=None):
    from royale_phase3.data_contract import load_dataset
    m=manifest();data,meta=load_dataset(m['phase3']['metadata'])
    counts={}
    for split in ('train','validation','test'):
        subset=[r for r in data if r['split']==split]
        counts[split]={'episodes':len({r['episode_id'] for r in subset}),'rows':len(subset),
            'saved_play_labels':sum(r['action_index']>0 for r in subset)}
    source_status=reference_status(meta['source'])
    source=read(meta['source']['path']) if source_status['status']!='missing' else {}
    references=[r for game in source.get('results',[]) for r in game.get('artifacts',[])]
    statuses=[reference_status(r) for r in references]
    sources=[reference_status(r) for r in meta.get('feature_sources',[])]
    report={'schema':'cloud-derived-data-audit.v1','saved_cache_usable':True,
        'metadata':{'path':m['phase3']['metadata'],'sha256':sha(local(m['phase3']['metadata']))},
        'rows':meta['rows'],'counts':counts,'observation_profile':meta['observation_profile'],
        'source_audit_status':source_status,
        'non_tower_entities_in_fair_input':False,'original_artifacts':dict(Counter(r['status'] for r in statuses)),
        'original_artifact_details':statuses,'feature_source_status':sources,
        'reexport_from_raw_verified':False,'fresh_native_consumption_verified':False,'native_rpc_calls':0,
        'meaning':'Saved derived vectors can be loaded; raw evidence availability and feature source versions are reported independently'}
    target=local(destination) if destination else output('data-check')/'report.json'
    if target.exists():raise FileExistsError('Use a new data audit output')
    write(target,report);report['report_path']=str(target.relative_to(ROOT));return report
