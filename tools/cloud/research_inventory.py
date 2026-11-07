"""生成作者源码、静态 Python 依赖与外部资源清单；不执行项目模块。"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'royale-research-dependency-inventory.v1'
CODE_SUFFIXES = {'.py', '.ps1', '.cmd', '.bat', '.sh', '.c', '.cc', '.cpp', '.cxx',
                 '.h', '.hpp', '.java', '.js', '.cjs', '.mjs', '.ts', '.tsx', '.jsx',
                 '.html', '.css', '.cmake'}
EXCLUDED_ROOTS = {'.git', '.codex', '.env', '.cache', '.tmp', 'outputs', 'data', 'models',
                  'third_party', 'shared_assets', 'node_modules', '__pycache__'}
GENERATED_RESEARCH = {'data', 'models', 'runtime', 'vm', 'runs', 'media', 'replays'}
CORE_DISTRIBUTIONS = ('numpy', 'torch', 'pyarrow', 'gymnasium', 'playwright', 'opencv-python',
                      'pillow', 'ultralytics', 'onnx', 'onnxruntime', 'androguard', 'capstone',
                      'matplotlib', 'PyYAML', 'pypdf', 'setuptools')
IMPORT_DISTRIBUTIONS = {'cv2': 'opencv-python', 'PIL': 'pillow', 'yaml': 'PyYAML',
                        **{name: name for name in CORE_DISTRIBUTIONS if name.isidentifier()}}
LAYER_ROLES = {
    'workbench': '当前科研入口、任务队列、HTTP 页面、配置与独立 worker',
    'research': '阶段三观测、belief、训练、部署和原生闭环算法',
    'native_platform': '阶段二固定版本协议、身份、可见性和动作消费；当前研究仍复用',
    'cloud_runtime': '项目内 Android/工具准备、运行时身份、媒体和云端适配',
    'approximate_baseline': '保留的 royale_ai/royale_lab 近似基线，验收独立',
    'historical_snapshot': 'baseline/ 冻结历史快照，不是当前科研默认入口',
    'windows_entrypoints': '保留的本地 Windows 启动与配置入口',
    'tests': '各层契约和回归检查；测试通过不等于原生对局验收',
    'tooling': '构建、转换、研究审计及其它作者工具',
    'native_probe_sources': '作者维护的阶段二/三原生或 Java 探针与支持源码',
    'other_source': '其它作者源码及页面资产',
}


def sensitive(parts):
    return any(part.lower() in {'auth.json', '.env', '.ssh', 'id_rsa', 'id_ed25519'}
               or part.lower().startswith('.env.')
               or 'credential' in part.lower() or 'secret' in part.lower()
               for part in parts)


def contained(root, value):
    """Reject absolute, non-normalized, sensitive or symlinked paths before I/O."""
    text = str(value)
    parts = PurePosixPath(text).parts
    if (not text or PurePosixPath(text).as_posix() != text
            or '\\' in text or PureWindowsPath(text).drive or text.startswith('/')
            or not parts or any(part in {'.', '..'} for part in text.split('/'))
            or sensitive(parts)):
        raise ValueError('Expected a normalized non-sensitive repository-relative path')
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('Symlinked inventory paths are not followed')
    target.resolve().relative_to(root.resolve())
    return target


def authored(path):
    parts = PurePosixPath(path).parts
    if not parts or sensitive(parts):
        return False
    if any(part in EXCLUDED_ROOTS or part.startswith(('.venv', 'venv', '.tox')) for part in parts):
        return False
    if parts[0] == 'research' and len(parts) > 1 and parts[1] in GENERATED_RESEARCH:
        return False
    return True


def git_paths(root):
    raw = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                         cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    return sorted(set(raw.decode('utf-8', errors='surrogateescape').split('\0')) - {''})


def layer_for(path):
    first = path.split('/')[0]
    if first == 'baseline': return 'historical_snapshot'
    if first.startswith('tests'): return 'tests'
    if path.endswith(('.ps1', '.cmd', '.bat')): return 'windows_entrypoints'
    if (path.startswith('tools/cloud/research_') or path in {
            'tools/cloud/workspace.py', 'royale_phase3/research.py', 'royale_phase3/workbench_config.py'}):
        return 'workbench'
    if first == 'royale_phase3': return 'research'
    if first == 'royale_phase2': return 'native_platform'
    if first in {'royale_ai', 'royale_lab'}: return 'approximate_baseline'
    if path.startswith('tools/cloud/'): return 'cloud_runtime'
    if first == 'tools': return 'tooling'
    if first in {'phase2', 'phase3'}: return 'native_probe_sources'
    return 'other_source'


def file_record(root, path):
    target = contained(root, path)
    if not target.is_file():
        raise FileNotFoundError(path)
    if target.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Inventory text file exceeds the 16 MiB source/manifest bound')
    raw = target.read_bytes()
    return {'path': path, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, raw


def module_name(path):
    parts = list(PurePosixPath(path).with_suffix('').parts)
    if parts[-1] == '__init__': parts.pop()
    return '.'.join(parts)


def analyze_python(sources):
    """Parse bytes only. Even imports behind conditions are retained as static edges."""
    modules = {module_name(path): path for path in sources if path.endswith('.py')}
    namespaces = {'.'.join(name.split('.')[:i]) for name in modules
                  for i in range(1, len(name.split('.')) + 1)}
    by_stem = defaultdict(list)
    for name, path in modules.items(): by_stem[Path(path).stem].append(name)
    edges, parsed, external, stdlib, errors = set(), [], defaultdict(set), defaultdict(set), []

    def resolve(base, names, scope, relative=False):
        candidates = [scope + '.' + base, base] if scope else [base]
        for candidate in candidates:
            targets = {modules[candidate]} if candidate in modules else set()
            targets.update(modules[candidate + '.' + name] for name in names
                           if candidate + '.' + name in modules)
            if targets or candidate in namespaces:
                return 'local', sorted(targets)
        # Directly executed tools often import a sibling by its bare name.
        if base.split('.')[0] in sys.stdlib_module_names:
            return 'stdlib', []
        if '.' not in base and len(by_stem.get(base, [])) == 1:
            return 'local_script_alias', [modules[by_stem[base][0]]]
        if relative: return 'unresolved_relative', []
        return ('stdlib' if base.split('.')[0] in sys.stdlib_module_names else 'external'), []

    for name, path in sorted(modules.items()):
        row = {'path': path, 'module': name, 'imports': [], 'dynamic_imports': [], 'process_calls': []}
        try:
            tree = ast.parse(sources[path], filename=path)
        except (SyntaxError, UnicodeError, ValueError) as exc:
            errors.append({'path': path, 'line': getattr(exc, 'lineno', None), 'error': type(exc).__name__})
            row['parse_status'] = 'failed'; parsed.append(row); continue
        row['parse_status'] = 'parsed'
        package = name if path.endswith('/__init__.py') else name.rpartition('.')[0]
        scope = 'baseline.mvp_v0_1' if path.startswith('baseline/mvp_v0_1/') else ''
        process_aliases, dynamic_aliases, module_aliases = set(), set(), {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module == 'subprocess':
                process_aliases.update(alias.asname or alias.name for alias in node.names
                                       if alias.name in {'Popen', 'run', 'call', 'check_call', 'check_output'})
            elif isinstance(node, ast.ImportFrom) and node.module == 'importlib':
                dynamic_aliases.update(alias.asname or alias.name for alias in node.names
                                       if alias.name == 'import_module')
        for node in sorted(ast.walk(tree), key=lambda item: getattr(item, 'lineno', 0)):
            imports = []
            if isinstance(node, ast.Import):
                imports = [(alias.name, [], False) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    parts = package.split('.') if package else []
                    prefix = parts[:len(parts) - node.level + 1] if node.level <= len(parts) else []
                    base = '.'.join(prefix + ([base] if base else []))
                imports = [(base, [alias.name for alias in node.names], bool(node.level))]
            for base, names, relative in imports:
                kind, targets = resolve(base, names, scope, relative)
                row['imports'].append({'line': node.lineno, 'module': base, 'names': names,
                                       'classification': kind, 'local_targets': targets})
                edges.update((path, target) for target in targets if target != path)
                if kind == 'external': external[base.split('.')[0]].add(path)
                if kind == 'stdlib': stdlib[base.split('.')[0]].add(path)
            if not isinstance(node, ast.Call): continue
            call = node.func
            label = call.id if isinstance(call, ast.Name) else ''
            if isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name):
                label = module_aliases.get(call.value.id, call.value.id) + '.' + call.attr
            if label in {'__import__', 'builtins.__import__', 'importlib.import_module'} or label in dynamic_aliases:
                entry = {'line': node.lineno, 'call': label, 'module_literal': None}
                if node.args and isinstance(node.args[0], ast.Constant):
                    value = node.args[0].value
                    if isinstance(value, str) and re.fullmatch(r'[A-Za-z_.][A-Za-z0-9_.]*', value):
                        entry['module_literal'] = value
                row['dynamic_imports'].append(entry)
            if label.startswith('subprocess.') or label in process_aliases or label in {
                    'os.system', 'os.execv', 'os.execve', 'os.execvp', 'os.execvpe', 'os.posix_spawn'}:
                # Never dump command strings or arbitrary argument literals.
                targets = sorted({value.value for argument in node.args for value in ast.walk(argument)
                                  if isinstance(value, ast.Constant) and isinstance(value.value, str)
                                  and value.value in sources})
                row['process_calls'].append({'line': node.lineno, 'call': label,
                                              'literal_author_script_targets': targets})
        parsed.append(row)
    return {'modules': parsed, 'local_edges': [{'from': left, 'to': right} for left, right in sorted(edges)],
            'external_import_roots': [{'name': name, 'distribution_hint': IMPORT_DISTRIBUTIONS.get(name),
                                       'imported_by': sorted(paths)} for name, paths in sorted(external.items())],
            'stdlib_import_roots': [{'name': name, 'importer_count': len(paths)} for name, paths in sorted(stdlib.items())],
            'parse_errors': errors}


def requirement_pin(value):
    """Keep package constraints, never URL credentials, options or arbitrary text."""
    line = value.strip()
    if not line or line.startswith('#'): return None
    if line.startswith('-'):
        return {'kind': 'option_or_include', 'option': line.split()[0], 'value_omitted': True}
    match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(\[[A-Za-z0-9_,.-]+\])?\s*'
                         r'((?:(?:===|==|~=|!=|>=|<=|>|<)\s*[A-Za-z0-9*_.+!-]+\s*,?\s*)*)'
                         r'(?:;[^\r\n]*)?(?:\s+#.*)?', line)
    if not match:
        # Hashes still identify the complete source file; unparsed content is not exported.
        return {'kind': 'unparsed_or_url_requirement', 'content_omitted': True}
    name, extras, specifier = match.groups()
    return {'kind': 'package', 'name': name, 'extras': extras or '',
            'specifier': re.sub(r'\s+', '', specifier), 'has_environment_marker': ';' in line,
            'exact_pin': specifier.strip().startswith('==') and '*' not in specifier}


def dependency_profiles(root, paths):
    profiles = []
    for path in paths:
        item = PurePosixPath(path)
        requirement = ((len(item.parts) == 1 and (item.name.startswith('requirements')
                                                 and item.suffix in {'.txt', '.lock'}))
                       or (item.parts[0] == 'tools' and 'requirements' in item.name and item.suffix == '.txt'))
        if not requirement and item.name != 'pyproject.toml': continue
        record, raw = file_record(root, path)
        if requirement:
            record['kind'] = 'requirements'
            record['pins'] = [pin for line in raw.decode('utf-8-sig').splitlines()
                              if (pin := requirement_pin(line)) is not None]
            record['scope'] = 'historical_windows_independent' if path == 'requirements.lock' else 'separate_profile'
        else:
            value = tomllib.loads(raw.decode('utf-8-sig')); project = value.get('project', {})
            record.update(kind='pyproject', python=project.get('requires-python'),
                          dependencies=[requirement_pin(item) for item in project.get('dependencies', [])],
                          optional_dependencies={key: [requirement_pin(item) for item in values]
                              for key, values in project.get('optional-dependencies', {}).items()},
                          build_dependencies=[requirement_pin(item) for item in value.get('build-system', {}).get('requires', [])],
                          scripts=project.get('scripts', {}))
        profiles.append(record)
    return profiles


def stat_reference(root, path):
    result = {'path': path, 'exists': False, 'kind': 'missing', 'bytes': None, 'content_hash_verified': False}
    try:
        target = contained(root, path)
        result['exists'] = target.exists()
        if target.is_file():
            result.update(kind='file', bytes=target.stat().st_size)
        elif target.is_dir(): result['kind'] = 'directory'
    except ValueError:
        return {'path': '<unsafe path omitted>', 'exists': False, 'status': 'unsafe_path',
                'content_hash_verified': False}
    return result


def configured_resources(root):
    config_path = 'research/system.json'
    if not (root / config_path).is_file():
        return {'system': {'path': config_path, 'status': 'missing'},
                'native_assets': {'status': 'unavailable_no_current_config', 'assets': []}}
    record, raw = file_record(root, config_path)
    config = json.loads(raw)
    paths = config.get('paths', {})
    record.update(schema=config.get('schema'), paths={key: stat_reference(root, value)
                    for key, value in paths.items() if isinstance(value, str)},
                  datasets=[{'name': name, 'offline_only': entry.get('offline_only'),
                             **stat_reference(root, entry['path'])}
                            for name, entry in sorted(config.get('datasets', {}).items())],
                  reference_checkpoints=[stat_reference(root, path)
                                         for path in config.get('reference_checkpoints', [])])
    manifest_path = paths.get('native_manifest')
    if not manifest_path or not contained(root, manifest_path).is_file():
        return {'system': record, 'native_assets': {'path': manifest_path, 'status': 'missing',
                 'historical_fallback_used': False, 'assets': []}}
    manifest_record, manifest_raw = file_record(root, manifest_path)
    manifest = json.loads(manifest_raw)
    assets = []
    for asset in manifest.get('assets', []):
        entry = stat_reference(root, asset['path'])
        digest = asset.get('sha256')
        entry.update(declared_sha256=digest if isinstance(digest, str) and re.fullmatch(r'[0-9a-fA-F]{64}', digest) else None,
                     role=asset.get('role'), declared_bytes=asset.get('bytes'))
        entry['size_matches_declared'] = (entry['bytes'] == entry['declared_bytes']) if (
            type(entry.get('bytes')) is int and type(entry['declared_bytes']) is int
            and entry['declared_bytes'] >= 0) else None
        assets.append(entry)
    manifest_record.update(status='listed_stat_only', fingerprint=manifest.get('fingerprint'),
                           abi=manifest.get('abi'), ticks_per_second=manifest.get('ticks_per_second'),
                           asset_count=len(assets), existing_files=sum(item.get('kind') == 'file' for item in assets),
                           declared_bytes=sum(item.get('declared_bytes') or 0 for item in assets),
                           declared_size_count=sum(type(item.get('declared_bytes')) is int for item in assets),
                           known_stat_bytes=sum(item.get('bytes') or 0 for item in assets),
                           size_totals_scope='declared_bytes sums only declared sizes; zero can mean declarations are absent. known_stat_bytes sums actual stat sizes of present regular files; missing or unsafe assets have unknown sizes.',
                           roles=dict(Counter(item.get('role') for item in assets)), assets=assets,
                           historical_fallback_used=False,
                           verification='Manifest bytes hashed; external asset existence/size only. Declared SHA256 values are not reverified, and runtime compatibility is not accepted.')
    return {'system': record, 'native_assets': manifest_record}


def non_python_roles(root):
    rows = [
        ('QEMU/Android/ARM bridge', '原生执行与 guest 软件图形；配置锁与 VM 磁盘分别管理',
         ['configs/native/software_android.lock.json', 'tools/cloud/software_android_vm.py']),
        ('ADB', '显式设备连接、只读身份核验与连续帧传输', ['.cache/native-tools/bin/adb']),
        ('FFmpeg/ffprobe', '主机 VP9/WebM 编码、PTS、完整解码和 seek 核验',
         ['.cache/native-tools/bin/ffmpeg', '.cache/native-tools/bin/ffprobe']),
        ('APK/libg/probe/content', '当前 native manifest 逐项锁定的外部原生资源，源代码包不含完整素材', []),
        ('Chromium', '可选 Playwright 浏览器验收；浏览器与系统共享库需独立准备',
         ['requirements-codespaces-browser.txt']),
        ('PowerShell/Windows', '保留本机启动器与独立 Windows 依赖锁',
         ['run_play.ps1', 'run_platform.ps1', 'run.ps1', 'phase2.ps1', 'requirements.lock']),
        ('JDK/APK tools/native build', '可选 Java/C++/APK 探针构建，使用项目内固定工具资源',
         ['tools/cloud/build_native_apk.py', 'requirements-codespaces-native-tools.json']),
        ('Community C++ engine', '历史近似基线需要匹配 Python ABI 的本地扩展；不是当前原生 libg',
         ['royale_lab/backend.py']),
    ]
    return [{'component': name, 'role': role, 'references': [stat_reference(root, path) for path in refs],
             'installed_or_execution_verified': False} for name, role, refs in rows]


def build_inventory(root=ROOT):
    root = Path(root).resolve()
    paths = [path for path in git_paths(root) if authored(path)]
    files, sources, skipped = [], {}, []
    for path in paths:
        if PurePosixPath(path).suffix.lower() not in CODE_SUFFIXES: continue
        try:
            record, raw = file_record(root, path)
        except (ValueError, OSError):
            skipped.append({'path': path, 'status': 'missing_or_symlinked'}); continue
        record.update(layer=layer_for(path), language_suffix=PurePosixPath(path).suffix.lower())
        files.append(record)
        if path.endswith('.py'): sources[path] = raw
    python = analyze_python(sources)
    distributions = []
    for name in CORE_DISTRIBUTIONS:
        try: installed = version(name)
        except PackageNotFoundError: installed = None
        distributions.append({'distribution': name, 'version': installed})
    entrypoints = [
        ('current_workbench', 'tools/cloud/workspace.py', '.venv-cloud/bin/python tools/cloud/workspace.py research'),
        ('workbench_http', 'tools/cloud/research_app.py', None),
        ('offline_worker', 'tools/cloud/research_worker.py', None),
        ('native_loop', 'tools/native_loop.py', None),
        ('legacy_cloud_page', 'tools/cloud/workspace.py', '.venv-cloud/bin/python tools/cloud/workspace.py serve'),
        ('legacy_native_viewer', 'tools/cloud/replay_app.py', None),
        ('windows_human_demo', 'run_play.ps1', None),
        ('windows_native_platform', 'run_platform.ps1', None),
        ('windows_baseline', 'run.ps1', None),
    ]
    return {
        'schema': SCHEMA, 'generated_utc': datetime.now(timezone.utc).isoformat(),
        'method': {'file_discovery': 'git ls-files --cached --others --exclude-standard -z',
                   'code_analysis': 'AST only; project modules are never imported or executed',
                   'installed_versions': 'Selected distribution metadata versions only; no environment variables read',
                   'exclusions': sorted(EXCLUDED_ROOTS) + ['.venv*/', 'venv*/', '.tox*/',
                       'research/{data,models,runtime,vm,runs,media,replays}/', 'credential/secret/auth files'],
                   'limitations': ['All AST import branches are included; this overapproximates actual startup dependencies.',
                       'Dynamic imports, subprocesses, runtime sys.path changes, compiled extensions and external tools mean this is not a complete execution closure.',
                       'Observed dynamic imports/process calls are listed without executing them or exporting arbitrary command arguments.',
                       'Stdlib classification describes this interpreter; platform-conditional dependencies may not be installed.',
                       'External asset stat/declared hashes do not prove actual content hashes, installation, compatibility, game fidelity or match acceptance.']},
        'summary': {'source_files': len(files), 'python_files': len(sources),
                    'local_import_edges': len(python['local_edges']), 'parse_errors': len(python['parse_errors']),
                    'file_counts_by_layer': dict(Counter(item['layer'] for item in files))},
        'layers': [{'name': name, 'role': role} for name, role in LAYER_ROLES.items()],
        'entrypoints': [{'name': name, **stat_reference(root, path), 'command': command}
                        for name, path, command in entrypoints],
        'source_files': files, 'python': python,
        'dependency_profiles': dependency_profiles(root, paths),
        'selected_installed_distributions': distributions,
        'non_python_components': non_python_roles(root), **configured_resources(root),
        'windows_lock_policy': {'path': 'requirements.lock',
             'policy': 'Preserve historical Windows lock independently; do not overwrite or merge it with Linux cloud profiles.'},
        'source_checkout_policy': 'Current tools require the repository root, research/system.json and separately prepared data/runtime assets. This inventory does not promise an independently deployable wheel.',
        'skipped_code_paths': skipped,
    }


def save_inventory(root, output, document, refresh=False):
    root = Path(root).resolve(); target = contained(root, output)
    if any(part in {'.git', '.codex', '.cache', '.env'} or part.startswith(('.venv', 'venv', '.tox'))
           for part in PurePosixPath(output).parts):
        raise ValueError('Inventory output cannot target environment, cache or private control directories')
    if target.suffix != '.json': raise ValueError('Inventory output must be a repository-local .json file')
    encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if target.exists():
        if target.read_bytes() == encoded: return 'unchanged'
        if not refresh:
            raise FileExistsError('Output already exists with different content; choose a new path or --refresh')
        try: prior = json.loads(target.read_bytes())
        except (ValueError, UnicodeError): prior = None
        if not isinstance(prior, dict) or prior.get('schema') != SCHEMA:
            raise ValueError('--refresh only replaces this tool’s own inventory schema')
        target.write_bytes(encoded)
        return 'refreshed'
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream: stream.write(encoded)
    return 'created'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='仓库内新 JSON；不覆盖不同内容')
    parser.add_argument('--refresh', action='store_true', help='仅允许刷新本工具 schema 的旧清单')
    args = parser.parse_args(argv)
    try:
        # Validate the destination before scanning any source or metadata.
        contained(ROOT, args.output)
        document = build_inventory(ROOT)
        status = save_inventory(ROOT, args.output, document, args.refresh)
        print(json.dumps({'status': status, 'output': args.output, 'summary': document['summary']},
                         ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__, 'message': str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
