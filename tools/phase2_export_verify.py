"""Run actual empty-export/readback/refusal and synthetic contract checks."""
from __future__ import annotations
import importlib.metadata
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import export_episodes, freeze_learning_split, project_path, write_json
from royale_phase2.learning import train_bc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-export', help='Optional real failed/unqualified export for an actual CLI refusal test')
    parser.add_argument('--version-manifest', default='data/phase2/version_manifest.json')
    args = parser.parse_args()
    output = project_path('outputs/phase2/learning_contracts'); output.mkdir(parents=True, exist_ok=True)
    split = 'data/phase2/learning/split_manifest.json'
    freeze_learning_split('data/phase2/frozen_manifest.json', split)
    report = export_episodes([], split, output/'empty_export')
    model_output = output/'empty_model_must_not_exist'
    refusal = None
    try:
        # The all-zero value is deliberately irrelevant: zero-sample rejection
        # occurs before any version comparison, optimization or file creation.
        train_bc(output/'empty_export', model_output, version_fingerprint='0'*64)
    except ValueError as error:
        refusal = str(error)
    if not refusal or 'Zero eligible' not in refusal or (model_output/'checkpoint.json').exists() or (model_output/'weights.npz').exists():
        raise AssertionError('Empty training input did not safely refuse')
    command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests_phase2', '-p', 'test_trajectories.py', '-v']
    tested = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True, encoding='utf-8')
    (output/'unittest.log').write_text(tested.stdout+tested.stderr, encoding='utf-8')
    record = {'schema': 'royale-phase2-learning-contract-evidence.v1',
              'scope': 'Actual empty Parquet export/readback/refusal; synthetic privacy/numerical contracts plus actual frozen-source grouping checks. No training data or native execution manufactured.',
              'empty_export': report['parquet'], 'empty_export_rows': report['rows'],
              'empty_training_refusal': refusal, 'model_created': False,
              'synthetic_unit_test_command': command, 'synthetic_unit_test_exit_code': tested.returncode,
              'synthetic_unit_test_log': (output/'unittest.log').relative_to(ROOT).as_posix(),
              'python': sys.version, 'python_executable': sys.executable,
              'dependencies': {name: importlib.metadata.version(name) for name in ('numpy', 'pyarrow')},
              'native_bc_run': False, 'valid_native_training_decisions': 0}
    if args.native_export:
        command = [sys.executable, '-B', 'tools/phase2_train_bc.py', '--export', args.native_export,
                   '--version-manifest', args.version_manifest, '--output',
                   'outputs/phase2/learning_contracts/actual_failed_episode_refusal']
        attempted = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1'),
                                   capture_output=True, text=True, encoding='utf-8')
        (output/'real_episode_training_refusal.log').write_text(attempted.stdout+attempted.stderr, encoding='utf-8')
        record['actual_failed_episode_guard'] = {'command': command, 'exit_code': attempted.returncode,
                                                'log': 'outputs/phase2/learning_contracts/real_episode_training_refusal.log'}
        if attempted.returncode != 2 or 'Zero eligible' not in attempted.stderr:
            raise AssertionError('Expected real unqualified episode training refusal did not occur')
    write_json(output/'report.json', record)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return tested.returncode


if __name__ == '__main__':
    raise SystemExit(main())
