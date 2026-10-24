"""运行有限的离线验收并保存命令/退出码/哈希；不验收原生或宣称十项研究已完成。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase3.common import configure, digest, local, now, read, reference, write


def required_modules(profile):
    return ('numpy',) if profile == 'core' else ('numpy', 'torch', 'pyarrow', 'gymnasium')


def dependency_inventory(profile):
    """Import each required module in this interpreter, retaining import failures."""
    result = {}
    for name in required_modules(profile):
        check = subprocess.run([sys.executable, '-B', '-c',
            f'import {name}; import importlib.metadata; print(importlib.metadata.version({name!r}))'],
            cwd=ROOT, capture_output=True, text=True, timeout=45)
        result[name] = {'available': check.returncode == 0, 'exit_code': check.returncode,
                        'version': check.stdout.strip() if check.returncode == 0 else None,
                        'error': check.stderr.strip() if check.returncode else None}
    return result


def final_json(stdout):
    """Read the final JSON result after optional progress records."""
    decoder = json.JSONDecoder()
    for start in reversed([index for index, char in enumerate(stdout) if char in '{[']):
        try:
            value, end = decoder.raw_decode(stdout, start)
        except json.JSONDecodeError:
            continue
        if not stdout[end:].strip():
            return value
    raise ValueError('Command did not emit a final JSON result')


def run_check(folder, label, arguments, timeout=180, validate=None):
    """Success requires the real command and, if supplied, its semantic assertion."""
    command = [sys.executable, '-B', *arguments]
    started = time.monotonic()
    failure = None
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                timeout=timeout)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        code, failure = None, f'Timeout after {timeout} seconds'
        stdout = error.stdout or b''
        stderr = error.stderr or b''
        if isinstance(stdout, bytes): stdout = stdout.decode('utf-8', errors='replace')
        if isinstance(stderr, bytes): stderr = stderr.decode('utf-8', errors='replace')
    stdout_path = folder/f'{label}.stdout.log'
    stderr_path = folder/f'{label}.stderr.log'
    stdout_path.write_text(stdout, encoding='utf-8')
    stderr_path.write_text(stderr, encoding='utf-8')
    passed = code == 0
    if passed and validate:
        try:
            validate(final_json(stdout))
        except (ValueError, TypeError, KeyError, AssertionError) as error:
            passed = False
            failure = f'Semantic validation failed: {type(error).__name__}: {error}'
    return {'id': label, 'command': command, 'cwd': str(ROOT), 'exit_code': code,
            'status': 'passed' if passed else 'failed', 'reason': failure,
            'elapsed_seconds': time.monotonic()-started,
            'stdout': reference(stdout_path), 'stderr': reference(stderr_path)}


def _verify_assets(value):
    assert value['status'] == 'pass' and value['files'] > 0


def _verify_tests(value):
    assert value['passed'] and value['tests'] > 0
    assert value['errors'] == 0 and value['failures'] == 0


def _verify_infer(value):
    assert value['native_rpc_calls'] == 0 and value['results']
    assert all(row['matches_saved_decoding'] for row in value['results'])


def _verify_gru(value):
    assert value['native_rpc_calls'] == 0 and value['samples']
    assert all('prediction' in row for row in value['samples'])


def _verify_native_boundary(value):
    assert value['native_runtime_verified'] is False and value['rpc_calls'] == 0


def acceptance(output=None, profile='research', timeout=180):
    configure()
    os.environ['TMPDIR'] = str(ROOT/'.tmp/phase3')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder = local(output or f'outputs/cloud/runs/{stamp}-phase3-acceptance')
    if folder.exists():
        raise FileExistsError('Acceptance output is immutable; choose a new directory')
    folder.mkdir(parents=True)
    dependencies = dependency_inventory(profile)
    missing = [key for key, row in dependencies.items() if not row['available']]
    checks = []
    def execute(label, arguments, validator=None):
        row = run_check(folder, label, arguments, timeout, validator)
        checks.append(row)
        print(json.dumps({'check': label, 'status': row['status'],
                          'exit_code': row['exit_code']}, ensure_ascii=False), flush=True)
    execute('reviewed-assets', ['tools/cloud/workspace.py', 'verify'], _verify_assets)
    execute('portable-contracts', ['tools/cloud/workspace.py', 'test'], _verify_tests)
    if dependencies['numpy']['available']:
        execute('numeric-saved-inference', ['tools/cloud/workspace.py', 'infer'], _verify_infer)
    if dependencies.get('torch', {}).get('available'):
        execute('gru-saved-inference', ['tools/cloud/workspace.py', 'infer-gru'], _verify_gru)
    elif profile == 'research':
        checks.append({'id': 'gru-saved-inference', 'status': 'not_run',
                       'reason': 'Torch import unavailable; install bootstrap.sh --ml'})
    execute('native-read-only-boundary', ['tools/cloud/workspace.py', 'native-check'], _verify_native_boundary)
    # Hash executable sources and declared inputs, retaining evidence independently
    # of historical reports. File presence alone never makes a check pass.
    source_paths = sorted({path for pattern in ('tools/cloud/*.py', 'tools/phase3*.py',
        'royale_phase3/*.py', 'tests_cloud/*.py', 'tests_phase3/*.py') for path in ROOT.glob(pattern)})
    sources = [reference(path) for path in source_paths]
    inputs = [reference('shared_assets/MANIFEST.json'), reference('requirements-codespaces.txt')]
    if profile == 'research':
        inputs.extend(reference(name) for name in ('requirements-codespaces-ml.txt',
                                                  'requirements-codespaces-research.txt'))
    passed = not missing and all(check['status'] == 'passed' for check in checks)
    report = {'schema': 'phase3-offline-acceptance.v1', 'created_utc': now(),
        'profile': profile, 'python': sys.version, 'executable': sys.executable,
        'offline_passed': passed, 'all_phase3_requirements_passed': False,
        'dependencies': dependencies, 'missing_dependencies': missing,
        'checks': checks, 'inputs': inputs, 'source_sha256': digest(sources), 'sources': sources,
        'native': {'status': 'not_verified', 'rpc_calls': 0, 'matches_run': 0,
                   'reason': '只执行只读主机条件检查；未启动 Android/Binder 或原生对局。'},
        'phase3_requirements': [{'item': i, 'status': 'not_verified', 'reason': reason}
            for i, reason in enumerate((
                '便携测试检查信息边界；新观测的原生完整运行和画面对齐仍待验收。',
                '动作接口可做离线检查；完整轮转与合格出牌数据门槛仍待验收。',
                '有保存 GRU 的离线推理证据；原生部署与消融仍待验收。',
                '时序契约可离线检查；官方独立时间真值与反应误差仍待验收。',
                '位置转换可离线检查；真实出牌位置指标仍待独立实验。',
                '离线保存模型推理不代表完整基线审计或原生配对评估完成。',
                '适配实现和合成契约不代表三个来源数据实验通过。',
                '未在此命令运行对手建模或未见对手实验。',
                '未在此命令训练或验收世界模型。',
                '未在此命令运行蒸馏或想象训练。'), 1)],
        'scope': '仅当前环境离线实现契约和保存模型推理；无新大规模训练、原生完成或策略提升结论。',
        'recovery': 'bash tools/cloud/bootstrap.sh --ml --research; rerun acceptance with a new --output directory'}
    write(folder/'acceptance.json', report)
    return report, folder/'acceptance.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='New repository-contained evidence directory')
    parser.add_argument('--profile', choices=('core', 'research'), default='research')
    parser.add_argument('--timeout', type=int, default=180, help='Seconds per bounded check (10..600)')
    args = parser.parse_args(argv)
    if not 10 <= args.timeout <= 600:
        parser.error('--timeout must be between 10 and 600 seconds')
    try:
        report, path = acceptance(args.output, args.profile, args.timeout)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({'report': str(path.relative_to(ROOT)),
        'offline_passed': report['offline_passed'],
        'all_phase3_requirements_passed': False,
        'missing_dependencies': report['missing_dependencies'],
        'native_status': 'not_verified'}, ensure_ascii=False), flush=True)
    return 0 if report['offline_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
