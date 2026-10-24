"""Codespaces 原生画面、人机操作、保存画面和引擎日志重演。"""
import argparse
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from royale_phase3.common import configure, local, read, reference, write


def source_folder(root, source_id):
    if not isinstance(source_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', source_id):
        raise ValueError('无效的公开对局编号')
    root = local(root)
    folder = local(root / source_id)
    folder.relative_to(root)
    if not (folder / 'plan.json').is_file():
        raise ValueError('找不到已导入的公开对局，请先选择 JSON 文件导入')
    return folder


def public_sources(root):
    root = local(root)
    values = []
    for path in sorted(root.glob('*/report.json'), reverse=True)[:200]:
        try:
            folder = source_folder(root, path.parent.name)
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            value = read(folder / 'report.json')
            values.append({**{k: value.get(k) for k in ('source_summary', 'assumptions', 'status', 'filename')},
                           'source_id': folder.name})
        except (ValueError, OSError, KeyError):
            continue
    return values


def import_source(data, root):
    from royale_phase2.public_replay import import_public_replay
    raw = None
    if 'raw_base64' in data:
        if not isinstance(data['raw_base64'], str):
            raise ValueError('JSON 文件编码必须为文本')
        raw = base64.b64decode(data['raw_base64'], validate=True)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('单局 JSON 最大为 2 MiB，请选择一局对局文件')
        payload = json.loads(raw.decode('utf-8-sig'))
    elif 'text' in data:
        if not isinstance(data['text'], str):
            raise ValueError('JSON 文件内容必须为文本')
        raw = data['text'].encode('utf-8')
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('单局 JSON 最大为 2 MiB，请选择一局对局文件')
        payload = json.loads(data['text'].lstrip('\ufeff'))
    elif 'payload' in data:
        payload = data['payload']
    else:
        raise ValueError('请选择一局 RoyaleAPI / IL_Replay JSON 文件')
    filename = str(data.get('filename', 'replay.json')).replace('\\', '/').rsplit('/', 1)[-1][:160]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ').lower()
    folder = local(root) / ('source-' + stamp + '-' + secrets.token_hex(3))
    result = import_public_replay(payload, folder, filename=filename, source_bytes=raw)
    return {**result, 'source_id': folder.name}


def import_archive(source, archive_root):
    """Register an immutable native source without copying or changing it."""
    from royale_phase2.cloud_replay import NativeReplaySource
    parsed = NativeReplaySource.load(source)
    directory = local(source)
    if directory.is_file(): directory = directory.parent
    if not (directory/'journal.json').exists() and (directory/'native/journal.json').is_file():
        directory = directory/'native'
    journal = read(directory/'journal.json')
    root = local(archive_root); root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ').lower()
    archive_id = 'import-'+stamp+'-'+secrets.token_hex(3)
    folder = root/archive_id; folder.mkdir()
    result = read(directory/'native_result.json') if (directory/'native_result.json').exists() else {}
    value = dict(schema='cloud-native-archive.v1', archive_id=archive_id, kind='import',
        created=datetime.now(timezone.utc).isoformat(), native_directory=directory.relative_to(ROOT).as_posix(),
        initial_tick=parsed.initialization_tick, end_tick=parsed.end_tick, owner=1, seed=journal['match'].get('rndSeed'),
        natural_terminal=bool(result.get('finalized')), frame_count=0, complete_frame_coverage=False,
        version_fingerprint=journal['version_fingerprint'], journal=reference(directory/'journal.json'),
        source_files=list(parsed.files),
        source='Original native journal/raw states; no historical pixels are invented')
    write(folder/'manifest.json', value)
    return value


def make_server(session, host='127.0.0.1', port=8772, *, source_root='outputs/cloud/public-replays'):
    from tools.cloud.workspace import allowed_origin
    page = local('tools/cloud/native.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def host_allowed(self):
            value = self.headers.get('Host', '')
            try:
                parsed = urlsplit('//'+value)
                if parsed.username or parsed.password or parsed.path:
                    return False
                if parsed.hostname in ('127.0.0.1', 'localhost') and parsed.port == self.server.server_port:
                    return True
                name = os.environ.get('CODESPACE_NAME')
                domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN')
                expected = f'{name}-{self.server.server_port}.{domain}' if name and domain else None
                return bool(os.environ.get('CODESPACES') == 'true' and expected and
                            parsed.hostname == expected and parsed.port in (None, 443))
            except ValueError:
                return False
        def respond(self, value, status=200, kind='application/json; charset=utf-8'):
            if not isinstance(value, bytes):
                value = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status); self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(value)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            self.end_headers()
            try: self.wfile.write(value)
            except (BrokenPipeError, ConnectionResetError): pass
        def do_GET(self):
            if not self.host_allowed(): return self.respond({'error': 'Host rejected'}, 403)
            route = urlsplit(self.path).path
            try:
                if route in ('/', '/native'):
                    return self.respond(page, kind='text/html; charset=utf-8')
                if route == '/api/native/state': return self.respond(session.public())
                if route == '/api/native/frame.png': return self.respond(session.latest_frame(), kind='image/png')
                if route == '/api/native/archives': return self.respond({'archives': session.archives()})
                if route == '/api/native/sources': return self.respond({'sources': public_sources(source_root)})
                if route == '/api/native/mirror/script.ps1':
                    code = local('tools/android_mirror.ps1').read_text(encoding='utf-8-sig')
                    name = os.environ.get('CODESPACE_NAME', '')
                    if not re.fullmatch(r'[A-Za-z0-9-]+', name):
                        raise ValueError('当前环境缺少 Codespace 名称，请在镜像脚本中指定 -Codespace')
                    return self.respond(code.replace('__ROYALE_CODESPACE__', name).encode('utf-8-sig'),
                                        kind='text/plain; charset=utf-8')
                parts = re.fullmatch(r'/api/native/archive/([a-z0-9._-]+)/(manifest|frames/(\d+)\.png|animation\.png)', route)
                if parts:
                    archive_id, resource, index = parts.groups()
                    if resource == 'manifest': return self.respond(session.manifest(archive_id))
                    if resource == 'animation.png':
                        folder = session._archive(archive_id)
                        path = folder/'animation.png'
                        if path.is_symlink(): raise ValueError('APNG符号链接不允许读取')
                        path.resolve().relative_to(folder.resolve())
                        if not path.is_file(): return self.respond({'error': '尚未导出APNG；使用export命令生成'}, 404)
                        if path.stat().st_size > 512*1024*1024: raise ValueError('APNG超过浏览器读取预算')
                        return self.respond(path.read_bytes(), kind='image/apng')
                    return self.respond(session.archive_frame(archive_id, int(index)), kind='image/png')
                return self.respond({'error': 'Unknown route'}, 404)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                return self.respond({'error': str(exc)}, 400)
            except Exception as exc:
                return self.respond({'error': f'{type(exc).__name__}: {exc}'}, 503)
        def do_POST(self):
            try:
                if not self.host_allowed(): return self.respond({'error': 'Host rejected'}, 403)
                origin = self.headers.get('Origin')
                if origin and not allowed_origin(origin, self.headers.get('Host'), self.server.server_port):
                    return self.respond({'error': 'Cross-origin request rejected'}, 403)
                if not secrets.compare_digest(self.headers.get('X-Native-Token', ''), session.csrf_token):
                    return self.respond({'error': 'Missing or invalid native session token'}, 403)
                if self.headers.get_content_type() != 'application/json':
                    return self.respond({'error': 'application/json required'}, 415)
                length = int(self.headers.get('Content-Length', '0'))
                route = urlsplit(self.path).path
                budget = 4 * 1024 * 1024 if route == '/api/native/source/import' else 16384
                if not 0 < length <= budget:
                    return self.respond({'error': f'JSON body must be 1..{budget} bytes'}, 400)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict): raise ValueError('JSON object required')
                if route == '/api/native/source/import':
                    return self.respond(import_source(data, source_root), 201)
                if route == '/api/native/source/open':
                    folder = source_folder(source_root, data.get('source_id'))
                    data = {'plan_path': (folder / 'plan.json').relative_to(ROOT).as_posix(),
                            'source_id': folder.name, 'owner': data.get('owner', 1)}
                commands = {'/api/native/'+name: name for name in ('reset', 'play', 'step', 'run', 'pause', 'save')}
                commands.update({'/api/native/replay/open': 'replay_open', '/api/native/replay/seek': 'replay_seek'})
                commands.update({'/api/native/source/open': 'source_open', '/api/native/source/seek': 'source_seek'})
                if route not in commands: return self.respond({'error': 'Unknown route'}, 404)
                job = session.submit(commands[route], data)
                return self.respond({'accepted': True, 'job_id': job, 'state': session.public()}, 202)
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond({'error': str(exc)}, 400)
            except Exception as exc:
                return self.respond({'error': f'{type(exc).__name__}: {exc}'}, 503)
    server = ThreadingHTTPServer((host, port), Handler); server.daemon_threads = True
    return server


def main(argv=None):
    configure()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='显式连接原生；不自动创建对局')
    serve.add_argument('--host', choices=['127.0.0.1'], default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8772)
    serve.add_argument('--connection', help='已核对当前Android进程的连接配置；默认读取ROYALE_NATIVE_CONNECTION')
    serve.add_argument('--output', help='新的日志目录；省略时自动按UTC时间建立，保留旧结果')
    serve.add_argument('--archives', default='outputs/cloud/native-archives')
    serve.add_argument('--sources', default='outputs/cloud/public-replays', help='已导入公开事件 JSON 的保存目录')
    serve.add_argument('--adb', default='.cache/native-tools/bin/adb')
    serve.add_argument('--adb-port', type=int, default=5038)
    serve.add_argument('--serial', default='127.0.0.1:15555')
    serve.add_argument('--root-shell', choices=['adbd', 'su0'], default='su0')
    serve.add_argument('--frame-wait', type=float, default=1.1)
    serve.add_argument('--capture-ticks', type=int, default=20, help='逻辑tick采样间隔；20=每游戏秒一次，1=逐tick；不保证实际画面刷新率')
    serve.add_argument('--max-tick', type=int, default=7200)
    serve.add_argument('--offline', action='store_true', help='只看已保存的真实画面；不校验运行资源、不接ADB/RPC')
    register = commands.add_parser('import', help='只读登记已有原生journal，历史帧不伪造')
    register.add_argument('--source', required=True)
    register.add_argument('--archives', default='outputs/cloud/native-archives')
    public = commands.add_parser('import-public', help='导入一局 RoyaleAPI / IL_Replay 事件 JSON；不启动 Android')
    public.add_argument('--source', required=True)
    public.add_argument('--sources', default='outputs/cloud/public-replays')
    export = commands.add_parser('export', help='核验真实PNG并无损导出APNG；不连接原生')
    export.add_argument('--archive', required=True, help='仓库内档案目录')
    args = parser.parse_args(argv)
    if args.command == 'import-public':
        source = local(args.source)
        if source.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('单局 JSON 最大为 2 MiB')
        result = import_source({'raw_base64': base64.b64encode(source.read_bytes()).decode('ascii'),
                                'filename': source.name}, args.sources)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command == 'import':
        print(json.dumps(import_archive(args.source, args.archives), ensure_ascii=False, indent=2)); return 0
    if args.command == 'export':
        from royale_phase2.cloud_frames import validate_frame_index, export_apng
        folder = local(args.archive)
        report = validate_frame_index(folder/'frames/index.jsonl', states_path=folder/'native/states-bound.jsonl')
        result = export_apng(folder/'frames/index.jsonl', folder/'animation.png',
                             states_path=folder/'native/states-bound.jsonl')
        write(folder/'export-report.json', {'validation': report, 'apng': result})
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    from royale_phase2.cloud_native import CloudNativeSession
    if args.output is None:
        args.output = 'outputs/cloud/native-browser/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    session = CloudNativeSession(args.output, archive_root=args.archives, connection=args.connection,
                adb=args.adb, adb_port=args.adb_port, serial=args.serial, root_shell=args.root_shell,
                frame_wait=args.frame_wait, capture_ticks=args.capture_ticks, max_tick=args.max_tick, offline=args.offline)
    server = None
    try:
        server = make_server(session, args.host, args.port, source_root=args.sources)
        print(json.dumps({'url': f'http://{args.host}:{server.server_port}/native',
                          'mode': 'saved_native_frames' if args.offline else 'actual_native_android',
                          'output': args.output}, ensure_ascii=False), flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server: server.server_close()
        session.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
