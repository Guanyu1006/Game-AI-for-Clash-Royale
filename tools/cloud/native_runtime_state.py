"""只读核验现有原生 VM 身份与可恢复安装；不启动或重置运行时。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
