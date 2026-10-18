"""Evidence-gated native trajectories. Parsing source actions never creates samples.

The actor receives only visibility.project_actor output. Evidence, future source
events and outcome/quality metadata stay outside that object and its mask.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path

from .visibility import TowerAttestation, affordability_mask, project_actor

ROOT = Path(__file__).resolve().parents[1]
EPISODE_SCHEMA = 'royale-phase2-native-episode.v1'
ROW_SCHEMA = 'royale-phase2-trajectory.v1'
PROOF_SCHEMA = 'royale-phase2-action-subspace-proof.v1'
SPLIT_SCHEMA = 'royale-phase2-learning-split.v1'
SUBSPACE = 'wait_or_arena_spell.v1'
ALLOWED_SPELLS = (28000000, 28000001)
QUALITY_KEYS = ('parse_valid', 'runtime_replayable', 'deterministic', 'outcome_consistent', 'trajectory_validated')
ACTOR_DEPENDENCIES = frozenset({'own_current_hand', 'own_current_cost', 'own_elixir', 'command_ready', 'public_tick'})
_HASH_CACHE = {}
_JSONL_OFFSETS = {}


class TrajectoryError(ValueError):
    pass


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path):
    path = Path(path); before = path.stat()
    key = (str(path.resolve()), before.st_size, before.st_mtime_ns)
    if key in _HASH_CACHE:
        return _HASH_CACHE[key]
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            result.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise TrajectoryError('Evidence changed while hashing')
    value = result.hexdigest(); _HASH_CACHE[key] = value
    return value


def project_path(path):
    candidate = Path(path)
    candidate = (ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as error:
        raise TrajectoryError('All inputs and outputs must remain inside the project') from error
    return candidate


def read_json(path):
    def invalid(value):
        raise TrajectoryError(f'Non-finite JSON value: {value}')
    return json.loads(project_path(path).read_text(encoding='utf-8-sig'), parse_constant=invalid)


def write_json(path, value):
    from .dataset import write_json as atomic_json
    path = project_path(path); path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)


def strict_int(value, name, low=0, high=2**63-1):
    if type(value) is not int or not low <= value <= high:
        raise TrajectoryError(f'Invalid {name}')
    return value


def strict_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TrajectoryError(f'Invalid finite numeric {name}')
    return value


def sha_value(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise TrajectoryError(f'Invalid SHA256: {name}')
    return value


def verify_reference(reference):
    if not isinstance(reference, dict):
        raise TrajectoryError('Missing evidence reference')
    path = project_path(reference['path'])
    expected = sha_value(reference['sha256'], 'evidence')
    if not path.is_file() or file_sha(path) != expected:
        raise TrajectoryError(f'Evidence file missing or hash mismatch: {reference["path"]}')
    result = {'path': path.relative_to(ROOT).as_posix(), 'sha256': expected}
    for key in ('json_pointer', 'jsonl_line'):
        if key in reference: result[key] = reference[key]
    return result


def verify_json_value(reference, expected):
    reference = verify_reference(reference)
    path = project_path(reference['path'])
    if 'jsonl_line' in reference:
        index = strict_int(reference['jsonl_line'], 'JSONL evidence line')
        stat = path.stat(); key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in _JSONL_OFFSETS:
            offsets = []
            with path.open('rb') as handle:
                while True:
                    position = handle.tell(); line = handle.readline()
                    if not line: break
                    offsets.append(position)
            _JSONL_OFFSETS[key] = offsets
        offsets = _JSONL_OFFSETS[key]
        if index >= len(offsets): raise TrajectoryError('JSONL evidence line missing')
        with path.open('rb') as handle:
            handle.seek(offsets[index]); value = json.loads(handle.readline().decode('utf-8-sig'))
    else:
        value = read_json(path)
    pointer = reference.get('json_pointer', '')
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise TrajectoryError('Invalid evidence JSON pointer')
    if pointer:
        for token in pointer[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            value = value[int(token)] if isinstance(value, list) else value[token]
    if canonical_bytes(value) != canonical_bytes(expected):
        raise TrajectoryError('Native pre-action capture does not equal its hashed evidence value')
    return reference


def read_jsonl(path):
    path = project_path(path)
    rows = []
    with path.open(encoding='utf-8-sig') as handle:
        for line in handle:
            if not line.strip(): raise TrajectoryError('Blank JSONL line would change evidence row identity')
            value = json.loads(line)
            canonical_bytes(value)
            rows.append(value)
    return rows


def _repeatability_status(paths, source_id, version):
    for path in paths:
        supplement = read_json(path)
        if supplement.get('schema') != 'royale-phase2-repeatability.v1':
            raise TrajectoryError('Unsupported repeatability supplement schema')
        for entry in supplement.get('episodes', []):
            if entry.get('source_episode_id') != source_id:
                continue
            if entry.get('version_fingerprint') != version:
                raise TrajectoryError('Repeatability supplement version mismatch')
            if entry.get('status') != 'pass':
                return entry.get('status', 'unknown'), None
            hashes = entry.get('sequence_hashes', [])
            if len(hashes) < 3 or len(set(hashes)) != 1:
                raise TrajectoryError('Repeatability needs three equal semantic sequence hashes')
            for value in hashes: sha_value(value, 'semantic sequence')
            runs = entry.get('runs', [])
            if len(runs) < 3:
                raise TrajectoryError('Repeatability requires three actual independent reset run artifacts')
            if (entry.get('independent_reset_generations') is not True
                    or entry.get('identical_native_initialization') is not True
                    or len({run.get('native_generation') for run in runs}) != len(runs)):
                raise TrajectoryError('Repeatability lacks actual distinct reset generations or identical initialization')
            seen_paths = set()
            for reference in runs:
                verified = verify_reference(reference)
                if verified['path'] in seen_paths:
                    raise TrajectoryError('Repeatability cannot repeat one run file')
                seen_paths.add(verified['path'])
                report = read_json(verified['path'])
                if (report.get('source_episode_id') != source_id or report.get('version_fingerprint') != version
                        or report.get('native_execution') is not True):
                    raise TrajectoryError('Repeatability run identity/native execution mismatch')
            # A locked episode.json is insufficient if its underlying state or
            # scheduler artifacts disappeared/changed after comparison. Re-read
            # every declared run file and recompute the actual exposed native
            # semantics; never trust report strings or generation claims alone.
            from .determinism import compare_runs
            recomputed = compare_runs([reference['path'] for reference in runs])
            if (recomputed['status'] != 'pass' or recomputed['sequence_hashes'] != hashes
                    or recomputed['independent_reset_generations'] is not True
                    or recomputed['identical_native_initialization'] is not True):
                raise TrajectoryError('Actual reset semantics do not reproduce the reported pass')
            for claimed, actual in zip(runs, recomputed['runs']):
                if any(claimed.get(key) != actual[key] for key in ('native_generation', 'state_points', 'sequence_sha256')):
                    raise TrajectoryError('Repeatability run claims disagree with actual state generations/semantic hashes')
            return 'pass', {'path': project_path(path).relative_to(ROOT).as_posix(), 'sha256': file_sha(project_path(path))}
    return 'not_run', None


def adapt_native_reconstruction(path, *, quality_supplements=(), expected_version=None):
    """Read root runner evidence, preserving actual line/transport identities.

    All source commands are retained for WAIT exclusion even when their label
    family is unsupported for BC. No source result is copied into an actor.
    """
    path = project_path(path); source = read_json(path)
    if source.get('schema_version') != 'royale-phase2-native-reconstruction.v1':
        raise TrajectoryError('Unsupported reconstruction schema')
    source_id = source['source_episode_id']; version = source['version_fingerprint']
    if expected_version is not None and version != expected_version:
        raise TrajectoryError('Reconstruction version differs from actual verified assets')
    if not source.get('completed') or source.get('native_execution') is not True:
        raise TrajectoryError('Incomplete native attempt has no exportable executed decision trace')
    files = source['files']
    for name in ('states.jsonl', 'decisions.jsonl', 'actions.json'):
        verify_reference(files[name])
    states = read_jsonl(files['states.jsonl']['path']); decisions = read_jsonl(files['decisions.jsonl']['path'])
    action_document = read_json(files['actions.json']['path'])
    action_rows = action_document['actions']; by_request = {}
    for index, action in enumerate(action_rows):
        if action['request_id'] in by_request: raise TrajectoryError('Duplicate request in native action evidence')
        by_request[action['request_id']] = (index, action)
    if not states: raise TrajectoryError('Native captures missing')
    if [row.get('capture_index') for row in states] != list(range(1, len(states)+1)):
        raise TrajectoryError('Native capture indices must be complete and one-based')
    if any(right['raw_capture']['tick'] <= left['raw_capture']['tick'] for left, right in zip(states, states[1:])):
        raise TrajectoryError('Native capture timeline is not strictly increasing')
    initial = states[0]['raw_capture']; terminal = states[-1]['raw_capture']
    quality = copy.deepcopy(source['quality'])
    repeat_status, repeat_evidence = _repeatability_status(quality_supplements, source_id, version)
    # A string in a first-run report alone cannot stand in for independent reset evidence.
    quality['deterministic'] = repeat_status
    root_ref = {'path': path.relative_to(ROOT).as_posix(), 'sha256': file_sha(path)}
    level_preserved = source.get('strict_source_levels') == 'pass'
    operational = ('pass' if expected_version == version and level_preserved and all(quality.get(key) == 'pass' for key in QUALITY_KEYS[:4])
                   and terminal.get('finalized') is True else 'fail' if any(quality.get(key) == 'fail' for key in QUALITY_KEYS[:4]) else 'unknown')
    compatibility = copy.deepcopy(source.get('version_compatibility', {}))
    compatibility.update(status=operational, source_build='unknown',
                         scope='operational_source_reconstruction; not proof of identical official build',
                         requirements={'actual_asset_fingerprint_matches': expected_version == version,
                                       'strict_source_levels': level_preserved, 'parse_runtime_determinism_outcome_pass': operational == 'pass',
                                       'native_natural_terminal': terminal.get('finalized') is True})
    normalized = {
        'schema': EPISODE_SCHEMA, 'episode_id': source_id+'@'+path.parent.name,
        'source_episode_id': source_id, 'leakage_group': source['leakage_group'],
        'version_fingerprint_sha256': version,
        'provenance': {'dataset_revision': source['dataset_revision'], 'raw_payload_sha256': source['source_payload_sha256'],
                       'converter_sha256': source['converter_sha256'], 'native_reconstruction_manifest': root_ref,
                       'source_episode': source['source_episode'],
                       'source_episode_sha256': source['source_episode_sha256'], 'hypothesis_sha256': source['hypothesis_sha256'],
                       'initialization_assumptions': copy.deepcopy(source.get('assumptions', {}))},
        'quality': quality, 'quality_evidence': {'native_execution': root_ref, 'outcome': root_ref, 'determinism': repeat_evidence},
        'configuration': {'source_levels_preserved': level_preserved, 'actual_version_compatible': operational,
                          'version_compatibility': compatibility},
        'reset_capture': initial,
        'source_journal_coverage': 'complete',
        'source_actions': [{'source_event_index': action['source_event_index'], 'native_owner': action['owner'],
                            'target_tick': action['tick'], 'kind': action['kind']} for action in source['source_actions']],
        'decisions': [], 'adapter_rejections': [],
    }
    source_episode = read_json(source['source_episode'])
    if file_sha(project_path(source['source_episode'])) != source['source_episode_sha256']:
        raise TrajectoryError('Source parsed episode changed since native reconstruction')
    source_events = {event['source_event_index']: event for event in source_episode['action_journal']}
    for decision in decisions:
        try:
            line = strict_int(decision['state_line'], 'one-based state evidence line', 1, len(states))
            if line >= len(states): raise TrajectoryError('No actual after-capture for this transition')
            before = states[line-1]['raw_capture']; after = states[line]['raw_capture']
            owner = strict_int(decision['owner'], 'native actor owner', 0, 1)
            if (decision['tick'], decision['capture_index']) != (before['tick'], line):
                raise TrajectoryError('Decision/native capture index mismatch')
            expected_actor = project_actor(before, owner, TowerAttestation.from_reset(initial))
            if expected_actor != decision['actor_observation'] or affordability_mask(expected_actor) != decision['hand_budget_mask']:
                raise TrajectoryError('Stored actor/mask differs from conservative raw pre-action capture projection')
            if decision.get('observation_before_label') is not True:
                raise TrajectoryError('Pre-label observation provenance missing')
            labels = decision['labels']
            if len(labels) > 1: raise TrajectoryError('Multiple same-owner same-boundary labels are ambiguous; do not split one observation into duplicated actions')
            raw_before = {**files['states.jsonl'], 'jsonl_line': line-1, 'json_pointer': '/raw_capture'}
            normalized_decision = {
                'decision_id': decision['decision_id'], 'native_owner': owner, 'before': before, 'before_evidence': raw_before,
                'timing': {'observation_phase': 'pre_submit', 'observation_sequence': decision['observation_sequence'],
                           'submit_sequence': decision['submit_sequence']},
                'command_ready': decision.get('command_ready'), 'source_event_index': None, 'source_time_seconds': None,
                'transition_end_tick': after['tick'], 'terminated': after['finalized'] is True,
                'truncated': after['finalized'] is not True and line == len(states)-1,
                'reward': 0.0, 'reward_version': 'native-sparse-final-result.v1',
            }
            # Only the transition actually reaching terminal receives this reward.
            raw_result = after.get('result')
            if after['finalized'] is True:
                if type(raw_result) is int and raw_result in (0, 1):
                    normalized_decision['reward'] = 1.0 if raw_result == owner else -1.0
                else:
                    raise TrajectoryError('Terminal raw result is neither independently mapped winner0 nor winner1; do not fabricate draw reward')
            if labels:
                label = labels[0]
                if label['kind'] != 'play_card':
                    raise TrajectoryError('Ability source label is unsupported, never a WAIT')
                index, result = by_request[label['request_id']]; feedback = result.get('feedback') or {}
                if feedback.get('card_consumed') is not True or feedback.get('consumption_status') != 'confirmed_native_card_event':
                    raise TrajectoryError('Native card consumption not uniquely confirmed')
                card_event = feedback['card_event']
                if len(card_event) != 5: raise TrajectoryError('Unsupported native CardPlay event shape')
                if label['target_tick'] != before['tick']+1 or result['tick'] != label['target_tick']:
                    raise TrajectoryError('Label is not the next-boundary native input')
                if (label['card_id'], label['x'], label['y'], owner) != (result['card_id'], result['x'], result['y'], result['owner']):
                    raise TrajectoryError('Requested action fields differ from executed input journal')
                event = source_events[label['source_event_index']]
                confirmation = _root_consumption_confirmation(feedback, before, after, owner, label)
                normalized_decision.update(
                    source_event_index=label['source_event_index'], source_time_seconds=event['source_seconds'],
                    label={'kind': 'play_card', 'slot': label['slot'], 'card': label['card_id'], 'x': label['x'], 'y': label['y']},
                    label_provenance={'kind': 'source_replay_command', 'source_request_id': label['request_id'],
                                      'source_event_sha256': event['source_event_sha256'], 'human_click_timestamp_known': False},
                    execution={'status': 'executed', 'request_id': label['request_id'], 'executed_tick': feedback['actual_execution_tick'],
                               'confirmation': confirmation,
                               'evidence': {**files['actions.json'], 'json_pointer': f'/actions/{index}/feedback'},
                               'native_feedback': feedback})
            else:
                if decision['sampling_origin'] != 'regular_grid':
                    raise TrajectoryError('Empty non-grid boundary is not a human WAIT label')
                interval = after['tick']-before['tick']
                normalized_decision.update(
                    label={'kind': 'wait'}, label_provenance={'kind': 'sampling_grid_absence', 'human_explicit_wait': False,
                        'interval_ticks': interval, 'sampling_grid_interval_ticks': decision['sampling_interval_ticks'],
                        'source_journal_coverage': 'complete'},
                    execution={'status': 'executed', 'request_id': 'sampled-wait:'+decision['decision_id'], 'executed_tick': after['tick'],
                               'confirmation': {'kind': 'native_advance'},
                               'evidence': {**files['states.jsonl'], 'jsonl_line': line, 'json_pointer': '/raw_capture'}})
            normalized['decisions'].append(normalized_decision)
        except (KeyError, ValueError, TypeError, OSError) as error:
            normalized['adapter_rejections'].append({'decision_id': decision.get('decision_id'), 'reason': str(error),
                                                       'kind': [label.get('kind') for label in decision.get('labels', [])]})
    normalized['adapter_input_decision_count'] = len(decisions)
    return normalized


def freeze_learning_split(frozen_manifest, output=None):
    """Assign all frozen groups before observing reconstruction outcomes.

    Development/formal status is retained independently. A learning test set
    does not retrospectively make a formal reconstruction tuning set independent.
    """
    source_path = project_path(frozen_manifest)
    source = read_json(source_path)
    groups = {}; ids = set(); entries = []
    for entry in source['entries']:
        episode_id = entry['source_episode_id']; group = entry['leakage_group']
        if episode_id in ids:
            raise TrajectoryError('Duplicate original source ID in frozen manifest')
        ids.add(episode_id)
        key = f'bc-split-v1|{source["dataset_revision"]}|{group}'
        bucket = int(hashlib.sha256(key.encode()).hexdigest()[:16], 16) % 100
        split = 'train' if bucket < 70 else 'validation' if bucket < 85 else 'test'
        if group in groups and groups[group] != split:
            raise TrajectoryError('Whole-source group split conflict')
        groups[group] = split
        entries.append({'source_episode_id': episode_id, 'leakage_group': group,
                        'reconstruction_split': entry['split'], 'learning_split': split,
                        'payload_sha256': entry['payload_sha256']})
    result = {'schema': SPLIT_SCHEMA, 'source_manifest': source_path.relative_to(ROOT).as_posix(),
              'source_manifest_sha256': file_sha(source_path), 'dataset_revision': source['dataset_revision'],
              'rule': 'SHA256(bc-split-v1|dataset_revision|leakage_group), first64bits modulo100: train<70, validation<85, else test',
              'uses_native_results': False, 'same_group_all_seats_fragments_hypotheses': True,
              'scope_note': 'Learning split is independent of development/formal reconstruction roles. No final outcomes are used to select initialization or tune the engine.',
              'counts': dict(Counter(entry['learning_split'] for entry in entries)), 'entries': entries}
    result['split_sha256'] = digest(result)
    if output is not None:
        path = project_path(output)
        if path.exists() and read_json(path) != result:
            raise TrajectoryError('Refusing to overwrite an existing different learning split')
        write_json(path, result)
    return result


def load_learning_split(path):
    result = read_json(path)
    if result.get('schema') != SPLIT_SCHEMA:
        raise TrajectoryError('Unsupported learning split')
    payload = {k: v for k, v in result.items() if k != 'split_sha256'}
    if result.get('split_sha256') != digest(payload):
        raise TrajectoryError('Learning split integrity mismatch')
    source = project_path(result['source_manifest'])
    if file_sha(source) != result['source_manifest_sha256']:
        raise TrajectoryError('Frozen source manifest changed')
    if result != freeze_learning_split(source):
        raise TrajectoryError('Learning split differs from the predeclared deterministic whole-group rule')
    ids = {}; groups = {}
    for entry in result['entries']:
        episode_id = entry['source_episode_id']; group = entry['leakage_group']; split = entry['learning_split']
        if episode_id in ids or split not in {'train', 'validation', 'test'}:
            raise TrajectoryError('Duplicate source or invalid split')
        if group in groups and groups[group] != split:
            raise TrajectoryError('Leakage group occurs in different splits')
        ids[episode_id] = entry; groups[group] = split
    return result, ids


def audit_subspace_proof(path, version_fingerprint):
    """Verify provenance and required checks, never manufacture native evidence.

    Signed/trusted external attestation is not implied: hashes establish which
    local artifacts were reviewed. Root/native tests establish their meaning.
    """
    if path is None:
        return {'status': 'unverified', 'reasons': ['missing_subspace_proof']}
    try:
        proof = read_json(path)
        if proof.get('schema') != PROOF_SCHEMA or proof.get('subspace') != SUBSPACE:
            raise TrajectoryError('Unsupported action subspace proof')
        if proof.get('version_fingerprint_sha256') != version_fingerprint:
            raise TrajectoryError('Action subspace proof version mismatch')
        catalog_sha = sha_value(proof.get('catalog_sha256'), 'catalog')
        catalog_reference = verify_reference(proof.get('catalog_reference'))
        if catalog_reference['sha256'] != catalog_sha:
            raise TrajectoryError('Catalog reference disagrees with the named catalog SHA256')
        if proof.get('allowed_card_ids') != list(ALLOWED_SPELLS):
            raise TrajectoryError('Unexpected spell catalog IDs')
        if proof.get('bounds') != {'x': [0, 17999], 'y': [0, 31999]}:
            raise TrajectoryError('Unexpected native coordinate bounds')
        dependencies = proof.get('actor_dependencies')
        if not isinstance(dependencies, list) or set(dependencies) != ACTOR_DEPENDENCIES or len(dependencies) != len(ACTOR_DEPENDENCIES):
            raise TrajectoryError('Mask dependencies must be exclusively the declared public/own inputs')
        references = [catalog_reference]
        privacy_owners = set()
        for check in ('native_positive', 'native_negative', 'enemy_private_perturbation', 'command_ready'):
            item = proof.get('checks', {}).get(check, {})
            if item.get('status') != 'pass' or not item.get('evidence'):
                raise TrajectoryError(f'Missing passing native subspace check: {check}')
            check_documents = []
            for reference in item['evidence']:
                verified = verify_reference(reference); references.append(verified)
                document = read_json(verified['path']); check_documents.append(document)
                if document.get('status') not in {None, 'pass'}:
                    raise TrajectoryError(f'Native subspace evidence itself reports a nonpass status: {check}')
                if check == 'enemy_private_perturbation':
                    for test in document.get('tests', []):
                        privacy_owners.add(strict_int(test['owner'], 'privacy fixture actor owner', 0, 1))
                        if test.get('actor_unchanged') is not True or test.get('mask_unchanged') is not True:
                            raise TrajectoryError('Private information altered actor or mask in the referenced evidence')
            if not any(document.get('status') == 'pass' for document in check_documents):
                raise TrajectoryError(f'No directly passing evidence document for {check}')
        if privacy_owners != {0, 1}:
            raise TrajectoryError('Subspace privacy evidence must cover both native actor owners')
        return {'status': 'verified_local_rule_evidence', 'subspace': SUBSPACE,
                'version_fingerprint_sha256': version_fingerprint,
                'proof': {'path': project_path(path).relative_to(ROOT).as_posix(), 'sha256': file_sha(project_path(path))},
                'evidence': references,
                'coverage_scope': 'Two named ordinary spells in the declared rectangular request domain; evidence-backed card-family rule, not exhaustive checking of every coordinate or all game cards.'}
    except (KeyError, ValueError, TypeError, OSError) as error:
        return {'status': 'unverified', 'reasons': [str(error)]}


def subspace_mask(actor, command_ready, proof_audit):
    """Full slot gate only for WAIT plus the proven all-arena spell family.

    No placement grid, effective forms, enemy state or arbitrary entities enter
    this mask. Continuous coordinates are constrained separately by bounds.
    """
    budget = affordability_mask(actor)
    values = [True, False, False, False, False]
    verified = proof_audit.get('status') == 'verified_local_rule_evidence'
    ready = command_ready is True
    for card in actor['hand']:
        values[card['slot']+1] = bool(verified and ready and actor['hand_complete']
                                     and card['card'] in ALLOWED_SPELLS and budget['values'][card['slot']+1])
    return {'schema': 'royale-phase2-subspace-mask.v1', 'subspace': SUBSPACE, 'values': values,
            'full_legality_verified': verified and type(command_ready) is bool,
            'command_ready': command_ready if type(command_ready) is bool else None,
            'coordinate_domain': {'x': [0, 17999], 'y': [0, 31999], 'units': 'native_requested_coordinate'},
            'scope': 'WAIT or current ordinary Fireball/Arrows slot; excludes other cards and all abilities',
            'version_fingerprint_sha256': proof_audit.get('version_fingerprint_sha256'),
            'proof': proof_audit.get('proof'),
            'proof_status': proof_audit['status']}


def _quality_reasons(episode):
    reasons = []
    quality = episode.get('quality', {})
    for key in QUALITY_KEYS:
        if quality.get(key) not in {'pass', 'fail', 'unknown', 'not_available', 'not_run'}:
            reasons.append(f'quality_{key}_invalid')
    for key in QUALITY_KEYS[:4]:
        if quality.get(key) != 'pass':
            reasons.append(f'quality_{key}_{quality.get(key, "missing")}')
    if quality.get('trajectory_validated') not in {'pass', 'not_available'}:
        reasons.append('trajectory_fidelity_unresolved_or_failed')
    config = episode.get('configuration', {})
    if config.get('source_levels_preserved') is not True:
        reasons.append('source_levels_not_preserved')
    if config.get('actual_version_compatible') != 'pass':
        reasons.append('actual_version_compatibility_unverified')
    for key in ('native_execution', 'determinism', 'outcome'):
        try:
            verify_reference(episode.get('quality_evidence', {}).get(key))
        except (KeyError, ValueError, TypeError, OSError):
            reasons.append(f'missing_or_invalid_{key}_evidence')
    if quality.get('trajectory_validated') == 'pass':
        try:
            verify_reference(episode.get('quality_evidence', {}).get('independent_reference'))
        except (KeyError, ValueError, TypeError, OSError):
            reasons.append('independent_trajectory_reference_missing')
    return reasons


def _check_execution(decision, actor, label):
    execution = decision['execution']; timing = decision['timing']
    if timing.get('observation_phase') != 'pre_submit':
        raise TrajectoryError('Observation is not certified pre-submit')
    observed = strict_int(timing.get('observation_sequence'), 'observation sequence')
    submitted = strict_int(timing.get('submit_sequence'), 'submit sequence')
    if not observed < submitted:
        raise TrajectoryError('Observation must precede submission in transport order')
    actual_tick = strict_int(execution.get('executed_tick'), 'execution tick')
    if actual_tick <= actor['tick']:
        raise TrajectoryError('Observation must precede actual execution tick')
    if execution.get('status') != 'executed' or not isinstance(execution.get('request_id'), str):
        raise TrajectoryError('Native executed feedback and request ID required')
    verify_reference(execution.get('evidence'))
    confirmation = execution.get('confirmation', {})
    if label['kind'] == 'play_card':
        if confirmation.get('kind') not in {'native_card_play', 'native_root_input_consumption'}:
            raise TrajectoryError('Card label requires actual native CardPlay confirmation')
        matched_card = confirmation.get('card') if confirmation['kind'] == 'native_card_play' else confirmation.get('input_root_card')
        if (confirmation.get('owner'), matched_card, confirmation.get('tick')) != (actor['native_owner'], label['card'], actual_tick):
            raise TrajectoryError('CardPlay confirmation does not match label owner/card/execution tick')
        if confirmation['kind'] == 'native_root_input_consumption' and confirmation.get('own_boundary_corroborated') is not True:
            raise TrajectoryError('Opaque/alias native event requires own-boundary root consumption corroboration')
    elif label['kind'] == 'wait':
        if confirmation.get('kind') != 'native_advance':
            raise TrajectoryError('WAIT requires actual native advance confirmation')
    else:
        raise TrajectoryError('Ability or unknown label cannot be converted into WAIT')
    return actual_tick


def _root_consumption_confirmation(feedback, before, after, owner, label):
    """Corroborate the input root card; never relabel an opaque raw event ID.

    Private own cycle/packed values are read only for execution diagnostics,
    outside actor features and legal masks, and remain in hashed debug evidence.
    """
    event = feedback['card_event']; root = label['card_id']
    if event[3] == root:
        return {'kind': 'native_card_play', 'owner': event[2], 'card': event[3], 'tick': event[1]}
    if feedback.get('own_boundary_consumption_evidence') is not True:
        raise TrajectoryError('Alias/opaque event has no own-boundary consumption evidence')
    snapshots = []
    for raw in (before, after):
        player = next(player for player in raw['players'] if player['owner'] == owner)
        snapshots.append({'tick': raw['tick'], 'hand': player['hand'], 'cycle': player['cycle'], 'elixir': player['elixir']})
    pre, post = snapshots
    if pre != feedback.get('before_registration') or post != feedback.get('after_execution'):
        raise TrajectoryError('Consumption snapshots disagree with actual before/after native captures')
    selected = [card for card in pre['hand'] if card[1] == root]
    if len(selected) != 1 or pre['tick']+1 != event[1] or post['tick'] != event[1]:
        raise TrajectoryError('Root consumption is not an unambiguous next-boundary transition')
    hand_card = selected[0]; replacement = [card for card in post['hand'] if card[0] == hand_card[0]]
    receipt = feedback.get('scheduler_feedback', {})
    if (receipt.get('state') != 'succeeded' or receipt.get('resolvedCardParameter') != hand_card[3]
            or event[4] != hand_card[2]):
        raise TrajectoryError('Root input hand/parameter/cost/rotation corroboration failed')
    partial_kind = feedback.get('own_boundary_evidence_kind')
    strict_partial = (partial_kind == 'pending_draw_exact_hand_cycle_cost_parameter_elixir'
                      and feedback.get('own_boundary_evidence_version') == 'strict-partial-draw.v1')
    if strict_partial:
        # During the measured pending-draw window the native runtime removes
        # the selected slot first and fills it on a later tick.  Accept exactly
        # that transition, with no replacement row silently inferred.
        if (len(pre['hand']) != 4 or len(post['hand']) != 3
                or sorted(post['hand']) != sorted(card for card in pre['hand'] if card[0] != hand_card[0])
                or post['cycle'] != pre['cycle'] + [root]
                or type(pre.get('elixir')) is not int or type(post.get('elixir')) is not int
                or post['elixir'] > min(100000, pre['elixir'] - hand_card[2] * 10000 + 1000)):
            raise TrajectoryError('Strict partial-draw hand/cycle/fee boundary failed')
    elif (len(replacement) != 1 or replacement[0][1:] == hand_card[1:]
          or pre['cycle'] == post['cycle']):
        raise TrajectoryError('Root input hand/parameter/cost/rotation corroboration failed')
    form = feedback.get('effective_form_identity')
    if event[3] == 0:
        if form != 'unknown' or feedback.get('matched_by') != 'unique_native_event_and_own_boundary_hand_cost_parameter_cycle':
            raise TrajectoryError('Raw event0 must retain unknown effective form identity')
    else:
        if not isinstance(form, dict) or form.get('kind') != 'evolution' or form.get('event_card_id') != event[3]:
            raise TrajectoryError('Non-root native event lacks explicit versioned evolution alias evidence')
        if not form.get('evidence'): raise TrajectoryError('Evolution alias source evidence missing')
        for reference in form['evidence']: verify_reference(reference)
    return {'kind': 'native_root_input_consumption', 'owner': event[2], 'tick': event[1],
            'input_root_card': root, 'raw_native_event_card': event[3],
            'effective_form_identity': form, 'own_boundary_corroborated': True,
            'scope': 'Input root card consumption only; raw event0 does not establish which hero/evolution form spawned.'}


def validate_actor(actor):
    """Reject extra keys when reloading; permissive JSON passthrough is forbidden."""
    allowed = {'schema', 'tick', 'native_owner', 'hand', 'hand_complete', 'own_elixir_raw', 'crowns', 'towers'}
    if not isinstance(actor, dict) or set(actor) != allowed or actor.get('schema') != 'royale-phase2-actor-conservative.v1':
        raise TrajectoryError('Actor schema or allowlist changed')
    strict_int(actor['tick'], 'actor tick'); strict_int(actor['native_owner'], 'actor owner', 0, 1)
    strict_int(actor['own_elixir_raw'], 'actor own elixir', 0, 100000)
    if type(actor['hand_complete']) is not bool or len(actor['hand']) > 4:
        raise TrajectoryError('Invalid hand completeness')
    slots = []
    for card in actor['hand']:
        if set(card) != {'slot', 'card', 'cost'}:
            raise TrajectoryError('Private card parameter or unsupported card field in actor')
        slots.append(strict_int(card['slot'], 'actor slot', 0, 3))
        strict_int(card['card'], 'actor card', 1); strict_int(card['cost'], 'actor cost', 0, 100)
    if len(slots) != len(set(slots)) or actor['hand_complete'] != (set(slots) == {0, 1, 2, 3}):
        raise TrajectoryError('Inconsistent hand completeness/slots')
    if set(actor['crowns']) != {'own', 'opponent'}:
        raise TrajectoryError('Unsupported actor crowns')
    for value in actor['crowns'].values():
        strict_int(value, 'actor crown', 0, 3)
    tower_keys = set()
    for tower in actor['towers']:
        if set(tower) != {'side', 'role', 'hp', 'max_hp', 'present'}:
            raise TrajectoryError('Unsupported tower field/native ID')
        key = (tower['side'], tower['role'])
        if key[0] not in {'own', 'opponent'} or key[1] not in {'king', 'left', 'right'} or key in tower_keys:
            raise TrajectoryError('Invalid actor tower identity')
        tower_keys.add(key)
        if type(tower['present']) is not bool:
            raise TrajectoryError('Invalid tower presence')
        if tower['present']:
            strict_int(tower['hp'], 'actor tower HP')
            strict_int(tower['max_hp'], 'actor tower max HP', max(1, tower['hp']))
        elif tower['hp'] is not None or tower['max_hp'] is not None:
            raise TrajectoryError('Absent tower HP must stay unknown')
    canonical_bytes(actor)
    return actor


def _wait_provenance(decision, episode, actor, execution_tick):
    origin = decision.get('label_provenance', {})
    if origin.get('kind') != 'sampling_grid_absence' or origin.get('human_explicit_wait') is not False:
        raise TrajectoryError('WAIT must identify sampling-grid origin, not a fabricated explicit human choice')
    strict_int(origin.get('interval_ticks'), 'WAIT grid interval', 1)
    if execution_tick - actor['tick'] != origin['interval_ticks']:
        raise TrajectoryError('WAIT interval does not match actual native advance')
    if episode.get('source_journal_coverage') != 'complete' or not isinstance(episode.get('source_actions'), list):
        raise TrajectoryError('WAIT requires a complete source action journal')
    for event in episode['source_actions']:
        tick = strict_int(event['target_tick'], 'source action target tick')
        owner = strict_int(event['native_owner'], 'source action owner', 0, 1)
        if owner == actor['native_owner'] and actor['tick'] < tick <= execution_tick:
            raise TrajectoryError('Source action/ability exists inside the proposed WAIT interval')


def convert_episode(episode, split_entry, *, proof_path=None):
    """Convert real native pre-submit captures, quarantining unproven decisions.

    The input contains debug data; output policy observations never contain it.
    An output row can exist as a diagnostic artifact with both eligible flags
    false. Only eligibility-selected rows enter training.
    """
    if episode.get('schema') != EPISODE_SCHEMA:
        raise TrajectoryError('Unsupported native episode contract')
    source_id = episode['source_episode_id']
    if source_id != split_entry['source_episode_id'] or episode['leakage_group'] != split_entry['leakage_group']:
        raise TrajectoryError('Source/group mismatch against frozen learning split')
    version = sha_value(episode['version_fingerprint_sha256'], 'episode version')
    provenance = episode['provenance']
    for key in ('raw_payload_sha256', 'converter_sha256'):
        sha_value(provenance.get(key), key)
    if provenance['raw_payload_sha256'] != split_entry['payload_sha256']:
        raise TrajectoryError('Raw payload differs from frozen source')
    episode_reasons = _quality_reasons(episode)
    if episode.get('quality', {}).get('deterministic') == 'pass':
        try:
            evidence = verify_reference(episode['quality_evidence']['determinism'])
            status, _ = _repeatability_status([evidence['path']], source_id, version)
            if status != 'pass': raise TrajectoryError('Actual repeatability does not pass')
        except (KeyError, ValueError, TypeError, OSError):
            episode_reasons.append('actual_repeatability_semantics_unverified')
    proof = audit_subspace_proof(proof_path, version)
    towers = None
    if episode.get('reset_capture') is not None:
        towers = TowerAttestation.from_reset(episode['reset_capture'])
    rows = []; rejected = []; seen_decisions = set(); seen_requests = set(); last_tick = {}
    for index, decision in enumerate(episode.get('decisions', [])):
        try:
            decision_id = decision['decision_id']
            if not isinstance(decision_id, str) or not decision_id or decision_id in seen_decisions:
                raise TrajectoryError('Empty/duplicate native decision ID')
            seen_decisions.add(decision_id)
            owner = strict_int(decision['native_owner'], 'native owner', 0, 1)
            actor = validate_actor(project_actor(decision['before'], owner, towers))
            if actor['tick'] < last_tick.get(owner, -1):
                raise TrajectoryError('Nonmonotonic pre-action observations')
            last_tick[owner] = actor['tick']
            raw_ref = verify_json_value(decision.get('before_evidence'), decision['before'])
            label = copy.deepcopy(decision['label'])
            kind = label.get('kind')
            if kind == 'play_card':
                if set(label) != {'kind', 'slot', 'card', 'x', 'y'}:
                    raise TrajectoryError('Unsupported card label fields')
                slot = strict_int(label['slot'], 'label slot', 0, 3)
                strict_int(label['card'], 'label card', 1)
                strict_int(label['x'], 'requested native x', 0, 17999)
                strict_int(label['y'], 'requested native y', 0, 31999)
                cards = [card for card in actor['hand'] if card['slot'] == slot]
                if len(cards) != 1 or cards[0]['card'] != label['card']:
                    raise TrajectoryError('Label card/slot not present in own pre-action hand')
            elif kind == 'wait':
                if set(label) != {'kind'}:
                    raise TrajectoryError('Unsupported WAIT fields')
            else:
                raise TrajectoryError('Unsupported ability/unknown source label; never remapped to WAIT')
            execution_tick = _check_execution(decision, actor, label)
            request_id = decision['execution']['request_id']
            if request_id in seen_requests:
                raise TrajectoryError('One native request cannot supply duplicate training decisions')
            seen_requests.add(request_id)
            if kind == 'wait':
                _wait_provenance(decision, episode, actor, execution_tick)
            elif type(decision.get('source_event_index')) is not int:
                raise TrajectoryError('Card label must map to an original source event index')
            budget = affordability_mask(actor)
            slot_index = 0 if kind == 'wait' else label['slot']+1
            if not budget['values'][slot_index]:
                raise TrajectoryError('Executed label violates own pre-action affordability; investigate timing/conversion')
            for field in ('terminated', 'truncated'):
                if type(decision.get(field)) is not bool:
                    raise TrajectoryError(f'Missing actual transition flag: {field}')
            if decision['terminated'] and decision['truncated']:
                raise TrajectoryError('Transition cannot be both terminated and truncated')
            end_tick = strict_int(decision['transition_end_tick'], 'transition end tick', execution_tick)
            reward = strict_number(decision['reward'], 'reward')
            if not isinstance(decision.get('reward_version'), str) or not decision['reward_version']:
                raise TrajectoryError('Versioned reward required')
            source_time = decision.get('source_time_seconds')
            if source_time is not None:
                strict_number(source_time, 'source timestamp')
                if source_time < 0: raise TrajectoryError('Negative source timestamp')
            reasons = list(episode_reasons)
            if not actor['hand_complete']:
                reasons.append('incomplete_current_hand')
            if decision.get('command_ready') is not True:
                reasons.append('own_command_readiness_unverified_or_false')
            diagnostic_eligible = not reasons
            legal = subspace_mask(actor, decision.get('command_ready'), proof)
            hard_reasons = list(reasons)
            if not legal['full_legality_verified']:
                hard_reasons.append('full_public_subspace_legality_unverified')
            if not legal['values'][slot_index]:
                hard_reasons.append('label_outside_verified_action_subspace')
            row = {
                'schema': ROW_SCHEMA, 'episode_id': episode['episode_id'], 'source_episode_id': source_id,
                'decision_id': decision_id, 'decision_index': index, 'source_event_index': decision.get('source_event_index'),
                'leakage_group': episode['leakage_group'], 'learning_split': split_entry['learning_split'],
                'reconstruction_split': split_entry['reconstruction_split'],
                'version_fingerprint_sha256': version, 'source_time_seconds': source_time,
                'engine_tick': actor['tick'], 'native_owner': owner, 'actor': actor,
                'affordability_only_mask': budget, 'legal_action_mask': legal, 'action': label, 'action_kind': kind,
                'label_semantics': 'sampled_grid_no_source_action' if kind == 'wait' else 'requested_native_coordinate_not_final_impact',
                'label_provenance': copy.deepcopy(decision.get('label_provenance', {})),
                'actual_execution_tick': execution_tick, 'execution_feedback': copy.deepcopy(decision['execution']),
                'before_evidence': raw_ref, 'timing': copy.deepcopy(decision['timing']), 'transition_end_tick': end_tick,
                'reward': reward, 'reward_version': decision['reward_version'],
                'terminated': decision['terminated'], 'truncated': decision['truncated'],
                'quality': copy.deepcopy(episode['quality']),
                'configuration': copy.deepcopy(episode.get('configuration', {})),
                'quality_evidence': copy.deepcopy(episode.get('quality_evidence', {})),
                'quality_grade': ('quarantined_native_diagnostic' if not diagnostic_eligible else
                                  'independent_reference_validated' if episode['quality'].get('trajectory_validated') == 'pass' else
                                  'native_reconstructed_outcome_consistent_no_independent_trajectory_truth'),
                'official_gold': False, 'provenance': copy.deepcopy(provenance),
                'eligible_native_label_bc': diagnostic_eligible, 'eligible_hard_e': not hard_reasons,
                'native_label_exclusion_reasons': reasons, 'hard_e_exclusion_reasons': hard_reasons,
            }
            row['row_sha256'] = digest(row)
            rows.append(row)
        except (KeyError, ValueError, TypeError, OSError) as error:
            rejected.append({'decision_index': index, 'decision_id': decision.get('decision_id'),
                             'source_event_index': decision.get('source_event_index'),
                             'kind': decision.get('label', {}).get('kind'), 'reason': str(error)})
    return rows, {'source_episode_id': source_id, 'input_decisions': len(episode.get('decisions', [])),
                  'exported_diagnostic_rows': len(rows), 'rejected_decisions': rejected,
                  'eligible_native_label_bc': sum(row['eligible_native_label_bc'] for row in rows),
                  'eligible_hard_e': sum(row['eligible_hard_e'] for row in rows),
                  'quality_exclusion_reasons': episode_reasons, 'subspace_proof': proof,
                  'input_action_kinds': dict(Counter(decision.get('label', {}).get('kind', 'missing') for decision in episode.get('decisions', [])))}


def validate_row(row):
    if row.get('schema') != ROW_SCHEMA:
        raise TrajectoryError('Unsupported trajectory row schema')
    if row.get('row_sha256') != digest({key: value for key, value in row.items() if key != 'row_sha256'}):
        raise TrajectoryError('Trajectory row hash mismatch')
    validate_actor(row['actor'])
    if (row['engine_tick'], row['native_owner']) != (row['actor']['tick'], row['actor']['native_owner']):
        raise TrajectoryError('Actor tick/owner mismatch')
    if row.get('action_kind') != row['action']['kind']:
        raise TrajectoryError('Action index column disagrees with action semantics')
    if row.get('official_gold') is not False:
        raise TrajectoryError('Local reconstructed data are never official gold')
    if row['eligible_hard_e'] and (not row['eligible_native_label_bc'] or row['hard_e_exclusion_reasons']):
        raise TrajectoryError('Inconsistent eligibility')
    if row['eligible_native_label_bc']:
        if _quality_reasons(row) or not row['actor']['hand_complete'] or row['native_label_exclusion_reasons']:
            raise TrajectoryError('Eligible native row lacks strict quality/source-level evidence')
        if row['actual_execution_tick'] <= row['engine_tick'] or row['execution_feedback']['status'] != 'executed':
            raise TrajectoryError('Eligible row missing actual post-observation execution')
        verify_reference(row['before_evidence']); verify_reference(row['execution_feedback']['evidence'])
    if row['eligible_hard_e']:
        mask = row['legal_action_mask']
        proof_ref = verify_reference(mask.get('proof'))
        proof = audit_subspace_proof(proof_ref['path'], row['version_fingerprint_sha256'])
        expected = subspace_mask(row['actor'], mask['command_ready'], proof)
        if mask != expected or not mask['full_legality_verified']:
            raise TrajectoryError('Eligible hard-E row mask/proof no longer validates')
        action_index = 0 if row['action']['kind'] == 'wait' else row['action']['slot']+1
        if not mask['values'][action_index]:
            raise TrajectoryError('Eligible hard-E label is masked')
    canonical_bytes(row)
    return row


def export_episodes(episode_paths, split_path, output, *, proof_path=None, expected_version=None,
                    quality_supplements=(), missing_inputs=()):
    """Arrow stores each canonical row and typed index fields; readback is mandatory."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    output = project_path(output); output.mkdir(parents=True, exist_ok=True)
    split, entries = load_learning_split(split_path)
    episode_paths = list(episode_paths); missing_inputs = list(missing_inputs)
    rows = []; reports = []; seen = set(); attempted_sources = set(); inputs = []; failed_inputs = []
    for failure in missing_inputs:
        source_id = failure['source_episode_id']
        if source_id not in entries or source_id in attempted_sources:
            raise TrajectoryError('Missing-input record is unfrozen or duplicates another missing source')
        if not isinstance(failure.get('reason'), str) or not failure['reason']:
            raise TrajectoryError('Missing-input source requires an explicit failure reason')
        if failure.get('path') is not None:
            raise TrajectoryError('Unavailable native result must not claim an existing input path')
        attempted_sources.add(source_id)
        failed_inputs.append(dict(failure, input_available=False))
    for item in episode_paths:
        path = project_path(item)
        episode = None
        try:
            episode = read_json(path)
            source_id = episode.get('source_episode_id')
            if source_id in attempted_sources:
                raise TrajectoryError('Source has multiple attempted inputs, including a missing-input record')
            if source_id in entries: attempted_sources.add(source_id)
            if episode.get('schema_version') == 'royale-phase2-native-reconstruction.v1':
                episode = adapt_native_reconstruction(path, quality_supplements=quality_supplements, expected_version=expected_version)
            if expected_version is not None and episode.get('version_fingerprint_sha256') != expected_version:
                raise TrajectoryError('Native episode fingerprint differs from actual verified assets')
            if episode['source_episode_id'] in seen:
                raise TrajectoryError('Duplicate source episode/hypothesis in export')
            seen.add(episode['source_episode_id'])
            if episode['source_episode_id'] not in entries:
                raise TrajectoryError('Source episode absent from predeclared frozen split')
            converted, report = convert_episode(episode, entries[episode['source_episode_id']], proof_path=proof_path)
            report['adapter_rejections'] = episode.get('adapter_rejections', [])
            report['adapter_input_decisions'] = episode.get('adapter_input_decision_count', len(episode.get('decisions', [])))
            rows.extend(converted); reports.append(report)
            inputs.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': file_sha(path)})
        except (KeyError, ValueError, TypeError, OSError) as error:
            failed_inputs.append({'path': path.relative_to(ROOT).as_posix(), 'reason': str(error),
                                  'source_episode_id': episode.get('source_episode_id') if isinstance(episode, dict) else None})
    seen_ids = set(); group_splits = {}; source_splits = {}; last_ticks = {}
    for row in rows:
        validate_row(row)
        identity = (row['source_episode_id'], row['decision_id'])
        if identity in seen_ids: raise TrajectoryError('Duplicate source decision')
        seen_ids.add(identity)
        for field, mapping in [('leakage_group', group_splits), ('source_episode_id', source_splits)]:
            key = row[field]
            if key in mapping and mapping[key] != row['learning_split']:
                raise TrajectoryError('Cross-split source/group leakage')
            mapping[key] = row['learning_split']
        stream = (row['episode_id'], row['native_owner'])
        if row['engine_tick'] < last_ticks.get(stream, -1): raise TrajectoryError('Nonmonotonic stream')
        last_ticks[stream] = row['engine_tick']
    schema = pa.schema([('source_episode_id', pa.string()), ('episode_id', pa.string()), ('decision_id', pa.string()),
                        ('leakage_group', pa.string()), ('learning_split', pa.string()), ('engine_tick', pa.int64()),
                        ('native_owner', pa.int8()), ('version_fingerprint_sha256', pa.string()),
                        ('source_event_index', pa.int64()), ('source_time_seconds', pa.float64()),
                        ('actual_execution_tick', pa.int64()), ('action_kind', pa.string()),
                        ('reward', pa.float64()), ('reward_version', pa.string()),
                        ('terminated', pa.bool_()), ('truncated', pa.bool_()),
                        ('eligible_hard_e', pa.bool_()), ('eligible_native_label_bc', pa.bool_()),
                        ('row_sha256', pa.string()), ('row_json', pa.large_string())],
                       metadata={b'schema': ROW_SCHEMA.encode(), b'official_gold': b'false', b'actor_separation': b'strict_allowlist'})
    records = [{key: row[key] for key in schema.names if key != 'row_json'} | {'row_json': canonical_bytes(row).decode('utf-8')} for row in rows]
    parquet = output / 'trajectories.parquet'
    temporary = output / 'trajectories.parquet.partial'
    pq.write_table(pa.Table.from_pylist(records, schema=schema), temporary, compression='zstd')
    readback = pq.read_table(temporary).to_pylist()
    if [record['row_json'] for record in readback] != [record['row_json'] for record in records]:
        raise TrajectoryError('Parquet roundtrip content mismatch')
    for record in readback:
        validate_row(json.loads(record['row_json']))
    temporary.replace(parquet)
    sample_rows = rows[:10]
    sample_ids = {(row['source_episode_id'], row['decision_id']) for row in sample_rows}
    for row in rows:
        if len(sample_rows) >= 20: break
        if row['action']['kind'] == 'play_card' and (row['source_episode_id'], row['decision_id']) not in sample_ids:
            sample_rows.append(row); sample_ids.add((row['source_episode_id'], row['decision_id']))
    with (output / 'sample.jsonl').open('w', encoding='utf-8', newline='\n') as handle:
        for row in sample_rows: handle.write(canonical_bytes(row).decode()+'\n')
    traces = []
    for row in sample_rows:
        source_path = row['provenance'].get('source_episode')
        if row['action']['kind'] != 'play_card' or not source_path: continue
        source_document = read_json(source_path)
        source_event = next(event for event in source_document['action_journal'] if event['source_event_index'] == row['source_event_index'])
        traces.append({'source_episode_id': row['source_episode_id'], 'decision_id': row['decision_id'],
                       'source_event_index': row['source_event_index'], 'original_source_action': source_event,
                       'exported_requested_action': row['action'], 'observation_tick': row['engine_tick'],
                       'execution_tick': row['actual_execution_tick'], 'row_sha256': row['row_sha256'],
                       'scope': 'Debug provenance only; never actor/model input'})
    with (output/'source_action_trace.jsonl').open('w', encoding='utf-8', newline='\n') as handle:
        for trace in traces: handle.write(canonical_bytes(trace).decode()+'\n')
    reason_counts = Counter(reason for row in rows for reason in row['hard_e_exclusion_reasons'])
    by_split = {}
    for learning_split in ('train', 'validation', 'test'):
        subset = [row for row in rows if row['learning_split'] == learning_split]
        by_split[learning_split] = {'rows': len(subset), 'episodes': len({row['source_episode_id'] for row in subset}),
                                    'eligible_hard_e': sum(row['eligible_hard_e'] for row in subset),
                                    'eligible_hard_e_play': sum(row['eligible_hard_e'] and row['action']['kind'] == 'play_card' for row in subset),
                                    'eligible_native_label_bc': sum(row['eligible_native_label_bc'] for row in subset)}
    by_source_role = {}
    for role in sorted({row['reconstruction_split'] for row in rows}):
        subset = [row for row in rows if row['reconstruction_split'] == role]
        by_source_role[role] = {'rows': len(subset), 'source_episodes': len({row['source_episode_id'] for row in subset}),
                                'eligible_hard_e': sum(row['eligible_hard_e'] for row in subset),
                                'eligible_native_label_bc': sum(row['eligible_native_label_bc'] for row in subset),
                                'hard_e_source_ids': sorted({row['source_episode_id'] for row in subset if row['eligible_hard_e']})}
    report = {'schema': 'royale-phase2-trajectory-export.v1', 'status': 'readback_verified',
              'split_sha256': split['split_sha256'], 'inputs': inputs, 'failed_inputs': failed_inputs,
              'attempted_episode_files': len(episode_paths), 'unavailable_episode_inputs': len(missing_inputs),
              'attempted_source_episodes': len(attempted_sources), 'converted_episodes': len(reports),
              'attempted_source_ids': sorted(attempted_sources),
              'frozen_episode_denominator': len(entries), 'unattempted_frozen_source_ids': sorted(set(entries)-attempted_sources),
              'rows': len(rows), 'eligible_hard_e': sum(row['eligible_hard_e'] for row in rows),
              'eligible_native_label_bc': sum(row['eligible_native_label_bc'] for row in rows),
              'official_gold_rows': 0, 'hard_e_exclusion_reason_counts': dict(reason_counts), 'by_split': by_split,
              'by_reconstruction_source_role': by_source_role,
              'before_after_distributions': {
                  'all_parsed_decisions': dict(Counter(row['action']['kind'] for row in rows)),
                  'hard_e_decisions': dict(Counter(row['action']['kind'] for row in rows if row['eligible_hard_e'])),
                  'all_card_labels': dict(Counter(str(row['action'].get('card')) for row in rows if row['action']['kind'] == 'play_card')),
                  'hard_e_card_labels': dict(Counter(str(row['action'].get('card')) for row in rows if row['eligible_hard_e'] and row['action']['kind'] == 'play_card')),
              },
              'filter_bias_note': 'The two-spell subspace excludes troop/building/ability labels and any episode failing runtime, repeatability, outcome, strict source levels or version checks. Source WAIT is sampled absence, not an explicit human choice. No claim of whole-game distribution or official temporal fidelity.',
              'episodes': reports,
              'parquet': {'path': parquet.relative_to(ROOT).as_posix(), 'sha256': file_sha(parquet), 'bytes': parquet.stat().st_size, 'readback_rows': len(readback)},
              'exporter_sha256': file_sha(Path(__file__)),
              'determinism_comparator_sha256': file_sha(ROOT/'royale_phase2/determinism.py'),
              'independent_reference_available': any(row['quality']['trajectory_validated'] == 'pass' for row in rows)}
    report['expected_version_fingerprint_sha256'] = expected_version
    write_json(output / 'export_manifest.json', report)
    return report


def load_export(path, *, profile='hard_e'):
    import pyarrow.parquet as pq
    if profile not in {'hard_e', 'native_label_bc'}:
        raise TrajectoryError('Explicit supported quality profile required')
    manifest_path = project_path(path)
    if manifest_path.is_dir(): manifest_path /= 'export_manifest.json'
    manifest = read_json(manifest_path)
    if manifest.get('schema') != 'royale-phase2-trajectory-export.v1':
        raise TrajectoryError('Unsupported trajectory export manifest')
    parquet = project_path(manifest['parquet']['path'])
    if file_sha(parquet) != manifest['parquet']['sha256']:
        raise TrajectoryError('Trajectory Parquet hash mismatch')
    rows = []; checked_repeats = set()
    all_rows = pq.read_table(parquet).to_pylist()
    if len(all_rows) != manifest['rows']:
        raise TrajectoryError('Trajectory count mismatch')
    eligibility = 'eligible_'+profile
    for record in all_rows:
        row = validate_row(json.loads(record['row_json']))
        if any(record[key] != row[key] for key in record if key != 'row_json'):
            raise TrajectoryError('Parquet typed index disagrees with canonical row')
        if row[eligibility]:
            evidence = verify_reference(row['quality_evidence']['determinism'])
            key = (row['source_episode_id'], row['version_fingerprint_sha256'], evidence['sha256'])
            if key not in checked_repeats:
                status, _ = _repeatability_status([evidence['path']], key[0], key[1])
                if status != 'pass': raise TrajectoryError('Eligible exported source no longer has actual reset evidence')
                checked_repeats.add(key)
            rows.append(row)
    return rows, manifest
