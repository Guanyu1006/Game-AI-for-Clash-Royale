"""一条命令准备已有 Android 并打开公开对局页面；不自动建局或播放。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from royale_phase3.common import configure, local, native_lock, read, write


def port_available(port):
    """Reserve nothing: a final bind in native_browser remains authoritative."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', port))


def inspect_vm(cache):
    """Return only a verified current VM or a verified stopped recovery disk."""
    from tools.cloud import software_android_vm as vm
    cache = vm.unlinked_local(cache)
    active = vm.unlinked_local(cache/'active-vm.json')
    if not active.is_file():
        raise ValueError('没有现成 VM 安装记录，不能自动新建 Android。请先按软件 Android 指南准备运行资源。')
    record = json.loads(active.read_text(encoding='utf-8'))
    original, disk = vm.checked_record(cache, record['attempt'])
    if original != record:
        raise ValueError('active-vm 与保存的进程记录不一致；保留现场，不自动重启。')
    identity = vm.process_identity(record['pid'])
    if identity is not None:
        if identity != record['process_starttime']:
            raise ValueError('记录的 PID 已由其他进程使用；不自动启动或结束该进程。')
        current = vm.require_record(cache)
        if current != record:
            raise ValueError('VM 运行记录在检查期间变化；请核对后重新运行。')
        status = vm.qmp(current, 'query-status')
        if status.get('result', {}).get('return', {}).get('status') != 'running':
            raise ValueError('已有 VM 未报告 running；不自动恢复、暂停或重启它，请检查 QMP 状态。')
        return {'status': 'running', 'record': record, 'mutable_disk': str(disk.relative_to(ROOT)),
                'qmp': status, 'action': 'reuse'}
    # This also refuses a still-active QMP socket, another disk user, a broken
    # recovery chain, symlinks and a missing/changed original installation disk.
    vm.record_stopped(record)
    image = json.loads(vm.unlinked_local(cache/'image.json').read_text(encoding='utf-8'))
    recovered, attempt = vm.resume_disk(cache, record['attempt'], image)
    return {'status': 'stopped', 'record': record, 'mutable_disk': str(recovered.relative_to(ROOT)),
            'resume_attempt': str(attempt.relative_to(ROOT)), 'action': 'resume_existing_installation'}


def _run_stage(folder, report, label, argv):
    """Log owned children; an interrupt never advances to the next stage."""
    stage = {'stage': label, 'command': argv, 'status': 'running', 'returncode': None}
    report['stages'].append(stage)
    write(folder/'report.json', report)
    print(json.dumps({'stage': label, 'status': 'running',
                      'logs': str(folder.relative_to(ROOT))+'/'+label+'.stdout.log'}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    process = None
    with (folder/(label+'.stdout.log')).open('wb') as stdout, (folder/(label+'.stderr.log')).open('wb') as stderr:
        try:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=stdout, stderr=stderr, start_new_session=True)
            stage['pid'] = process.pid
            heartbeat = started + 10
            while process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() >= heartbeat and process.poll() is None:
                    stage['elapsed_seconds'] = round(time.monotonic()-started, 1)
                    write(folder/'report.json', report)
                    print(json.dumps({'stage': label, 'status': 'running',
                                      'elapsed_seconds': stage['elapsed_seconds']}, ensure_ascii=False), flush=True)
                    heartbeat = time.monotonic() + 10
        except KeyboardInterrupt:
            stage['status'] = 'interrupted'
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass  # The owned child may have exited between poll and signal.
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    stage['child_shutdown_pending'] = True
                    print('已通知本命令启动的进程退出，仍在等待关闭；请查看本次日志，不要另开控制器。', flush=True)
            raise
        finally:
            if process is not None:
                stage['returncode'] = process.poll()
            stage['elapsed_seconds'] = round(time.monotonic()-started, 1)
            if stage['status'] == 'running':
                stage['status'] = 'completed' if stage['returncode'] == 0 else 'failed'
            write(folder/'report.json', report)
    if stage['returncode'] != 0:
        detail = ''
        if label == 'prepare' and (folder/'prepare/report.json').is_file():
            detail = '; '+str(read(folder/'prepare/report.json').get('error', '查看 prepare/report.json'))
        raise RuntimeError(f'{label} 退出码 {stage["returncode"]}{detail}；日志保留在 {folder.relative_to(ROOT)}')


def launch(args):
    folder = local(args.output)
    folder.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'royale-replay-app.v1', 'status': 'starting',
              'started_utc': datetime.now(timezone.utc).isoformat(), 'stages': [],
              'output': str(folder.relative_to(ROOT)), 'port': args.port,
              'automatic_playback': False, 'automatic_reset': False, 'vm_stopped_on_exit': False}
    write(folder/'report.json', report)
    python = [sys.executable, '-B']
    try:
        try:
            port_available(args.port)
        except OSError as exc:
            raise ValueError(f'端口 {args.port} 已被使用；已有服务保持运行。请在 Ports 打开它，或用 --port 指定其他空闲端口。') from exc
        with native_lock():
            pass  # Preparation and browser each acquire their own native lease.
        source_text = None
        if args.source:
            from royale_phase2.public_replay import MAX_SOURCE_BYTES, PublicReplayPlan
            source_path = local(args.source)
            if source_path.stat().st_size > MAX_SOURCE_BYTES:
                raise ValueError('请选择不超过 2 MiB 的单局事件 JSON。')
            PublicReplayPlan.load(source_path)  # Reject unsupported input before booting anything.
            source_text = source_path.read_text(encoding='utf-8')
        if args.connection:
            from royale_phase2.connection import load_connection
            settings = load_connection(args.connection)
            if settings is None:
                raise ValueError('--connection 需要显式的已准备连接文件。')
            connection = str(local(args.connection).relative_to(ROOT))
            report.update(preparation='explicit_connection_reused', connection=connection,
                          vm={'action': 'not_inspected_explicit_connection'})
        else:
            vm_state = inspect_vm(args.cache)
            report['vm'] = vm_state
            write(folder/'report.json', report)
            if vm_state['status'] == 'stopped':
                # Keep Unix QMP sockets short. The VM tool itself creates a new
                # timestamped attempt; all launcher logs stay in this app run.
                _run_stage(folder, report, 'vm-start', python+['tools/cloud/software_android_vm.py', 'start',
                    '--cache', args.cache, '--output', 'outputs/cloud/avm',
                    '--resume-attempt', vm_state['resume_attempt'], '--vcpus', '2',
                    '--timeout-multiplier', '20', '--tb-size-mib', '64',
                    '--memory-mib', str(args.memory_mib), '--display-fps', str(args.display_fps)])
                after_start = inspect_vm(args.cache)
                if after_start['status'] != 'running':
                    raise RuntimeError('恢复后 VM 未运行；不重试启动，请查看 vm-start 日志。')
                report['vm_after_start'] = after_start
            else:
                print('复用当前已核验的 Android VM，开始准备连接。', flush=True)
            prepare_argv = python+['tools/cloud/native_prepare.py',
                '--manifest', args.manifest, '--output', str((folder/'prepare').relative_to(ROOT)),
                '--stability-seconds', '15']
            if args.display_compat:
                prepare_argv.append('--display-compat')
            _run_stage(folder, report, 'prepare', prepare_argv)
            prepared = read(folder/'prepare/report.json')
            connection_path = folder/'prepare/connection.json'
            if prepared.get('status') != 'prepared' or not connection_path.is_file():
                raise RuntimeError('准备命令没有生成 prepared 连接；请检查 prepare/report.json，不启动页面。')
            connection = str(connection_path.relative_to(ROOT))
            report.update(preparation='prepared', connection=connection)
        if source_text is not None:
            from tools.cloud.native_browser import import_source
            imported = import_source({'text': source_text, 'filename': source_path.name}, args.sources)
            report['imported_source'] = imported
            print('公开对局已导入：'+imported['source_id']+'；进入页面选择它，点击“打开”和播放。', flush=True)
        url = f'http://127.0.0.1:{args.port}/native'
        report.update(status='serving', url=url)
        write(folder/'report.json', report)
        print(f'页面入口：{url}\n在 VS Code 的 Ports 面板打开 {args.port}。保持此终端运行；Ctrl+C 关闭本次页面。', flush=True)
        _run_stage(folder, report, 'browser', python+['tools/cloud/native_browser.py', 'serve',
            '--connection', connection, '--output', str((folder/'browser').relative_to(ROOT)),
            '--sources', args.sources, '--host', '127.0.0.1', '--port', str(args.port)])
        report['status'] = 'stopped'
        return 0
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        report['error'] = '用户中断；不继续准备或自动播放，保留 VM 与本次日志。'
        return 130
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        print(report['error'], file=sys.stderr, flush=True)
        return 2
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write(folder/'report.json', report)
        print('本次记录：'+str((folder/'report.json').relative_to(ROOT)), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', help='可选：导入一局 RoyaleAPI / IL_Replay JSON，不自动播放')
    parser.add_argument('--connection', help='显式复用已准备的 connection.json，跳过 VM / prepare')
    parser.add_argument('--cache', default='.cache/native-tools/android-recovery-001')
    parser.add_argument('--manifest', default='outputs/native_loop_20261020/local-runtime-version-001.json')
    parser.add_argument('--output', help='全新日志目录，默认按 UTC 时间创建')
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--sources', default='outputs/cloud/public-replays')
    parser.add_argument('--memory-mib', type=int, choices=(1536, 2048, 3072, 4096), default=3072,
                        help='仅新启动 VM 的 guest RAM，默认 3072 MiB；已有 VM 保持原设置')
    parser.add_argument('--display-fps', type=int, choices=(1, 5, 10, 15, 30), default=5,
                        help='仅下一次冷启动的 Android 显示帧率，默认 5；已有 VM 不重启')
    parser.add_argument('--display-compat', action=argparse.BooleanOptionalAction, default=True,
                        help='默认对选定 Android guest 应用画面兼容设置；--no-display-compat 可关闭')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port 须为 1..65535')
    if args.output is None:
        args.output = 'outputs/cloud/replay-app/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    configure()
    try:
        return launch(args)
    except (OSError, ValueError) as exc:
        print(f'无法创建本次独立日志目录：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
