"""有预算的原生浏览器服务验收；仅显式命令连接已运行的本地 HTTP 服务。

POST mutation 只提交一次，之后仅轮询缓存的 job 状态。失败不会自动重发、
重置或切换近似引擎。工具不启动 VM、ADB、训练或原生服务。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from royale_phase3.common import append, local, read, reference, write


def now():
    return datetime.now(timezone.utc).isoformat()


def public_copy(value):
    if isinstance(value, dict):
        return {k: public_copy(v) for k, v in value.items() if k not in ('csrf_token', 'token')}
    if isinstance(value, list):
        return [public_copy(v) for v in value]
    return value


class NativeVisualClient:
    """No network I/O in construction; every request has a finite timeout."""
    def __init__(self, base, output, *, wall_seconds=600, job_seconds=240, http_seconds=10,
                 poll_seconds=1, max_frames=10000):
        parsed = urlsplit(base)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('只允许 http://127.0.0.1:<port> 本地原生服务')
        try:
            self.port = parsed.port or 80
        except ValueError as exc:
            raise ValueError('无效本地服务端口') from exc
        for value, low, high, name in ((wall_seconds, 10, 7200, 'wall_seconds'),
                                      (job_seconds, 1, 1800, 'job_seconds'),
                                      (http_seconds, .1, 60, 'http_seconds'),
                                      (poll_seconds, .05, 10, 'poll_seconds')):
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f'{name} 超出预算范围')
        if type(max_frames) is not int or not 3 <= max_frames <= 10000:
            raise ValueError('max_frames 必须为3..10000')
        self.folder = local(output)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.wall_seconds, self.job_seconds = wall_seconds, job_seconds
        self.http_seconds, self.poll_seconds = http_seconds, poll_seconds
        self.max_frames = max_frames
        self.started = time.monotonic()
        self.deadline = self.started + wall_seconds
        self.next_progress = self.started
        self.token = None
        self.last_state = None
        self.commands = []
        self.request_count = 0
        self.report = {'schema': 'cloud-native-visual-http-check.v1', 'status': 'running', 'started': now(),
                       'base': f'http://127.0.0.1:{self.port}', 'wall_seconds_budget': wall_seconds,
                       'attempted_experiments': 1, 'completed_experiments': 0, 'failed_experiments': 0,
                       'mutations_retried': 0, 'direct_probe_calls': 0, 'vm_started': False,
                       'training_started': False, 'pixel_art_fidelity_verified': False, 'commands': self.commands}
        self.save()

    def save(self):
        self.report['elapsed_seconds'] = time.monotonic() - self.started
        self.report['http_requests'] = self.request_count
        write(self.folder / 'report.json', public_copy(self.report))

    def budget(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('原生画面验收墙钟预算耗尽；没有自动重试')
        return remaining

    def progress(self, stage, **details):
        if time.monotonic() >= self.next_progress:
            print(json.dumps({'stage': stage, 'elapsed_seconds': round(time.monotonic() - self.started),
                              **details}, ensure_ascii=False), flush=True)
            self.next_progress = time.monotonic() + 10
            self.save()

    def request(self, path, data=None, *, binary=False):
        timeout = min(self.http_seconds, self.budget())
        method = 'GET' if data is None else 'POST'
        headers = {'Accept': 'image/png' if binary else 'application/json', 'Cache-Control': 'no-store'}
        body = None
        if data is not None:
            if not self.token:
                raise RuntimeError('须先读取本地会话token')
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers.update({'Content-Type': 'application/json', 'X-Native-Token': self.token})
        self.request_count += 1
        record = {'index': self.request_count, 'at': now(), 'method': method, 'path': path,
                  'body': data, 'retry': False}
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        started = time.monotonic()
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            limit = 32 * 1024 * 1024 if binary else 8 * 1024 * 1024
            payload = response.read(limit + 1)
            record.update(status=response.status, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            if len(payload) > limit:
                raise RuntimeError('HTTP响应超过有限读取预算')
            if not 200 <= response.status < 300:
                raise RuntimeError(f'HTTP {response.status}: {payload[:1000].decode(errors="replace")}')
            if binary:
                if response.getheader('Content-Type', '').split(';')[0] != 'image/png':
                    raise RuntimeError('原生帧未返回image/png')
                return payload
            value = json.loads(payload)
            record['response'] = public_copy(value)
            return value
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            if data is not None:
                record['mutation_outcome'] = 'unknown_or_rejected; never automatically reissued'
            raise
        finally:
            connection.close()
            record['elapsed_seconds'] = time.monotonic() - started
            append(self.folder / 'http.jsonl', record)

    def state(self):
        state = self.request('/api/native/state')
        if not isinstance(state, dict):
            raise RuntimeError('原生状态响应不是对象')
        self.token = state.get('csrf_token', self.token)
        self.last_state = state
        return state

    def ready(self):
        state = self.state()
        if not state.get('ready') or state.get('offline') or state.get('busy') or state.get('error'):
            raise RuntimeError('原生服务尚未空闲就绪：' + str(public_copy(state)))
        return state

    def command(self, name, data=None):
        """One POST only; transport failures do not trigger a second mutation."""
        stage = {'command': name, 'data': data or {}, 'at': now(), 'status': 'submitting', 'submissions': 1}
        self.commands.append(stage)
        self.save()
        try:
            result = self.request('/api/native/' + name, data or {})
            if result.get('accepted') is not True or not isinstance(result.get('job_id'), str):
                raise RuntimeError('服务未返回可轮询的accepted/job_id')
            job_id = result['job_id']
            stage.update(job_id=job_id, status='queued')
            end = min(self.deadline, time.monotonic() + self.job_seconds)
            while True:
                state = self.state()
                job = state.get('jobs', {}).get(job_id)
                if not isinstance(job, dict):
                    raise RuntimeError('已接受的job_id不在状态中；禁止重发原操作')
                self.progress(name, job_id=job_id, tick=state.get('tick'), job_status=job.get('status'))
                if job.get('status') in ('done', 'failed', 'error'):
                    stage.update(status=job['status'], result=public_copy(job), tick=state.get('tick'))
                    if job['status'] != 'done':
                        raise RuntimeError('原生操作失败：' + str(job))
                    self.save()
                    return state
                if time.monotonic() >= end:
                    raise TimeoutError('等待已提交操作超时；结果未知，未重发')
                time.sleep(min(self.poll_seconds, max(.001, end - time.monotonic())))
        except Exception as exc:
            stage.update(status='failed_or_unknown', error=f'{type(exc).__name__}: {exc}')
            self.save()
            raise

    def archive_manifest(self, archive_id):
        if not isinstance(archive_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}', archive_id):
            raise ValueError('无效档案编号')
        value = self.request(f'/api/native/archive/{archive_id}/manifest')
        if value.get('archive_id') != archive_id:
            raise RuntimeError('档案编号与服务响应不一致')
        return value

    def audit_archive(self, archive_id, *, require_terminal=False):
        """Hash/CRC every recorded PNG; decode a bounded representative sample."""
        from royale_phase2.cloud_frames import _chunks, frame_info, validate_frame_index
        manifest = self.archive_manifest(archive_id)
        if require_terminal and (not manifest.get('natural_terminal') or not manifest.get('complete_frame_coverage')):
            raise RuntimeError('未自然终局或采样时轴不完整，不能验收完整录制')
        frame_ref = manifest.get('frame_index')
        if not isinstance(frame_ref, dict) or not frame_ref.get('sha256'):
            raise RuntimeError('档案未seal或缺少帧索引哈希；请显式save')
        index = local(frame_ref['path'])
        folder = index.parent.parent
        folder.relative_to(ROOT)
        if folder.name != archive_id or index != folder / 'frames/index.jsonl':
            raise RuntimeError('帧索引不在所选档案内')
        local_manifest = read(folder / 'manifest.json')
        if {k: v for k, v in manifest.items() if k not in ('frames', 'animation_url')} != local_manifest:
            raise RuntimeError('HTTP档案和磁盘manifest不同')
        references = [frame_ref, manifest.get('journal', {}), *manifest.get('source_files', [])]
        if not manifest.get('source_files'):
            raise RuntimeError('档案缺少已seal的原始状态/来源哈希')
        for item in references:
            self.budget()
            path = local(item['path'])
            path.relative_to(folder)
            if reference(path)['sha256'] != item.get('sha256'):
                raise RuntimeError('档案文件哈希不匹配：' + item['path'])
        rows = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
        if not 3 <= len(rows) <= self.max_frames:
            raise RuntimeError(f'帧数{len(rows)}不满足3..{self.max_frames}有限验收范围')
        if len(rows) != manifest.get('frame_count'):
            raise RuntimeError('帧索引数量与manifest不一致')
        http_rows = manifest.get('frames', [])
        if len(http_rows) != len(rows) or any({**row, 'index': i} != http_rows[i] for i, row in enumerate(rows)):
            raise RuntimeError('HTTP帧清单与磁盘索引不一致')
        hashes = set()
        for number, row in enumerate(rows):
            self.budget()
            path = local(row['path'])
            path.relative_to(folder)
            if path.stat().st_size > 32 * 1024 * 1024:
                raise RuntimeError('单张PNG超过预算')
            png = path.read_bytes()
            if hashlib.sha256(png).hexdigest() != row.get('sha256'):
                raise RuntimeError('保存PNG哈希不匹配')
            _chunks(png)
            hashes.add(row['sha256'])
            self.progress('archive-integrity', checked=number + 1, total=len(rows))
        chosen = sorted({round(i * (len(rows) - 1) / min(7, len(rows) - 1))
                         for i in range(min(8, len(rows)))})
        # Decode at most eight real images; every recorded image was CRC/hash checked above.
        selected = []
        sample_dir = self.folder / ('frames-' + archive_id)
        sample_dir.mkdir(exist_ok=False)
        for number in chosen:
            self.budget()
            row = rows[number]
            png = self.request(f'/api/native/archive/{archive_id}/frames/{number}.png', binary=True)
            info = frame_info(png)
            if info['sha256'] != row['sha256']:
                raise RuntimeError('HTTP PNG与档案哈希不同')
            (sample_dir / f'{number:06d}.png').write_bytes(png)
            selected.append({'index': number, 'tick': row['tick'], **info})
        nonblack = [r for r in selected if r['non_black'] is True]
        if len({r['pixel_sha256'] for r in nonblack}) < 3:
            raise RuntimeError('不足三张不同的非黑实际PNG；不能验收原生游戏画面')
        sample_index = sample_dir / 'sample-index.jsonl'
        for number in chosen:
            append(sample_index, rows[number])
        bindings = validate_frame_index(sample_index, states_path=folder / 'native/states-bound.jsonl')
        self.budget()
        result = {'status': 'pass', 'archive_id': archive_id, 'manifest': reference(folder / 'manifest.json'),
                  'frame_index': frame_ref, 'all_pngs_crc_hash_checked': len(rows),
                  'distinct_png_hashes': len(hashes), 'pixel_sample_frames': selected,
                  'sample_state_bindings': bindings, 'natural_terminal': manifest.get('natural_terminal'),
                  'complete_frame_coverage': manifest.get('complete_frame_coverage'),
                  'capture_ticks': manifest.get('capture_ticks'), 'full_20hz_frames': manifest.get('full_20hz_frames'),
                  'scope': 'Every PNG hash/CRC; at most eight decoded, HTTP-matched state-bound frames; no independent art/presentation fidelity claim'}
        write(self.folder / ('archive-audit-' + archive_id + '.json'), result)
        return result

    def smoke(self, seed):
        self.ready()
        state = self.command('reset', {'owner': 1, 'seed': seed, 'opponent': 'cycle'})
        ordinary = [c for c in state.get('hand', []) if c.get('affordable') is True
                    and 26000000 <= c.get('card_id', 0) < 27000000]
        if not ordinary:
            raise RuntimeError('初始化手牌没有可负担普通兵；未猜牌或擅自重新建局')
        card = min(ordinary, key=lambda c: (c['cost'], c['slot']))
        state = self.command('play', {'generation': state['generation'], 'expected_tick': state['tick'],
                   'slot': card['slot'], 'card_id': card['card_id'], 'x': 3500, 'y': 21000})
        if state.get('last_action', {}).get('card_consumed') is not True:
            raise RuntimeError('人工下牌未确认原生卡消费，不能只把登记当执行成功')
        self.report['human_play'] = public_copy(state['last_action'])
        state = self.command('step', {'ticks': 100})
        state = self.command('save')
        self.report['archive_id'] = state['archive_id']
        self.report['visual_audit'] = self.audit_archive(state['archive_id'])
        self.report['scope'] = 'New rendered match, browser API human action consumption, 100 ticks and real saved PNG integrity; not a complete game'

    def record(self, ticks_per_step):
        state = self.ready()
        if state.get('mode') != 'live' or state.get('tick') is None or state.get('terminal'):
            raise RuntimeError('record仅继续当前未终止live局；不会自动新建或重复对局')
        self.report['archive_id'] = state['archive_id']
        state = self.command('run', {'ticks_per_step': ticks_per_step})
        reserve = min(60, max(10, self.wall_seconds * .15))
        while True:
            state = self.state()
            self.progress('record', tick=state.get('tick'), terminal=state.get('terminal'), running=state.get('running'))
            if state.get('error'):
                raise RuntimeError('原生连续推进失败：' + state['error'])
            if state.get('terminal'):
                break
            if state.get('truncated') or state.get('running') is not True:
                raise RuntimeError('原生局停止但未自然终局，不计完整录制')
            if self.deadline - time.monotonic() <= reserve:
                self.command('pause')
                self.command('save')
                raise TimeoutError('录制墙钟预算不足，已显式暂停保存；该局计失败，不重新开局')
            time.sleep(min(self.poll_seconds, self.budget()))
        state = self.command('save')
        self.report['visual_audit'] = self.audit_archive(state['archive_id'], require_terminal=True)
        self.report['scope'] = 'Natural terminal and complete saved sampling timeline; sampling interval remains explicit'

    def replay_report(self, state, label):
        manifest = self.archive_manifest(state['archive_id'])
        folder = local(manifest['native_directory']).parent
        report_path = folder / 'replay-verification.json'
        value = read(report_path)
        write(self.folder / (label + '-verification.json'), value)
        if (value.get('status') != 'pass' or not value.get('saved_state_trajectory_verified')
                or not value.get('all_saved_states_equal') or not value.get('all_registration_ticks_equal')
                or value.get('available_consumption_feedback_equal') is not True
                or not value.get('executed_feedback_complete') or value.get('terminal_equal') is not True):
            raise RuntimeError('原生重演原始状态／登记／消费／终局核对未全部通过')
        self.report[label] = {'report': reference(report_path), 'source_saved_states': value['source_saved_states'],
                              'compared_saved_states': value['compared_saved_states'],
                              'natural_terminal_verified': value.get('natural_terminal_verified')}
        return value

    def replay(self, archive_id):
        self.ready()
        source = self.archive_manifest(archive_id)
        begin, end = source.get('initial_tick'), source.get('end_tick')
        if type(begin) is not int or type(end) is not int or not 0 <= begin < end:
            raise RuntimeError('来源档案缺少完整起止tick')
        if source.get('natural_terminal') is not True:
            raise RuntimeError('来源未自然终局；不能将部分轨迹当完整原生重演验收')
        state = self.command('replay/open', {'archive_id': archive_id})
        if state.get('tick') != begin:
            raise RuntimeError('重演未停在来源初始化tick')
        state = self.command('replay/seek', {'tick': end})
        state = self.command('save')
        self.replay_report(state, 'forward')
        first = state['archive_id']
        generation = state.get('generation')
        state = self.command('replay/seek', {'tick': begin})
        if state.get('tick') != begin or state.get('generation') == generation:
            raise RuntimeError('向后seek未创建新的原生generation并重置至初始化tick')
        state = self.command('replay/seek', {'tick': end})
        state = self.command('save')
        self.replay_report(state, 'backward_then_forward')
        self.report.update(source_archive_id=archive_id, first_replay_archive_id=first,
                           archive_id=state['archive_id'], backward_seek_created_generation=True,
                           replay_raw_states_verified=True, replay_terminal_verified=True)
        self.report['scope'] = 'Two native re-executions compare every saved raw state, original registration/feedback and terminal; seek does not invent unsaved historical pixels'

    def cleanup_pause(self):
        """One new cancellation command, never a retry of an unknown mutation."""
        original = self.deadline
        self.deadline = max(original, time.monotonic() + min(30, self.http_seconds + 20))
        try:
            state = self.state()
            if state.get('running') or state.get('busy'):
                self.command('pause')
                self.report['failure_cleanup'] = 'One explicit pause completed; failed experiment not retried'
            else:
                self.report['failure_cleanup'] = 'Service already paused/idle; no new mutation'
        except Exception as exc:
            self.report['failure_cleanup'] = f'Pause outcome unknown or service unavailable: {type(exc).__name__}: {exc}'
        finally:
            self.deadline = original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('smoke', 'record', 'replay'):
        command = commands.add_parser(name)
        command.add_argument('--base', default='http://127.0.0.1:8772')
        command.add_argument('--output', required=True, help='新的仓库内证据目录；拒绝覆盖')
        command.add_argument('--wall-seconds', type=float, default=600 if name == 'smoke' else 1800)
        command.add_argument('--job-seconds', type=float, default=240)
        command.add_argument('--http-seconds', type=float, default=10)
        command.add_argument('--poll-seconds', type=float, default=1)
        if name == 'smoke':
            command.add_argument('--seed', type=int, default=610024)
        if name == 'record':
            command.add_argument('--ticks-per-step', type=int, choices=range(1, 101), default=100)
        if name == 'replay':
            command.add_argument('--archive', required=True)
    args = parser.parse_args(argv)
    client = NativeVisualClient(args.base, args.output, wall_seconds=args.wall_seconds,
                               job_seconds=args.job_seconds, http_seconds=args.http_seconds,
                               poll_seconds=args.poll_seconds)
    client.report['command'] = args.command
    try:
        if args.command == 'smoke':
            if not 0 <= args.seed <= 2147483647:
                raise ValueError('seed必须为0..2147483647')
            client.smoke(args.seed)
        elif args.command == 'record':
            client.record(args.ticks_per_step)
        else:
            client.replay(args.archive)
        client.report.update(status='pass', completed_experiments=1)
    except BaseException as exc:
        client.report.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'fail',
                             failed_experiments=1, error=f'{type(exc).__name__}: {exc}')
        client.cleanup_pause()
    finally:
        client.report['finished'] = now()
        client.save()
    print(json.dumps(public_copy(client.report), ensure_ascii=False, indent=2), flush=True)
    return 0 if client.report['status'] == 'pass' else 2


if __name__ == '__main__':
    raise SystemExit(main())
