"""Consume existing r1/r2/r3 artifacts; never opens or waits for native RPC.

Only fixed round r1 supplies trajectories. All three rounds establish reset
repeatability. Unqualified sources remain in the denominator and are isolated;
only the actually qualified subset may satisfy the training gates.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import (TrajectoryError, audit_subspace_proof, digest, export_episodes,
                                       file_sha, load_export, load_learning_split, project_path, read_json,
                                       verify_reference, write_json, _repeatability_status)
from royale_phase2.learning import NumpyBCPolicy
from royale_phase2.versioning import verify_manifest


def completed_round(root, cohort_entries):
    """Return available artifacts and per-source failures, without dropping IDs.

    A malformed shared queue identity remains a global integrity failure. A
    missing/unrecorded/corrupt individual result cannot disqualify other sources.
    """
    root = project_path(root)
    queue_path = root/'queue.json'
    expected = {entry['source_episode_id'] for entry in cohort_entries}
    failures = []
    def failed(source_id, reason, **extra):
        failures.append({'source_episode_id': source_id,
                         'round_root': root.relative_to(ROOT).as_posix(), 'reason': reason, **extra})
    if not queue_path.is_file():
        for source_id in sorted(expected): failed(source_id, 'Missing recorded round queue')
        return {}, failures
    queue = read_json(queue_path)
    jobs = {entry['source_episode_id']: entry for entry in queue['jobs']}
    if len(jobs) != len(queue['jobs']) or set(jobs)-expected:
        raise TrajectoryError('Native round queue contains duplicate or unfrozen source IDs')
    if queue['identity']['grid_ticks'] != 5:
        raise TrajectoryError('Learning cohort requires the predeclared grid5; do not combine grids as extra samples')
    results = {}
    for entry in cohort_entries:
        source_id = entry['source_episode_id']; job = jobs.get(source_id)
        if job is None:
            failed(source_id, 'Source absent from round jobs'); continue
        if job.get('state') != 'recorded':
            failed(source_id, 'Round job has no recorded result', job_state=job.get('state')); continue
        try:
            reference = verify_reference(job['result']); episode = read_json(reference['path'])
            if episode['source_episode_id'] != source_id:
                raise TrajectoryError('Recorded native source identity mismatch')
            results[source_id] = reference
        except (OSError, KeyError, TypeError, ValueError) as error:
            failed(source_id, str(error), result=job.get('result'))
    return results, failures


def normalize_repeatability(paths, cohort_entries, rounds, version):
    """Keep every candidate; a pass requires exactly its three declared runs.

    This derived report never creates a pass: it preserves one only after the
    source report and locked native run evidence have both been checked.
    """
    expected = {entry['source_episode_id'] for entry in cohort_entries}
    reported = {}; source_reports = []; report_failures = []
    for item in paths:
        path = project_path(item)
        try:
            report = read_json(path)
            if report.get('schema') != 'royale-phase2-repeatability.v1':
                raise TrajectoryError('Actual reset repeatability report schema mismatch')
            reference = {'path': path.relative_to(ROOT).as_posix(), 'sha256': file_sha(path)}
            source_reports.append(reference)
        except OSError as error:
            report_failures.append({'path': path.relative_to(ROOT).as_posix(), 'reason': str(error)})
            continue
        for entry in report.get('episodes', []):
            source_id = entry['source_episode_id']
            if source_id not in expected or source_id in reported:
                raise TrajectoryError('Repeatability report contains an unfrozen or duplicate source ID')
            reported[source_id] = (entry, reference)
    episodes = []
    for source_id in sorted(expected):
        original, reference = reported.get(source_id, ({}, None))
        reasons = []
        for index, round_ in enumerate(rounds, 1):
            if source_id not in round_: reasons.append(f'Missing valid declared r{index} native episode artifact')
        if not original:
            reasons.append('Source missing from repeatability reports')
        elif original.get('status') != 'pass':
            reasons.append('Reported repeatability status: '+str(original.get('status', 'unknown')))
        elif not reasons:
            try:
                expected_paths = {round_[source_id]['path'] for round_ in rounds}
                actual_paths = {verify_reference(run)['path'] for run in original['runs']}
                if len(original['runs']) != 3 or actual_paths != expected_paths:
                    raise TrajectoryError('Repeatability did not compare the declared r1/r2/r3 artifacts')
                status, _ = _repeatability_status([reference['path']], source_id, version)
                if status != 'pass': raise TrajectoryError('Repeatability evidence did not validate as pass')
            except (OSError, KeyError, TypeError, ValueError) as error:
                reasons.append(str(error))
        if reasons:
            status = 'not_run' if not original else 'fail'
            episodes.append({'source_episode_id': source_id, 'version_fingerprint': version,
                             'status': status, 'pipeline_exclusion_reasons': reasons,
                             'reported_entry': original or None, 'reported_evidence': reference})
        else:
            episodes.append(dict(original, reported_evidence=reference))
    return {'schema': 'royale-phase2-repeatability.v1', 'episodes': episodes,
            'episode_denominator': len(expected), 'episodes_passed': sum(e['status'] == 'pass' for e in episodes),
            'source_reports': source_reports, 'report_failures': report_failures,
            'scope': 'Artifact-only validation of declared rounds; no new native run and no newly inferred pass.'}


def run_logged(command, output, name):
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1'),
                            capture_output=True, text=True, encoding='utf-8')
    path = output/(name+'.log'); path.write_text(result.stdout+result.stderr, encoding='utf-8')
    return {'command': command, 'started_utc': started, 'exit_code': result.returncode,
            'log': {'path': path.relative_to(ROOT).as_posix(), 'sha256': file_sha(path)}}


def check_training_gates(exported):
    """Qualification is local to rows; failed candidates are audit statistics."""
    if exported['eligible_hard_e'] < 10000:
        raise TrajectoryError(f'Only {exported["eligible_hard_e"]} hard-E decisions; need >=10000 without copying repeats')
    for subset in ('train', 'validation', 'test'):
        if not exported['by_split'][subset]['eligible_hard_e']:
            raise TrajectoryError('No actual eligible '+subset+' subset')
        if not exported['by_split'][subset]['eligible_hard_e_play']:
            raise TrajectoryError('No real eligible card-play labels in '+subset+' subset')


def validated_cohort(path, *, formal=False):
    cohort = read_json(path)
    schema = 'royale-phase2-formal-training-cohort.v1' if formal else 'royale-phase2-native-learning-cohort.v1'
    counts = {'train': 1, 'validation': 1} if formal else {'train': 10, 'validation': 3, 'test': 3}
    if cohort.get('schema') != schema or cohort['cohort_sha256'] != digest({key: value for key, value in cohort.items() if key != 'cohort_sha256'}):
        raise TrajectoryError('Frozen native learning cohort integrity mismatch')
    if cohort['counts'] != counts or len(cohort['entries']) != sum(counts.values()):
        raise TrajectoryError('Unexpected frozen learning cohort quotas')
    verify_reference(cohort['upstream_manifest']); verify_reference(cohort['combined_manifest'])
    if formal and any(entry['split'] != 'formal' for entry in cohort['entries']):
        raise TrajectoryError('Formal training supplement contains a nonformal source')
    return cohort


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort-manifest', default='data/phase2/learning/native_cohort_manifest.json')
    parser.add_argument('--split-manifest', default='data/phase2/learning/extended_split_manifest.json')
    parser.add_argument('--r1', default='outputs/phase2/batches/learning16-r1')
    parser.add_argument('--r2', default='outputs/phase2/batches/learning16-r2')
    parser.add_argument('--r3', default='outputs/phase2/batches/learning16-r3')
    parser.add_argument('--additional-cohort-manifest', help='Optional frozen formal train1/validation1 supplement')
    parser.add_argument('--additional-r1', default='outputs/phase2/batches/formal-learning2-r1')
    parser.add_argument('--additional-r2', default='outputs/phase2/batches/formal-learning2-r2')
    parser.add_argument('--additional-r3', default='outputs/phase2/batches/formal-learning2-r3')
    parser.add_argument('--repeatability', action='append', help='Repeatable; one merged report or disjoint16+2 reports')
    parser.add_argument('--version-manifest', default='data/phase2/version_manifest.json')
    parser.add_argument('--subspace-proof', default='data/phase2/action_subspace_proof.json')
    parser.add_argument('--output', default='outputs/phase2/learning_pipeline')
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--hidden', type=int, default=64)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--seed', type=int, default=19)
    args = parser.parse_args()
    output = project_path(args.output); output.mkdir(parents=True, exist_ok=True)
    state_path = output/'pipeline_state.json'
    previous = read_json(state_path) if state_path.is_file() else None
    state = {'schema': 'royale-phase2-learning-pipeline.v1', 'status': 'checking_artifacts',
             'native_rpc_opened': False, 'native_matches_executed': 0,
             'trajectory_round': 'r1_only_predeclared', 'commands': [],
             'resume_command': [sys.executable, '-B', 'tools/phase2_learning_pipeline.py']+sys.argv[1:]}
    preserve_previous_state = bool(previous and previous.get('identity_sha256'))
    try:
        cohort = validated_cohort(args.cohort_manifest)
        cohorts = [cohort]; round_groups = [[args.r1, args.r2, args.r3]]
        if args.additional_cohort_manifest:
            cohorts.append(validated_cohort(args.additional_cohort_manifest, formal=True))
            round_groups.append([args.additional_r1, args.additional_r2, args.additional_r3])
        all_entries = [entry for item in cohorts for entry in item['entries']]
        state['cohort_counts'] = dict(Counter(entry['learning_split'] for entry in all_entries))
        state['cohort_source_denominator'] = len(all_entries)
        if len({entry['source_episode_id'] for entry in all_entries}) != len(all_entries):
            raise TrajectoryError('A source was duplicated across supplementary cohorts')
        split, split_entries = load_learning_split(args.split_manifest)
        if any(split['split_sha256'] != item['learning_split_sha256'] for item in cohorts):
            raise TrajectoryError('Cohort and whole-source learning split differ')
        for entry in all_entries:
            if split_entries[entry['source_episode_id']]['learning_split'] != entry['learning_split']:
                raise TrajectoryError('Cohort changed an episode learning split')
        version = verify_manifest(args.version_manifest)['fingerprint']
        proof = audit_subspace_proof(args.subspace_proof, version)
        if proof['status'] != 'verified_local_rule_evidence':
            raise TrajectoryError('Hard action-subspace proof missing/unverified: '+str(proof))
        round_paths = [path for paths in round_groups for path in paths]
        if len({project_path(path) for path in round_paths}) != len(round_paths):
            raise TrajectoryError('r1/r2/r3 must be three distinct independently reset round roots')
        rounds = [{}, {}, {}]; round_failures = []
        for item, paths in zip(cohorts, round_groups):
            for index, path in enumerate(paths):
                available, failures = completed_round(path, item['entries'])
                rounds[index].update(available)
                round_failures.extend(dict(failure, round='r'+str(index+1)) for failure in failures)
        state['round_failures'] = round_failures
        repeat_paths = args.repeatability or ['outputs/phase2/repeatability/learning16.json']
        normalized = normalize_repeatability(repeat_paths, all_entries, rounds, version)
        repeated = {entry['source_episode_id']: entry for entry in normalized['episodes']}
        state['failed_repeat_sources'] = sorted(source_id for source_id, entry in repeated.items() if entry['status'] != 'pass')
        state['repeatability_status_counts'] = dict(Counter(entry['status'] for entry in repeated.values()))
        state['repeatability_report_failures'] = normalized['report_failures']
        state['qualification_scope'] = 'Train only the actually qualified subset; all failed/unavailable source attempts remain recorded in the fixed denominator.'
        identity = {'cohort_sha256s': [item['cohort_sha256'] for item in cohorts], 'split_sha256': split['split_sha256'],
                    'version_fingerprint': version, 'proof_sha256': proof['proof']['sha256'],
                    'repeatability': normalized['source_reports'], 'missing_repeatability_reports': normalized['report_failures'],
                    'normalized_repeatability_sha256': digest(normalized),
                    'round_artifacts': rounds, 'round_failures': round_failures, 'trajectory_round': 'r1',
                    'pipeline_sha256': file_sha(Path(__file__)),
                    'determinism_comparator_sha256': file_sha(ROOT/'royale_phase2/determinism.py'),
                    'exporter_sha256': file_sha(ROOT/'royale_phase2/trajectories.py'),
                    'learning_sha256': file_sha(ROOT/'royale_phase2/learning.py'),
                    'training_configuration': {'epochs': args.epochs, 'hidden': args.hidden, 'batch_size': args.batch_size,
                                               'seed': args.seed, 'minimum_samples': 10000, 'profile': 'hard_e'}}
        identity_sha = digest(identity)
        if previous and previous.get('identity_sha256') not in {None, identity_sha}:
            preserve_previous_state = True
            raise TrajectoryError('Inputs or implementation differ from this pipeline output; choose a fresh output directory')
        if (output/'model/checkpoint.json').is_file() and (not previous or previous.get('identity_sha256') != identity_sha):
            preserve_previous_state = bool(previous)
            raise TrajectoryError('Existing checkpoint has no matching pipeline identity; choose a fresh output directory')
        existing_export = None
        if (output/'model/checkpoint.json').is_file():
            # Existing model bytes are never overwritten after a mismatch. Do
            # not reserialize its original training export merely to reuse it.
            reference = verify_reference(previous['export'])
            if project_path(reference['path']) != output/'export/export_manifest.json':
                raise TrajectoryError('Prior training export path differs; choose a fresh output directory')
            metadata = NumpyBCPolicy.load(output/'model/checkpoint.json', version_fingerprint=version).metadata
            if metadata['training_data']['export_manifest_sha256'] != reference['sha256']:
                raise TrajectoryError('Existing checkpoint and training export differ; choose a fresh output directory')
            if previous.get('checkpoint'):
                verify_reference(previous['checkpoint'])
            _, existing_export = load_export(output/'export', profile='hard_e')
        preserve_previous_state = False
        state.update(identity=identity, identity_sha256=identity_sha, status='exporting_r1')
        write_json(state_path, state)
        normalized_path = output/'validated_repeatability.json'; write_json(normalized_path, normalized)
        state['validated_repeatability'] = {'path': normalized_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(normalized_path)}
        paths = [rounds[0][entry['source_episode_id']]['path'] for entry in all_entries if entry['source_episode_id'] in rounds[0]]
        missing_inputs = [dict(failure, path=None) for failure in round_failures if failure['round'] == 'r1']
        exported = existing_export or export_episodes(paths, args.split_manifest, output/'export', proof_path=args.subspace_proof,
                                                      expected_version=version, quality_supplements=[normalized_path], missing_inputs=missing_inputs)
        state['export_reused'] = existing_export is not None
        state['export'] = {'path': (output/'export/export_manifest.json').relative_to(ROOT).as_posix(),
                           'sha256': file_sha(output/'export/export_manifest.json'),
                           'eligible_hard_e': exported['eligible_hard_e'], 'diagnostic_rows': exported['rows']}
        state.update(status='checking_training_gates'); write_json(state_path, state)
        state['failed_inputs'] = exported['failed_inputs']
        write_json(state_path, state)
        check_training_gates(exported)
        model_dir = output/'model'; checkpoint = model_dir/'checkpoint.json'
        reusable = False
        if checkpoint.is_file() and previous and previous.get('identity_sha256') == identity_sha:
            metadata = NumpyBCPolicy.load(checkpoint, version_fingerprint=version).metadata
            reusable = metadata['training_data']['export_manifest_sha256'] == state['export']['sha256']
            if not reusable:
                preserve_previous_state = True
                raise TrajectoryError('Existing checkpoint provenance differs; choose a fresh output directory')
        if not reusable:
            state['status'] = 'training_hard_e'; write_json(state_path, state)
            command = [sys.executable, '-B', 'tools/phase2_train_bc.py', '--export', str(output/'export'),
                       '--version-manifest', args.version_manifest, '--profile', 'hard_e', '--minimum-samples', '10000',
                       '--output', str(model_dir), '--epochs', str(args.epochs), '--hidden', str(args.hidden),
                       '--batch-size', str(args.batch_size), '--seed', str(args.seed)]
            trained = run_logged(command, output, 'train_bc'); state['commands'].append(trained); write_json(state_path, state)
            if trained['exit_code'] != 0: raise TrajectoryError('BC process failed; see train_bc.log')
        state.update(status='new_process_heldout_evaluation', checkpoint_reused=reusable); write_json(state_path, state)
        command = [sys.executable, '-B', 'tools/phase2_evaluate_model.py', '--checkpoint', str(checkpoint),
                   '--export', str(output/'export'), '--version-manifest', args.version_manifest, '--split', 'test',
                   '--output', str(output/'heldout_evaluation.json')]
        evaluated = run_logged(command, output, 'heldout_evaluation'); state['commands'].append(evaluated)
        if evaluated['exit_code'] != 0: raise TrajectoryError('New-process checkpoint evaluation failed; see heldout_evaluation.log')
        state.update(status='bc_and_new_process_holdout_complete',
                     checkpoint={'path': checkpoint.relative_to(ROOT).as_posix(), 'sha256': file_sha(checkpoint)},
                     evaluation={'path': (output/'heldout_evaluation.json').relative_to(ROOT).as_posix(),
                                 'sha256': file_sha(output/'heldout_evaluation.json')},
                     native_ten_match_requirement='not_run_by_this_artifact_pipeline')
        write_json(state_path, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, ValueError, TypeError) as error:
        state.update(status='refused_or_inputs_not_ready', reason=str(error), pipeline_complete=False,
                     checkpoint_exists=(output/'model/checkpoint.json').is_file())
        refusal_path = output/'pipeline_refusal.json' if preserve_previous_state else state_path
        if preserve_previous_state and previous:
            write_json(state_path, previous)
        write_json(refusal_path, state)
        print(json.dumps({'status': state['status'], 'reason': str(error), 'resume_state': str(state_path.relative_to(ROOT)),
                          'refusal_report': str(refusal_path.relative_to(ROOT)),
                          'previous_pipeline_state_preserved': preserve_previous_state,
                          'native_rpc_opened': False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
