"""Bounded three-source conversion, audit and independent public-history task.

Run from project root with .venv-phase2-data/Scripts/python.exe -X utf8 -B.
No network, VM, RPC, Android, unsafe pickle or third-party training imports.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure, digest, local, now, reference, rows, sha, write
from royale_phase3.sources import (ROOT, EXTERNAL, COCHON_REV, TV_REV, WTY_REV,
    cochon_record, interpret_object_npy_xz, parse_cochon_raw, tv_record,
    validate_record, wty_records, sequence_fingerprint)


def jsonlines(path, values):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        for value in values:
            f.write(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n')


def audit_duplicates(records):
    groups = defaultdict(list)
    for row in records:
        groups[row['group_id']].append(row)
    fingerprints = defaultdict(list)
    shingle_groups = []
    for group, rs in sorted(groups.items()):
        # TV processed offsets are derived rows of one underlying replay.
        seen, cards = set(), []
        order = sorted(rs, key=lambda x: (x['time']['source_value'] or 0, x['source_index'] or 0))
        for r in order:
            card = r['action']['card']
            if not card or r['action']['kind'] not in ('card_play', 'visual_card_label'):
                continue
            identity = (r['time']['source_value'], card, r['player_view']['actor'])
            if identity in seen: continue
            seen.add(identity)
            cards.append(card.lower().replace('_', '-'))
        if len(cards) >= 8:
            fingerprints[digest(cards)].append(group)
        shingles = {tuple(cards[i:i+5]) for i in range(len(cards)-4)}
        if len(shingles) >= 5:
            shingle_groups.append((group, shingles))
    flagged, pairs = [], 0
    for i, (g1, a) in enumerate(shingle_groups):
        for g2, b in shingle_groups[i+1:]:
            if g1.split(':')[0] == g2.split(':')[0]: continue
            pairs += 1
            overlap = len(a & b) / len(a | b)
            if overlap >= 0.8:
                flagged.append({'groups': [g1, g2], 'five_card_shingle_jaccard': overlap,
                                'decision': 'manual_review_do_not_merge_or_split_independently'})
    return {'exact_card_sequence_duplicates': [v for v in fingerprints.values() if len(v) > 1],
        'near_duplicate_rule': 'normalized-card five-grams Jaccard >=0.8, >=5 distinct grams per replay',
        'cross_source_comparable_pairs': pairs, 'near_candidates': flagged,
        'shared_battle_ids_verified': 0, 'cross_source_join_performed': False,
        'limitations': ['no_images_downloaded_so_perceptual_image_matching_not_run',
            'wty_has_numeric_slot_and_class_labels_without_verified_card_map',
            'card_sequence_similarity_alone_cannot_prove_shared_match',
            'no_match_found_does_not_prove_corpora_are_disjoint']}


def convert(output, max_cochon=80, max_tv=256):
    folder = local(output)
    if folder.exists():
        manifest = folder / 'manifest.json'
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding='utf8'))
            for ref in saved['outputs']:
                if sha(ref['path']) != ref['sha256']: raise ValueError('converted artifact changed')
            return {'status': 'resumed_verified_complete', 'manifest': reference(manifest)}
        raise ValueError('partial output exists; preserve it and choose a new output version')
    folder.mkdir(parents=True)
    config = {'task': 'three_source_small_adapters_v1', 'max_cochon_matches': max_cochon,
              'max_tv_rows_per_subset': max_tv, 'started_at': now(),
              'native_compatible_sources': [], 'snapshot_archive_downloaded': False}
    write(folder / 'config.json', config)
    records, inputs, audits = [], [], {}

    # Legacy JSONL remains distinct from raw HTML and later snapshots.
    cochon_dir = EXTERNAL / 'raw/cochon' / COCHON_REV
    match_path = cochon_dir / 'cleaned-legacy/matches.jsonl'
    event_path = cochon_dir / 'cleaned-legacy/events.jsonl'
    inputs.extend([reference(match_path), reference(event_path), reference(cochon_dir/'README.md')])
    # Verify immutable HF tree identities rather than relying on filenames.
    legacy_tree=Path('data/phase3/external/discovery/cochon_cleaned-legacy_tree.json')
    tree_entries=json.loads(local(legacy_tree).read_text(encoding='utf8'))
    for entry in tree_entries:
        candidate=cochon_dir/entry['path']
        if not local(candidate).exists(): continue
        raw_bytes=local(candidate).read_bytes()
        if len(raw_bytes)!=entry['size']: raise ValueError('Cochon tree size mismatch')
        identity=entry.get('lfs',{}).get('oid')
        actual=hashlib.sha256(raw_bytes).hexdigest() if identity else hashlib.sha1(b'blob '+str(len(raw_bytes)).encode()+b'\0'+raw_bytes).hexdigest()
        if actual!=(identity or entry['oid']): raise ValueError('Cochon fixed tree identity mismatch')
    inputs.append(reference(legacy_tree))
    matches = {x['battle_id']: x for x in rows(match_path)}
    selected = sorted(matches)[:max_cochon]
    grouping = {f'cochon:{k}': 'cochon-content:'+matches[k].get('content_hash', k) for k in selected}
    write(folder / 'cochon_selection.json', {'method': 'lexicographic_battle_id_first_N_before_metric_computation',
          'battle_ids': selected, 'content_grouping': grouping, 'source_matches_sha256': sha(match_path)})
    selected_set, counts, bad = set(selected), Counter(), []
    event_ref = reference(event_path)
    all_legacy_events = 0
    for event in rows(event_path):
        all_legacy_events += 1
        if event.get('battle_id') not in selected_set: continue
        record = cochon_record(event, event_ref)
        errors = validate_record(record)
        if errors:
            bad.append({'source_index': event.get('sequence'), 'reasons': errors})
            continue
        counts[event.get('event_type')] += 1
        records.append(record)
    raw_path = cochon_dir / 'raw/00/000YYC2UJPVG.json'
    raw = json.loads(local(raw_path).read_text(encoding='utf8'))
    parsed = parse_cochon_raw(raw)
    raw_ref = reference(raw_path); inputs.append(raw_ref)
    marker_map = defaultdict(list)
    for m in parsed['markers']:
        marker_map[(m.get('data-c'), m.get('data-t'), {'t':'team','o':'opponent'}.get(m.get('data-s')))].append(m)
    raw_records = []
    raw_events = sorted(parsed['events'], key=lambda x: (int(x['data-t']), x.get('data-s','')))
    for index, e in enumerate(raw_events):
        side = {'blue': 'team', 'red': 'opponent'}.get(e.get('data-s'))
        candidates = marker_map[(e.get('data-card'), e.get('data-t'), side)]
        marker = candidates[0] if len(candidates) == 1 else {}
        def number(key):
            value=marker.get(key)
            if value is None: return None
            try:
                parsed_number=float(value)
                return parsed_number if math.isfinite(parsed_number) else value
            except (TypeError,ValueError):
                # Raw HTML really contains the string "None" for some cards.
                # Retain it as the source value; the position mask remains false.
                return value
        event = {'battle_id':'000YYC2UJPVG', 'sequence':index, 'ticks':int(e['data-t']),
            'side':side, 'event_type':'ability' if e.get('data-ability') == '1' else 'card_play',
            'card':e.get('data-card'), 'x':number('data-x'), 'y':number('data-y')}
        r = cochon_record(event, raw_ref, subset='raw_html_v1')
        r['quality']['reasons'].append('html_timeline_marker_unique_key_join' if len(candidates)==1 else 'ambiguous_or_missing_marker')
        raw_records.append(r)
    records.extend(raw_records)
    audits['cochon'] = {'revision': COCHON_REV, 'license': 'MIT dataset-card declaration; game/upstream terms apply',
        'available_legacy_matches':len(matches), 'available_legacy_events':all_legacy_events,
        'selected_legacy_matches':len(selected), 'selected_legacy_event_types':dict(counts),
        'excluded_schema':bad, 'auxiliary_excluded_event_types':[x for x in counts if x != 'card_play'],
        'raw_html_sample': {'schema':parsed['schema_kind'],'events':len(raw_records),'markers':len(parsed['markers'])},
        'new_snapshot':'manifest separately inspected when present; archive not downloaded or assumed same schema',
        'ghost_states_generated':0, 'tower_hp_labels_generated':0, 'accepted_current_native_training':0}
    for optional in ('cleaned-legacy/manifest.json', 'snapshots/data-2026-10-16.manifest.json'):
        p = cochon_dir / optional
        if local(p).exists(): inputs.append(reference(p))
    snapshot_path=cochon_dir/'snapshots/data-2026-10-16.manifest.json'
    if local(snapshot_path).exists():
        snapshot=json.loads(local(snapshot_path).read_text(encoding='utf8'))
        directory=Path('data/phase3/external/discovery/cochon_snapshots_tree.json')
        upstream=json.loads(local(directory).read_text(encoding='utf8'));inputs.append(reference(directory))
        entry=next(x for x in upstream if x['path'].endswith('.manifest.json'))
        if sha(snapshot_path)!=entry['lfs']['oid']: raise ValueError('snapshot manifest hash mismatch')
        audits['cochon']['new_snapshot']={
            'scope':'inventory_only_not_dataset_or_schema_conversion', 'manifest':reference(snapshot_path),
            'created_utc':snapshot['created_utc'],'producer_git_revision':snapshot['git_revision'],
            'artifact_commit':snapshot['artifact_commit'], 'listed_files':len(snapshot['files']),
            'listed_uncompressed_bytes':sum(x['bytes'] for x in snapshot['files']),
            'archive':snapshot['archive'],'archive_downloaded':False,
            'top_level_path_counts':dict(Counter('/'.join(x['path'].split('/')[:2]) for x in snapshot['files'])),
            'board_oracle':'annotation queue listed; no human verification inferred',
            'tower_hp_runs':'weak visual labels listed; no truth/accuracy inferred',
            'ghost':'not_downloaded_or_validated_not_native_truth',
            'excluded_by_producer':snapshot['excluded']}

    # Read selected Parquet columns only. Never infer seconds from frame IDs.
    tv_dir = EXTERNAL / 'raw/tv' / TV_REV / 'range_samples'
    tv_manifest = json.loads(local(tv_dir/'range_manifest_v2.json').read_text(encoding='utf8'))
    inputs.extend([reference(tv_dir/'range_manifest_v2.json'),reference(EXTERNAL/'raw/tv'/TV_REV/'README.md')])
    tv_audit = []
    for descriptor in tv_manifest:
        if descriptor['status'] != 'converted_selected_columns':
            tv_audit.append({'subset':descriptor['tag'],'status':'excluded_missing_metadata'}); continue
        p = descriptor['selected_path']; inputs.append(reference(p))
        sample = list(rows(p)); chosen = sample[:max_tv]
        ref = reference(p)
        for item in chosen: records.append(tv_record(item, ref, descriptor['tag']))
        tv_audit.append({'subset':descriptor['tag'],'available_rows':len(sample),'converted_sample_rows':len(chosen),
            'replay_groups_available':len({x.get('replay','raw-single') for x in sample}),
            'missing_or_sentinel_xy_rows':sum(x.get('x',-1)<0 or x.get('y',-1)<0 for x in sample),
            'offset_distribution':dict(Counter(str(x.get('offset')) for x in sample)),
            'label_in_observed_hand_by_offset':{offset:{'total':sum(str(x.get('offset'))==offset for x in sample),
                'card_in_hand':sum(str(x.get('offset'))==offset and x.get('card') in (x.get('hand') or []) for x in sample)}
                for offset in sorted({str(x.get('offset')) for x in sample})},
            'metadata_range_bytes':descriptor['requested_bytes'], 'source_parquet_bytes':descriptor['source_bytes'],
            'image_bytes_decoded':0, 'coordinate_to_native_conversion':'not_proved_excluded',
            'time_to_engine_conversion':'not_proved_excluded'})
        for receipt in descriptor['receipts']:
            if sha(receipt['local']) != receipt['sha256']: raise ValueError('TV range hash mismatch')
    audits['tv'] = {'revision':TV_REV,'license':'MIT dataset-card declaration', 'subsets':tv_audit,
        'total_metadata_range_bytes':sum(x['requested_bytes'] for x in tv_manifest),
        'budget_bytes':64_000_000,'exact_timestamps_known':False,'accepted_current_native_training':0}

    # Historical object NPY -> inert interpreter -> whitelisted intermediate JSON.
    wty_dir = ROOT/'references/library/assets/wty-yy__Clash-Royale-Replay-Dataset'
    tree = Path('references/projects/wty-yy__Clash-Royale-Replay-Dataset/upstream_tree.json')
    tree_data = json.loads(local(tree).read_text(encoding='utf8'))
    if tree_data['sha']!=WTY_REV or tree_data.get('truncated'): raise ValueError('wrong/incomplete pinned wty tree')
    wty_entries={x['path']:x for x in tree_data['tree']}
    wty_audit = []
    for path in sorted(wty_dir.rglob('*.npy.xz')):
        original_ref = reference(path); inputs.append(original_ref)
        relative=path.relative_to(wty_dir).as_posix(); data_bytes=path.read_bytes()
        blob=hashlib.sha1(b'blob '+str(len(data_bytes)).encode()+b'\0'+data_bytes).hexdigest()
        if blob!=wty_entries[relative]['sha']: raise ValueError('wty fixed Git blob mismatch')
        raw_copy=local(EXTERNAL/'raw/wty'/WTY_REV/relative);raw_copy.parent.mkdir(parents=True,exist_ok=True)
        if not raw_copy.exists():
            with raw_copy.open('xb') as f:f.write(data_bytes)
        if raw_copy.read_bytes()!=data_bytes: raise ValueError('immutable wty raw copy differs')
        ref = reference(raw_copy); inputs.append(ref)
        data, safe = interpret_object_npy_xz(raw_copy)
        converted = list(wty_records(data, ref, path.stem.removesuffix('.npy')))
        records.extend(converted)
        times = [r['time']['seconds'] for r in converted]
        acts = [r for r in converted if r['action']['kind']=='play_visual']
        wty_audit.append({'file':ref, 'upstream_git_blob_sha1':blob,'source_copy':original_ref,
            'safe_interpreter':safe,'rows':len(converted),
            'play_visual_rows':len(acts),'wait_visual_rows':len(converted)-len(acts),
            'time_min_max':[min(times),max(times)], 'ocr_time_delta_histogram':dict(Counter(str(r['time']['dt_seconds']) for r in converted)),
            'action_slot_distribution':dict(Counter(str(r['action']['slot']) for r in converted)),
            'last_action_source_index':acts[-1]['source_index'] if acts else None,
            'last_saved_source_index':len(converted)-1,'true_terminated_labels':0,
            'reward_nonzero_rows':sum(r['reward']['value'] != 0 for r in converted)})
    inputs.append(reference(tree))
    tree_paths = [x.get('path','') for x in (tree_data.get('tree',[]) if isinstance(tree_data,dict) else tree_data)]
    licenses = [x for x in tree_paths if Path(x).name.lower().startswith(('license','copying'))]
    audits['wty'] = {'revision':WTY_REV, 'license':'unknown: fixed upstream tree contains no LICENSE',
        'permission_for_redistribution_training':'not_established_do_not_expand', 'files':wty_audit,
        'fixed_tree_license_paths':licenses, 'fixed_tree_paths_checked':len(tree_paths),
        'accepted_current_native_training':0, 'rtg_actor_fields':0,
        'upstream_code_audit': [reference(p) for p in (
          'third_party/audit_candidates/KataCR/katacr/policy/replay_data/offline_data_builder.py',
          'third_party/audit_candidates/KataCR/katacr/policy/offline/dataset.py',
          'third_party/audit_candidates/KataCR/katacr/policy/perceptron/reward_builder.py')],
        'alignment':'KataCR builder moves actions backward by ceil(offset/save_freq), removes offset; exact per-row shift lost',
        'reward':'vision HP change normalized + tower/king destruction + king activation/elixir shaping; not official environment reward',
        'rtg':'upstream computes future return-to-go; excluded from deployment actor',
        'terminal':'upstream clips last action and calls it terminal; converter retains full file and leaves terminated/truncated unknown'}

    failures = [{'index':i,'errors':validate_record(x)} for i,x in enumerate(records) if validate_record(x)]
    if failures: raise ValueError(f'schema validation errors: {failures[:5]}')
    by_source = defaultdict(list)
    for r in records: by_source[r['source']].append(r)
    outputs = []
    for name, rs in sorted(by_source.items()):
        path = folder/f'{name}.records.jsonl'; jsonlines(path, rs); outputs.append(reference(path))
        audits[name]['converted_rows'] = len(rs)
        audits[name]['converted_groups'] = len({r['group_id'] for r in rs})
    write(folder/'schema_audit.json', {'schema':'phase3.external_audit.v1','sources':audits,
        'validated_rows':len(records),'schema_errors':failures,
        'all_sources_excluded_from_current_native_dynamics':True})
    write(folder/'duplicate_audit.json', audit_duplicates(records))
    source_lock={'schema':'phase3.source_lock.v1','sources':{
        'cochon':{'url':'https://huggingface.co/datasets/Cochon123/clash-royale-replays',
            'revision':COCHON_REV,'license':'MIT dataset-card declaration','raw_immutable':True,
            'subsets_audited':['raw_html_v1','cleaned-legacy'],'full_snapshot_archive_downloaded':False},
        'tv':{'url':'https://huggingface.co/datasets/chrisrca/clash-royale-tv-replays',
            'revision':TV_REV,'license':'MIT dataset-card declaration','raw_immutable':True,
            'columns':'explicit non-image allowlist','max_http_range_bytes':8_000_000,
            'total_metadata_range_budget':64_000_000,'raw_full_parquet_downloaded':False},
        'wty':{'url':'https://github.com/wty-yy/Clash-Royale-Replay-Dataset',
            'revision':WTY_REV,'license':'unknown_no_license_in_fixed_tree','raw_immutable':True,
            'sample_files':len(wty_audit),'unsafe_pickle_execution':False}},
        'inputs':inputs,'sha256_over_inputs':digest(inputs),
        'budget':{'raw_downloaded_bytes_local':sum(p.stat().st_size for p in local(EXTERNAL/'raw').rglob('*') if p.is_file()),
                  'wty_tree_full_compressed_bytes':sum(x.get('size',0) for x in wty_entries.values() if x.get('type')=='blob'),
                  'unpacked_wty_small_samples_bytes':sum(x['safe_interpreter']['unpacked_bytes'] for x in wty_audit),
                  'tv_selected_metadata_range_bytes':audits['tv']['total_metadata_range_bytes'],
                  'new_full_downloads_permitted':False}}
    write(folder/'source_lock.json',source_lock)
    outputs.extend([reference(folder/'schema_audit.json'),reference(folder/'duplicate_audit.json'),
                    reference(folder/'cochon_selection.json'),reference(folder/'config.json'),reference(folder/'source_lock.json')])
    manifest = {'schema':'phase3.source_conversion_manifest.v1','created_at':now(),
        'code':[reference('royale_phase3/sources.py'),reference('tools/phase3_sources.py')],
        'inputs':inputs,'outputs':outputs,'input_fingerprint':digest(inputs),
        'converted_rows':len(records),'native_qualified_rows':0,'schema_errors':0}
    write(folder/'manifest.json',manifest)
    return {'status':'converted','manifest':reference(folder/'manifest.json'), 'rows':len(records)}


def next_card_examples(records):
    by_side = defaultdict(list)
    for row in records:
        if row['source_subset'] != 'cleaned-legacy' or row['action']['kind'] != 'card_play': continue
        by_side[(row['group_id'],row['player_view']['actor'])].append(row)
    examples = []
    for (group, side), events in sorted(by_side.items()):
        events.sort(key=lambda x:(x['time']['seconds'],x['source_index']))
        for prev, target in zip(events,events[1:]):
            examples.append({'group':group,'side':side, 'previous_public_card':prev['action']['card'],
                'previous_event_seconds':prev['time']['seconds'], 'target_next_card':target['action']['card'],
                'target_event_seconds':target['time']['seconds']})
    return examples


def experiment(dataset, output):
    folder = local(output)
    if folder.exists(): raise ValueError('immutable experiment output exists; choose new task ID')
    folder.mkdir(parents=True)
    path = local(dataset)/'cochon.records.jsonl'
    records = list(rows(path)); examples = next_card_examples(records)
    selection=json.loads((local(dataset)/'cochon_selection.json').read_text(encoding='utf8'))
    grouping=selection['content_grouping']
    # Both player sides, all temporal slices and any exact-content aliases share
    # a split. The model never receives these provenance grouping keys.
    groups = sorted(set(grouping[x['group']] for x in examples), key=lambda s:digest({'group':s,'seed':20261018}))
    split = max(1, int(len(groups)*0.8)); train_content=set(groups[:split]); val_content=set(groups[split:])
    train_groups={g for g,c in grouping.items() if c in train_content};val_groups={g for g,c in grouping.items() if c in val_content}
    if not train_groups or not val_groups: raise ValueError('need independent held-out matches')
    config = {'task':'public_next_card_auxiliary_v1','source':reference(path),
        'source_conversion':reference(local(dataset)/'manifest.json'),
        'train_groups':sorted(train_groups),'validation_groups':sorted(val_groups),'test_groups':[],
        'train_content_groups':sorted(train_content),'validation_content_groups':sorted(val_content),
        'split_seed':20261018,'smoothing_mass':5.0,'vocabulary':'train_only_plus_UNK',
        'baseline':'train unigram frequency with Dirichlet 0.1/class',
        'candidate':'previous same-side public card -> next-card counts, backoff to unigram; fixed prior mass 5',
        'actor_features':['previous_public_card'],
        'excluded_features':['deck_not_yet_revealed','future_cards','outcome','tower_ghost','native_state','source_path','group_id'],
        'metrics_declared':['validation_nll','validation_accuracy','validation_top5_accuracy','ece10'],
        'metric_direction':'lower NLL is better; no native win-rate inference',
        'not_main_bc_or_native_dynamics':True, 'created_at':now()}
    # Freeze complete grouping and hyperparameters before fit/evaluation.
    write(folder/'frozen_config.json',config)
    train = [x for x in examples if x['group'] in train_groups]
    val = [x for x in examples if x['group'] in val_groups]
    vocab = sorted({x['target_next_card'] for x in train} | {x['previous_public_card'] for x in train})+['<UNK>']
    vocabulary = set(vocab)
    def label(c): return c if c in vocabulary else '<UNK>'
    counts, conditional = Counter(), defaultdict(Counter)
    began = time.perf_counter()
    for x in train:
        y = label(x['target_next_card']); context=label(x['previous_public_card'])
        counts[y] += 1; conditional[context][y] += 1
    total = sum(counts.values())
    unigram = {k:(counts[k]+0.1)/(total+0.1*len(vocab)) for k in vocab}
    checkpoint = {'kind':'public-card conditional count model', 'vocabulary':vocab,
        'unigram_counts':dict(counts),'conditional_counts':{k:dict(v) for k,v in conditional.items()},
        'unigram_probability':unigram,'prior_mass':5.0,'config_sha256':sha(folder/'frozen_config.json')}
    write(folder/'checkpoint.json',checkpoint)
    # Deliberately reload the saved checkpoint for held-out evaluation.
    checkpoint = json.loads((folder/'checkpoint.json').read_text(encoding='utf8'))
    def evaluate(candidate):
        nll, correct, top5, bins = 0.,0,0,defaultdict(list)
        per_group = defaultdict(lambda:[0.,0])
        for x in val:
            y,context=label(x['target_next_card']),label(x['previous_public_card'])
            cond=checkpoint['conditional_counts'].get(context,{}) if candidate else {}
            denominator=sum(cond.values())+checkpoint['prior_mass']
            prob={k:(cond.get(k,0)+checkpoint['prior_mass']*checkpoint['unigram_probability'][k])/denominator for k in vocab}
            ranked=sorted(vocab,key=lambda k:(-prob[k],k)); pred=ranked[0]
            loss=-math.log(max(prob[y],1e-300)); nll+=loss; correct+=pred==y; top5+=y in ranked[:5]
            confidence=prob[pred]; bins[min(9,int(confidence*10))].append((confidence,pred==y))
            per_group[x['group']][0]+=loss; per_group[x['group']][1]+=1
        n=len(val)
        ece=sum(len(b)/n*abs(sum(x[0] for x in b)/len(b)-sum(x[1] for x in b)/len(b)) for b in bins.values())
        return {'n':n,'nll':nll/n,'accuracy':correct/n,'top5_accuracy':top5/n,'ece10':ece,
                'calibration_bins':{str(k):{'n':len(b),'mean_confidence':sum(x[0] for x in b)/len(b),
                    'accuracy':sum(x[1] for x in b)/len(b)} for k,b in bins.items()},
                'nll_per_group':{k:v[0]/v[1] for k,v in per_group.items()}}
    baseline=evaluate(False); candidate=evaluate(True)
    nll_deltas=[candidate['nll_per_group'][g]-baseline['nll_per_group'][g] for g in sorted(val_groups)]
    report={'schema':'phase3.external_auxiliary_experiment.v1','status':'ran',
        'config':reference(folder/'frozen_config.json'),'checkpoint':reference(folder/'checkpoint.json'),
        'source_version':COCHON_REV,'current_native_data_used':0,
        'split':{'train_matches':len(train_groups),'validation_matches':len(val_groups),
                 'train_examples':len(train),'validation_examples':len(val),'group_overlap':sorted(train_groups&val_groups)},
        'vocabulary_size':len(vocab),'unknown_validation_targets':sum(label(x['target_next_card'])=='<UNK>' for x in val),
        'class_coverage':{'train_target_classes':len(counts),
            'validation_target_classes':len({x['target_next_card'] for x in val}),
            'train_targets_per_class':dict(counts),'validation_targets_per_class':dict(Counter(x['target_next_card'] for x in val))},
        'fit_and_evaluate_seconds':time.perf_counter()-began,
        'baseline_unigram':baseline,'candidate_public_history_bigram':candidate,
        'nll_delta_candidate_minus_baseline':candidate['nll']-baseline['nll'],
        'validation_matches_better':sum(x<0 for x in nll_deltas),
        'validation_matches_worse':sum(x>0 for x in nll_deltas),
        'improved_declared_validation_nll':candidate['nll']<baseline['nll'],
        'native_strength_improvement':'not_tested_not_implied',
        'limitations':['single_frozen_80_match_legacy_subset','no_official_or_native_dynamics_targets',
            'no_test_used_or_claimed','frequency_model_only_no_rl_or_neural_training',
            'same-side_next_card_task_does_not_estimate_play_time_or_location','source_versions_incompatible_unknown']}
    jsonlines(folder/'validation_examples.jsonl',val)
    write(folder/'report.json',report)
    return {'status':'ran','report':reference(folder/'report.json'),
            'baseline_nll':baseline['nll'],'candidate_nll':candidate['nll']}


def main():
    configure()
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    c=commands.add_parser('convert'); c.add_argument('--output',default='data/phase3/external/converted_v2')
    c.add_argument('--cochon-matches',type=int,default=80); c.add_argument('--tv-rows',type=int,default=256)
    e=commands.add_parser('experiment'); e.add_argument('--dataset',default='data/phase3/external/converted_v2')
    e.add_argument('--output',default='outputs/phase3/sources/aux_next_card_v1')
    args=parser.parse_args()
    if args.command=='convert':
        if not 2<=args.cochon_matches<=160 or not 1<=args.tv_rows<=512: raise ValueError('small sample limit')
        result=convert(args.output,args.cochon_matches,args.tv_rows)
    else: result=experiment(args.dataset,args.output)
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__': main()
