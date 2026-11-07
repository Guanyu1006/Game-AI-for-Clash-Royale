"""One private HTTP/CLI entry for registered research tasks and saved video."""
from __future__ import annotations

import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase3.workbench_config import DEFAULT_RUN_ROOT

HEALTH_SCHEMA = 'royale-research-health.v1'
APPLICATION = 'royale-research-workbench'
ID = r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'
MAX_BODY = 64 * 1024
IMPLEMENTATION_SOURCES = (
    'tools/cloud/research_app.py', 'tools/cloud/research_workbench.html',
    'royale_phase3/research.py', 'tools/cloud/research_worker.py',
    'royale_phase3/common.py', 'royale_phase3/actions.py',
    'royale_phase3/data_contract.py', 'royale_phase3/observation.py',
    'royale_phase3/learning.py',
    'tools/cloud/research_runtime.py', 'tools/native_loop.py',
    'royale_phase3/native_loop.py', 'tools/cloud/native_media.py',
    'tools/cloud/native_frame_media.py',
    'tools/cloud/native_runtime_state.py',
    'royale_phase3/workbench_config.py', 'research/system.json',
)


def project_path(value):
    path = Path(value)
    if '\\' in str(value) or (os.name != 'nt' and re.match(r'^[A-Za-z]:', str(value))):
        raise ValueError('需要当前仓库内的路径')
    path = (ROOT / path).resolve()
    path.relative_to(ROOT)
    return path


def process_identity(pid):
    """Read only process start time; never inspect environment or credentials."""
    try:
        raw = Path(f'/proc/{int(pid)}/stat').read_text()
        return raw[raw.rfind(')') + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def application_identity(run_root):
    # A live executor has already imported its service modules. Reusing it after
    # editing those files would mix old queue code with new child-process code.
    sources = hashlib.sha256()
    for name in IMPLEMENTATION_SOURCES:
        path = ROOT / name
        content = path.read_bytes() if path.is_file() else b'<missing>'
        sources.update(name.encode() + b'\0' + str(len(content)).encode() + b'\0' + content)
    return {'schema': HEALTH_SCHEMA, 'application': APPLICATION,
            'workspace_fingerprint': hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest(),
            'assets_sha256': sources.hexdigest(),
            'run_root': project_path(run_root).relative_to(ROOT).as_posix()}


def host_allowed(host, port):
    try:
        parsed = urlsplit('//' + host)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        if parsed.hostname in ('127.0.0.1', 'localhost') and parsed.port == port:
            return True
        name = os.environ.get('CODESPACE_NAME')
        domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN')
        expected = f'{name}-{port}.{domain}' if name and domain else None
        return bool(os.environ.get('CODESPACES') == 'true' and expected and
                    parsed.hostname == expected and parsed.port in (None, 443))
    except ValueError:
        return False


def origin_allowed(origin, host, port):
    if origin in ('http://' + host, 'https://' + host):
        return True
    name = os.environ.get('CODESPACE_NAME')
    domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN')
    expected = f'https://{name}-{port}.{domain}' if name and domain else None
    return bool(os.environ.get('CODESPACES') == 'true' and expected and origin == expected)


def byte_range(value, size):
    """Single HTTP byte range, including suffix ranges; reject multi-ranges."""
    if value is None:
        return 0, size - 1, False
    found = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not found or not any(found.groups()) or size <= 0:
        raise ValueError('无效的视频 Range')
    first, last = found.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError('无效的视频 Range')
        return max(0, size - suffix), size - 1, True
    start, end = int(first), int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError('视频 Range 超出文件范围')
    return start, min(end, size - 1), True


def registered_media(service, run_root, run_id, media_id):
    """Resolve only a media path explicitly registered by this run's service."""
    if not re.fullmatch(ID, run_id) or not re.fullmatch(ID, media_id):
        raise ValueError('无效的实验或媒体编号')
    record = service.get_run(run_id)
    entries = []
    media = record.get('media', [])
    if isinstance(media, dict):
        media = [media] if 'path' in media else list(media.values())
    entries.extend(item for item in media if isinstance(item, dict))
    for episode in record.get('episodes', []):
        value = episode.get('media')
        if isinstance(value, dict):
            entries.append({**value, 'id': value.get('id', str(episode.get('episode_id', episode.get('id', ''))))})
        elif isinstance(value, list):
            entries.extend(item for item in value if isinstance(item, dict))
    selected = next((item for item in entries if str(item.get('id', item.get('media_id', ''))) == media_id), None)
    if selected is None or not selected.get('path'):
        raise FileNotFoundError('本局没有登记可播放的原生录像')
    base = ROOT / run_root / run_id
    cursor = ROOT
    for part in base.relative_to(ROOT).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('实验目录不允许符号链接')
    base = project_path(base)
    path = ROOT / selected['path']
    # Reject every link, including an in-root link; the authorized file must be
    # the exact registered artifact rather than a replaceable path alias.
    relative = path.relative_to(ROOT)
    cursor = ROOT
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('媒体路径不允许符号链接')
    path = project_path(path)
    path.relative_to(base.resolve())
    if not path.is_file() or path.suffix.lower() not in ('.mp4', '.webm', '.m4v', '.mkv'):
        raise FileNotFoundError('已登记的录像文件不存在或格式不支持')
    return path


def make_server(service, host='127.0.0.1', port=8770, *, run_root=DEFAULT_RUN_ROOT):
    identity = application_identity(run_root)
    token = secrets.token_hex(24)
    health = {**identity, 'pid': os.getpid(), 'process_starttime': process_identity(os.getpid()),
              'instance_id': secrets.token_hex(16), 'status': 'ready'}
    page = (ROOT / 'tools/cloud/research_workbench.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not persist headers, session tokens, or request bodies.

        def send_headers(self, status, kind, length, extra=None):
            self.send_response(status)
            for key, value in {'Content-Type': kind, 'Content-Length': str(length),
                               'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                               'Referrer-Policy': 'same-origin', **(extra or {})}.items():
                self.send_header(key, value)
            self.end_headers()

        def respond(self, value, status=200, kind='application/json; charset=utf-8'):
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_headers(status, kind, len(raw))
            if self.command != 'HEAD':
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def media(self, run_id, media_id):
            path = registered_media(service, run_root, run_id, media_id)
            size = path.stat().st_size
            try:
                start, end, partial = byte_range(self.headers.get('Range'), size)
            except ValueError:
                return self.respond_range_error(size)
            self.send_headers(206 if partial else 200, mimetypes.guess_type(path.name)[0] or 'video/mp4',
                         end - start + 1, {'Accept-Ranges': 'bytes',
                         **({'Content-Range': f'bytes {start}-{end}/{size}'} if partial else {})})
            if self.command == 'HEAD':
                return
            try:
                with path.open('rb') as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        block = stream.read(min(65536, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def respond_range_error(self, size):
            self.send_headers(416, 'text/plain; charset=utf-8', 0, {'Content-Range': f'bytes */{size}', 'Accept-Ranges': 'bytes'})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not host_allowed(self.headers.get('Host', ''), self.server.server_port):
                return self.respond({'error': 'Host rejected'}, 403)
            route = unquote(urlsplit(self.path).path)
            try:
                if route in ('/', '/research'):
                    return self.respond(page, kind='text/html; charset=utf-8')
                if route == '/api/research/health':
                    return self.respond(health)
                if route == '/api/research/session':
                    return self.respond({'csrf_token': token})
                if route == '/api/research/catalog':
                    return self.respond(service.catalog())
                if route == '/api/research/runs':
                    return self.respond({'runs': service.list_runs()})
                match = re.fullmatch(r'/api/research/runs/(' + ID + r')(?:/media/(' + ID + r'))?', route)
                if match:
                    run_id, media_id = match.groups()
                    return self.media(run_id, media_id) if media_id else self.respond(service.get_run(run_id))
                return self.respond({'error': 'Unknown route'}, 404)
            except FileNotFoundError as exc:
                return self.respond({'error': str(exc)[:500]}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond({'error': str(exc)[:500]}, 400)
            except Exception as exc:
                return self.respond({'error': f'{type(exc).__name__}: {str(exc)[:400]}'}, 503)

        def do_POST(self):
            if not host_allowed(self.headers.get('Host', ''), self.server.server_port):
                return self.respond({'error': 'Host rejected'}, 403)
            if self.headers.get('Origin') and not origin_allowed(self.headers['Origin'], self.headers['Host'], self.server.server_port):
                return self.respond({'error': 'Cross-origin request rejected'}, 403)
            if not secrets.compare_digest(self.headers.get('X-Research-Token', ''), token):
                return self.respond({'error': 'Missing or invalid research session token'}, 403)
            try:
                if self.headers.get_content_type() != 'application/json':
                    return self.respond({'error': 'application/json required'}, 415)
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('请求需要明确的 Content-Length')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('请求 JSON 须为 1..65536 字节')
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError('请求须为 JSON 对象')
                route = unquote(urlsplit(self.path).path)
                if route == '/api/research/runs':
                    return self.respond(service.submit(value), 202)
                if route == '/api/research/runtime/stop':
                    return self.respond(service.stop_runtime())
                match = re.fullmatch(r'/api/research/runs/(' + ID + r')/cancel', route)
                if match:
                    return self.respond(service.cancel(match.group(1)), 202)
                return self.respond({'error': 'Unknown route'}, 404)
            except FileNotFoundError as exc:
                return self.respond({'error': str(exc)[:500]}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond({'error': str(exc)[:500]}, 400)
            except Exception as exc:
                return self.respond({'error': f'{type(exc).__name__}: {str(exc)[:400]}'}, 503)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def client_request(port, path, body=None, token=None):
    connection = http.client.HTTPConnection('127.0.0.1', port,
                                             timeout=60 if path.endswith('/runtime/stop') else 5)
    try:
        headers = {'Accept': 'application/json'}
        raw = None
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            headers.update({'Content-Type': 'application/json', 'X-Research-Token': token or ''})
        connection.request('POST' if body is not None else 'GET', path, body=raw, headers=headers)
        response = connection.getresponse()
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('服务响应超出读取预算')
        value = json.loads(raw)
        if response.status >= 400:
            raise ValueError(value.get('error', f'HTTP {response.status}'))
        return value
    finally:
        connection.close()


def probe_existing(port, run_root=DEFAULT_RUN_ROOT):
    try:
        value = client_request(port, '/api/research/health')
    except ConnectionRefusedError:
        return None
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
        raise ValueError(f'端口 {port} 已占用或服务无法核验；保留已有进程，请检查原服务终端或改用 --port。') from exc
    expected = application_identity(run_root)
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
        raise ValueError(f'端口 {port} 不是当前工作区同版本研究服务；已有服务保持运行。')
    if not isinstance(value.get('pid'), int) or not value.get('instance_id'):
        raise ValueError('研究服务缺少进程身份，不能自动复用')
    current = process_identity(value['pid'])
    if current is None or current != value.get('process_starttime'):
        raise ValueError('研究服务进程身份已变化，不能自动复用')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('serve', 'list', 'status', 'submit', 'cancel', 'stop-runtime'), default='serve')
    parser.add_argument('run_id', nargs='?')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--root', '--output', default=DEFAULT_RUN_ROOT, dest='run_root')
    parser.add_argument('--request', help='submit 的完整 JSON 配置；配置由任务服务统一校验')
    parser.add_argument('--config', help='submit 的仓库内 JSON 配置文件')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port 须为 1..65535')
    try:
        existing = probe_existing(args.port, args.run_root)
        if args.command != 'serve':
            if not existing:
                raise ValueError('研究服务尚未启动，请先运行 workspace.py research 并保持其终端运行')
            if args.command in ('status', 'cancel') and (not args.run_id or not re.fullmatch(ID, args.run_id)):
                raise ValueError('需要有效的 run_id')
            if args.command == 'list':
                result = client_request(args.port, '/api/research/runs')
            elif args.command == 'status':
                result = client_request(args.port, '/api/research/runs/' + args.run_id)
            else:
                token = client_request(args.port, '/api/research/session')['csrf_token']
                if args.command == 'submit':
                    if bool(args.request) == bool(args.config):
                        raise ValueError('submit 需要 --request 或 --config，二选一')
                    if args.config:
                        path = project_path(args.config)
                        if path.stat().st_size > MAX_BODY:
                            raise ValueError('配置文件超过 64 KiB')
                        value = json.loads(path.read_text(encoding='utf-8-sig'))
                    else:
                        value = json.loads(args.request)
                    result = client_request(args.port, '/api/research/runs', value, token)
                else:
                    path = '/api/research/runtime/stop' if args.command == 'stop-runtime' else '/api/research/runs/' + args.run_id + '/cancel'
                    result = client_request(args.port, path, {}, token)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if existing:
            print(json.dumps({'status': 'reused', 'url': f'http://127.0.0.1:{args.port}/research',
                              'pid': existing['pid']}, ensure_ascii=False), flush=True)
            return 0
        from royale_phase3.research import ResearchService
        service = ResearchService(root=args.run_root)
        server = None
        try:
            server = make_server(service, port=args.port, run_root=args.run_root)
            print(json.dumps({'status': 'serving', 'url': f'http://127.0.0.1:{server.server_port}/research',
                              'run_root': args.run_root, 'runtime_started': False}, ensure_ascii=False), flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server:
                server.server_close()
            service.close()
        return 0
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
