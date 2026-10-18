"""Read-only acceptance join for a sealed Human demo; writes only a new report.

No HTTP, RPC, ADB, SSH, VM control, policy execution or browser operation.
Earlier failed reports are retained and referenced, never rewritten.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.phase2_platform_audit import audit_session

BASE = ROOT/'outputs/platform_next/fixes/20261018_manual_play'
UI = ROOT/'outputs/platform_next/human/ui_synthetic/report.json'
POWERSHELL = ROOT/'outputs/platform_next/fixes/20261018_human_encoding/powershell5.report.json'
COORDINATES = BASE/'coordinate_calibration_audit.json'
COMPARISON = BASE/'stream_rate_compare/report.json'
REQUIRED_CHECKS = {
    'human_bottom_ai_top_ready_paused', 'human_actual_consumption',
    'ai_input_contains_already_consumed_human_play', 'ai_actual_rule_reason_logged',
    'teaching_turn_advances_exact20_and_pauses', 'stale_card_rejected_without_advance',
    'continuous_native_advance_and_pause', 'continuous_human_play_consumed',
    'real_h264_to_http_mjpeg_multiple_changed_frames', 'shutdown_submitted'}
REQUIRED_SOURCE_FILES = {
    'royale_phase2/platform.py', 'royale_phase2/observability.py', 'royale_phase2/battlefield.py',
    'royale_phase2/platform_ui.html', 'royale_phase2/backend.py', 'tools/phase2_platform.py',
    'run_platform.ps1', 'royale_phase2/human.py', 'royale_phase2/platform_stream.py',
    'royale_phase2/human_ui.html', 'examples/human_ai_policy.py', 'run_play.ps1'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_path(value):
    require(isinstance(value, (str, Path)), 'Expected a project-local file path')
    path = Path(value)
    path = (path if path.is_absolute() else ROOT/path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        raise ValueError('Path escapes project root: ' + str(path)) from None
    return path


def sha(path):
    return hashlib.sha256(project_path(path).read_bytes()).hexdigest()


def relative(path):
    return str(project_path(path).relative_to(ROOT))


def decode_text(data):
    return data.decode('utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def summarize(session, check_path, audit_path):
    session = project_path(session)
    inputs = {}; checks = []

    def read(path, *, json_file=True):
        path = project_path(path)
        data = path.read_bytes()
        inputs[relative(path)] = hashlib.sha256(data).hexdigest()
        value = decode_text(data)
        return json.loads(value) if json_file else value

    def passed(name):
        checks.append(name)

    result = {'schema': 'royale-human-demo-acceptance.v1', 'status': 'fail',
              'session': relative(session), 'network_calls': 0, 'native_rpc_calls': 0,
              'checks': checks, 'input_sha256': inputs,
              'reporter_sha256': sha(__file__), 'auditor_sha256': sha('tools/phase2_platform_audit.py')}
    try:
        check = read(check_path); audit = read(audit_path)
        sealed = read(session/'session.json'); report = read(session/'report.json')
        config = read(session/'configuration.json')
        require(check.get('status') == 'pass' and project_path(check.get('session_folder')) == session,
                'Live checker is not pass or is bound to a different session')
        require(REQUIRED_CHECKS <= set(check.get('checks', [])), 'Live checker is missing a required Human/AI/stream check')
        passed('live_checker_pass_and_exact_session_binding')
        require(audit.get('status') == 'pass' and project_path(audit.get('session')) == session,
                'Artifact audit is not pass or is bound to a different session')
        require(sealed.get('error') is None and sealed.get('mode') == 'human' and report.get('mode') == 'human',
                'Sealed session has an error or is not Human mode')
        require(project_path(sealed.get('folder')) == session, 'Sealed snapshot folder differs from selected session')
        require(sealed.get('playing') is False, 'Sealed session claims the game is still running')
        passed('sealed_human_without_worker_error')

        # Re-run the purely offline auditor rather than trusting its old pass
        # string. It rebinds native wire, oracle/BF inputs, actions, feedback and
        # PNG first/last interval to the exact saved files.
        rebuilt = audit_session(session)
        require(canonical(rebuilt) == canonical(audit), 'Supplied artifact audit differs from a fresh offline reconstruction')
        for name, fingerprint in audit['input_sha256'].items():
            path = project_path(session/name)
            path.relative_to(session)
            require(sha(path) == fingerprint, 'Artifact changed after audit: ' + name)
            inputs[relative(path)] = fingerprint
        require(audit.get('mode') == 'human' and audit.get('rpc_calls') == 0,
                'Expected a native-wire-bound offline Human audit')
        require(audit.get('battlefield_observations_verified') is True and audit['decisions'] > 0 and
                audit['battlefield_policy_decisions'] == audit['decisions'], 'AI battlefield inputs were not all independently rebuilt')
        require(audit.get('human_actions_verified', 0) >= 2 and audit.get('pngs_verified', 0) >= 2,
                'Need at least two independently audited human requests and first/last PNG evidence')
        counts = audit.get('confirmed_by_controller', {})
        require(counts.get('human', 0) >= 2 and counts.get('model', 0) >= 1,
                'Need at least two human and one AI confirmed native consumption')
        require(check.get('initial_tick') == audit['start_tick'] and check.get('last_tick') == audit['last_tick'],
                'Live checker/audit native intervals differ')
        require(check.get('decisions') == audit['decisions'] and check.get('native_events') == audit['native_events'] and
                check.get('confirmed_consumptions') == audit['confirmed_consumptions_cross_checked'],
                'Live checker counts differ from independently audited artifacts')
        passed('fresh_wire_battlefield_action_feedback_and_png_audit')

        # At tick t the human request was made; it executed at t+1, and the AI
        # read an accepted state at or after t+1. Same-tick consumption and AI
        # observation are correctly allowed; this is not a future-event leak.
        decisions = [json.loads(line) for line in read(session/'decisions.jsonl', json_file=False).splitlines() if line.strip()]
        feedback = read(session/'execution_feedback.json')
        human_events = [row['card_event'] for row in feedback if row.get('owner') == 1 and row.get('card_consumed') is True]
        seen = []
        for decision in decisions:
            for event in human_events:
                if any(row.get('sequence') == event[0] and row.get('tick') == event[1] and row.get('owner') == 1 and
                       row.get('native_card_id') == event[3] and row['tick'] <= decision['tick']
                       for row in decision['policy_input']['history']):
                    seen.append({'native_sequence': event[0], 'event_tick': event[1], 'ai_decision_tick': decision['tick']})
        require(seen, 'No AI policy input contains an already-consumed human native event')
        require(any(isinstance(row.get('policy_reason'), str) and row['policy_reason'].strip() for row in decisions),
                'No actual rule-branch explanation was saved')
        passed('ai_actually_observed_prior_human_consumption')

        cleanup = read(session/'stream/cleanup.json'); stream = sealed.get('stream', {})
        token = stream.get('stream_token')
        require(isinstance(token, str) and token and cleanup.get('stream_token') == token and
                cleanup.get('android', {}).get('token') == token, 'Stream cleanup token differs from sealed stream')
        require(cleanup.get('verified') is True and cleanup.get('released') is True and cleanup.get('exit_code') == 0 and
                cleanup.get('android', {}).get('ownership_verified') is True and cleanup['android'].get('released') is True,
                'Final stream encoder cleanup is not ownership-verified and released')
        require(stream.get('remote_cleanup_verified') is True and
                canonical(stream.get('remote_cleanup')) == canonical(cleanup), 'Sealed stream cleanup differs from saved cleanup receipt')
        passed('final_stream_owned_encoder_released')

        sources = config.get('source_sha256', {})
        require(isinstance(sources, dict) and REQUIRED_SOURCE_FILES <= set(sources), 'Configuration omits required source fingerprints')
        for name, fingerprint in sources.items():
            require(sha(name) == fingerprint, 'Session was run with different source bytes: ' + name)
            inputs[relative(name)] = fingerprint
        policy = sealed.get('policy', {})
        require(policy.get('kind') == 'project_python_plugin' and policy.get('information_mode') == 'battlefield',
                'This acceptance expects a battlefield Python policy')
        require(sha(policy.get('path')) == policy.get('sha256'), 'Loaded policy bytes differ from current policy')
        inputs[relative(policy['path'])] = policy['sha256']
        require(config.get('arguments', {}).get('mode') == 'human', 'Configuration is not for Human mode')
        passed('all_recorded_sources_and_loaded_policy_match_current_bytes')

        ui = read(UI)
        require(ui.get('status') == 'pass' and project_path(ui.get('html')) == project_path('royale_phase2/human_ui.html') and
                ui.get('html_sha256') == sha('royale_phase2/human_ui.html'), 'UI synthetic report is stale or failed')
        require(ui.get('test_count') == len(ui.get('tests', [])) > 0 and
                all(row.get('status') == 'pass' for row in ui['tests']), 'UI synthetic tests did not all pass')
        passed('current_human_ui_synthetic_report')

        test_logs = list(BASE.glob('*tests*.log'))
        require(test_logs, 'No saved test-suite log')
        latest_tests = max(test_logs, key=lambda path: (path.stat().st_mtime_ns, path.name))
        text = read(latest_tests, json_file=False)
        matches = re.findall(r'Ran\s+(\d+)\s+tests?\s+in\s+([\d.]+)s', text)
        require(matches and text.rstrip().endswith('OK') and int(matches[-1][0]) > 0,
                'Latest test-suite log has no successful final unittest summary')
        tests = {'path': relative(latest_tests), 'count': int(matches[-1][0]), 'seconds': float(matches[-1][1]),
                 'scope': 'Offline unit/contract suite; not the number of actual native matches'}
        passed('latest_saved_test_log_ends_ok')

        powershell = read(POWERSHELL)
        require(powershell.get('status') == 'pass' and str(powershell.get('ps_version', '')).startswith('5.') and
                powershell.get('launcher_sha256') == sha('run_platform.ps1'), 'PowerShell5 report is stale or failed')
        require(powershell.get('new_file_read', {}).get('parse_success') is True and
                powershell['new_file_read'].get('exact_chinese_path') is True and
                powershell.get('new_native_capture', {}).get('exit_code') == 0 and
                powershell['new_native_capture'].get('exact_chinese_path') is True,
                'PowerShell5 Chinese-path/file/native-output regression did not pass')
        passed('current_launcher_powershell5_encoding_regression')

        coordinates = read(COORDINATES)
        require(coordinates.get('status') == 'measured_pinned_hud_mapping_supported_by_saved_floor_grid',
                'Coordinate audit has no supported measured-HUD conclusion')
        for source in coordinates['sources']:
            require(sha(source['path']) == source['sha256'], 'Coordinate geometry source changed')
        for saved in coordinates['saved_image_floor_grid_diagnostics']:
            require(sha(saved['path']) == saved['sha256'] and sha(saved['frame']['path']) == saved['frame']['sha256'],
                    'Saved coordinate diagnostic or PNG changed')
        passed('measured_coordinate_source_and_saved_image_evidence')

        comparison = read(COMPARISON)
        variants = {row['variant']: row for row in comparison.get('rows', [])}
        require(set(variants) == {'nominal_pts_fps_filter', 'all_frames_without_pts_filter'}, 'Expected the declared two stream A/B variants')
        comparison_rows = []
        for name, row in variants.items():
            require(row['window_seconds'] > 0 and type(row['new_jpegs']) is int and row['new_jpegs'] > 0 and
                    math.isclose(row['measured_jpegs_per_second'], row['new_jpegs']/row['window_seconds'], rel_tol=1e-8),
                    'Stream A/B frame count, duration and rate disagree')
            comparison_rows.append({'variant': name, 'new_jpegs': row['new_jpegs'], 'window_seconds': row['window_seconds'],
                'observed_jpegs_per_second': row['measured_jpegs_per_second'],
                'cleanup_verified_in_that_old_comparison': row.get('cleanup', {}).get('verified'),
                'cleanup_released_in_that_old_comparison': row.get('cleanup', {}).get('released')})
        passed('paused_scene_single_stream_ab_receipts_read')

        failures = []
        for name in ('human_live_001', 'human_live_002', 'human_live_003'):
            old_path = BASE/name/'report.json'
            old = read(old_path)
            require(old.get('status') == 'failed', 'Historical failed report was unexpectedly rewritten: ' + name)
            failures.append({'path': relative(old_path), 'status': old['status'], 'session_folder': old.get('session_folder'),
                             'completed_checks': old.get('checks', []), 'error': old.get('error')})
        native_terminal = audit['native_terminal']
        require(check.get('native_terminal') is native_terminal and report.get('complete_native_terminal') is native_terminal,
                'Natural terminal claims disagree')
        result.update(status='pass', native_terminal=native_terminal,
            run_scope='natural_native_terminal' if native_terminal else 'bounded_manually_closed_native_demo_not_complete_match',
            native_interval={'start_tick': audit['start_tick'], 'last_tick': audit['last_tick']},
            confirmed_by_controller=counts, decisions=audit['decisions'], state_rows=audit['state_rows'],
            pngs_verified=audit['pngs_verified'], battlefield_inputs_verified=True,
            prior_human_events_observed_by_ai=seen, final_stream_cleanup=cleanup,
            live_http_stream=check['stream'], source_files_verified=len(sources),
            ui_synthetic_tests=ui['test_count'], offline_tests=tests, powershell_version=powershell['ps_version'],
            coordinate_calibration={'scope': coordinates['scope'], 'exact_camera_verified': False,
                                    'actual_native_final_placement_verified': False},
            stream_ab={'scope': 'One sequential comparison of display encoding on the same paused scene; not a replicated performance study.',
                       'rows': comparison_rows,
                       'interpretation': 'JPEG throughput only. Never proof of 2x native physics, game-time progression, or training throughput.'},
            historical_failed_reports=failures,
            limitations=['Asynchronous wall-clock JPEG arrival is display evidence, not exact native-tick training labels.',
                         'Default opponent policy is explained Python rules, not an RL-trained model.',
                         'Existing NativeAgentEnv remains conservative/headless and restricted to WAIT/ordinary Fireball/Arrows.',
                         'Broader Human card requests do not certify full eight-card RL legality, all interactions, or official-game equivalence.',
                         'File/receipt consistency does not independently authenticate the engine or all combat causality.'])
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('session', 'check', 'audit', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    output = project_path(args.output)
    require(output.suffix.lower() == '.json' and not output.exists(), 'Choose a new project-local JSON output; existing evidence is never overwritten')
    result = summarize(args.session, args.check, args.audit)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'output': relative(output),
                      'checks': len(result['checks']), 'error': result.get('error')}, ensure_ascii=False))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
