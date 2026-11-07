"""真实 Chromium 验证研究页面；隔离 HTTP 夹具，不训练、不连接 Android。

显式设置 ROYALE_RESEARCH_BROWSER_OUTPUT 为仓库内新目录才运行。
生成的视频是清楚标记的合成播放器夹具，绝不计入原生媒体验收。
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import threading
import traceback
import unittest
from unittest.mock import patch

from tools.cloud.research_app import ROOT, make_server


RUN_ID = 'run-20261107t000000000000z-abcdef01'


class ResearchBrowserFixture:
    """In-memory task API; no ResearchService, executor lease or native process."""

    def __init__(self, folder):
        self.folder = folder
        self.submissions = []
        self.cancellations = []
        self.stop_calls = 0
        self.record = None
        self.dataset = dict(id='native-run-002', label='本机原生数据（测试夹具）',
                            available=True, offline_only=False,
                            split_groups={'train': ['a', 'b', 'c'], 'validation': ['d'], 'test': []},
                            counts={'train': {'play': 85}, 'validation': {'play': 27}},
                            version_fingerprint_sha256='fixture-native-version')
        self.checkpoint = dict(path='models/browser-fixture/epoch-0002.json',
                               sha256='f' * 64, checkpoint_sha256='e' * 64,
                               version_fingerprint_sha256='fixture-native-version')
        self.defaults = {'dataset': 'native-run-002', 'epochs': 2, 'seed': 51001,
                         'hidden': 32, 'play_weight': 8, 'wall_seconds': 1800,
                         'max_tick': 7200}

    def catalog(self):
        return {'datasets': [self.dataset, {**self.dataset, 'id': 'portable-pilot',
                'label': '便携离线数据（测试夹具）', 'available': True, 'offline_only': True},
                {'id': 'missing-fixture', 'label': '缺失数据', 'available': False,
                 'error': 'Missing reviewed local resource'}],
                'models': [{'id': 'gru-c', 'label': 'GRU C · 序列行为克隆'}],
                'checkpoints': [self.checkpoint], 'defaults': deepcopy(self.defaults)}

    def list_runs(self):
        return [deepcopy(self.record)] if self.record else []

    def get_run(self, run_id):
        if not self.record or run_id != RUN_ID:
            raise KeyError(run_id)
        return deepcopy(self.record)

    def submit(self, payload):
        self.submissions.append(deepcopy(payload))
        self.record = dict(run_id=RUN_ID, status='queued', stage='queued', config=deepcopy(payload),
                           request=deepcopy(payload), created_at='2026-11-07T00:00:00+00:00',
                           updated_at='2026-11-07T00:00:00+00:00', stages=[], curves=[],
                           episodes=[], media=[], logs=[], native_execution_verified=False,
                           strength_improvement_claim=False)
        return self.get_run(RUN_ID)

    def cancel(self, run_id):
        self.cancellations.append(run_id)
        self.record.update(status='cancelled', stage='cancelled', cancel_requested=True)
        return self.get_run(run_id)

    def stop_runtime(self):
        self.stop_calls += 1
        return {'status': 'stopped', 'process_exited': True,
                'scope': 'Inert fixture: no Android exists in this test'}


def check_configured_defaults(page, fixture, report):
    """Exercise nonstandard configured budgets and missing-resource behavior."""
    from playwright.sync_api import expect
    defaults, dataset = deepcopy(fixture.defaults), deepcopy(fixture.dataset)
    try:
        fixture.defaults.update(mode='full', games=4, hidden=48, record_video=True,
                                record_game_indices=[3])
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('radio', name='完整实验', exact=True)).to_be_checked()
        expect(page.locator('#games')).to_have_value('4')
        expect(page.locator('#hidden')).to_have_value('48')
        expect(page.locator('#recording')).to_have_value('configured')
        assert page.locator('#recording option:checked').get_attribute('data-indices') == '[3]'
        report['checks'].append('catalog_applies_full_mode_four_games_and_preselected_index_three')

        page.locator('details.advanced summary').click()
        page.locator('#hidden').fill('64')
        page.locator('#games').select_option('2')
        expect(page.locator('#submitButton')).to_be_disabled()
        expect(page.locator('#modeHelp')).to_contain_text('录像局号超出当前局数')
        assert not fixture.submissions
        report['checks'].append('reducing_game_budget_disables_out_of_range_configured_recording_before_submit')

        page.locator('#recording').select_option('all')
        page.get_by_role('radio', name='评估', exact=True).check()
        expect(page.locator('#submitButton')).to_be_enabled()
        page.evaluate('() => researchRefresh(true)')
        expect(page.get_by_role('radio', name='评估', exact=True)).to_be_checked()
        expect(page.locator('#hidden')).to_have_value('64')
        expect(page.locator('#games')).to_have_value('2')
        expect(page.locator('#recording')).to_have_value('all')
        report['checks'].append('catalog_refresh_preserves_user_mode_hidden_game_budget_and_recording_choice')

        page.locator('#games').select_option('4')
        page.locator('#recording').select_option('configured')
        page.get_by_role('radio', name='完整实验', exact=True).check()
        expect(page.locator('#submitButton')).to_be_enabled()
        page.locator('#submitButton').click()
        expect(page.locator('#runStatus')).to_have_text('排队中')
        assert len(fixture.submissions) == 1
        submitted = fixture.submissions[0]
        assert submitted['mode'] == 'full' and submitted['games'] == 4
        assert submitted['hidden'] == 64 and submitted['record_game_indices'] == [3]
        report['configured_default_submission'] = deepcopy(submitted)
        report['checks'].append('configured_recording_indices_are_submitted_exactly_without_reset_to_first_game')

        fixture.record = None
        fixture.submissions.clear()
        fixture.dataset.update(available=False, error='fixture current metadata is missing')
        page.reload(wait_until='networkidle')
        expect(page.locator('#dataset')).to_have_value('native-run-002')
        expect(page.locator('#submitButton')).to_be_disabled()
        expect(page.locator('#datasetNote')).to_contain_text('fixture current metadata is missing')
        assert page.locator('#dataset option[value="portable-pilot"]').is_enabled()
        fixture.dataset['available'] = True
        page.evaluate('() => researchRefresh(true)')
        expect(page.locator('#submitButton')).to_be_enabled()
        fixture.dataset['available'] = False
        page.evaluate('() => researchRefresh(true)')
        expect(page.locator('#dataset')).to_have_value('native-run-002')
        expect(page.locator('#submitButton')).to_be_disabled()
        assert not fixture.submissions
        report['checks'].append('missing_default_or_selected_dataset_is_preserved_without_portable_fallback')
    finally:
        fixture.defaults = defaults
        fixture.dataset = dataset
        fixture.record = None
        fixture.submissions.clear()
    page.reload(wait_until='networkidle')
    expect(page.locator('#submitButton')).to_be_enabled()
    expect(page.locator('#nativeFields')).to_be_hidden()


def check_research_browser(output):
    folder = Path(output).resolve()
    folder.relative_to(ROOT)
    folder.mkdir(parents=True, exist_ok=False)
    fixture = ResearchBrowserFixture(folder)
    report = {'schema': 'research-workbench-browser-check.v1', 'status': 'running',
              'checks': [], 'page_errors': [], 'console_errors': [],
              'native_rpc_calls': 0, 'adb_calls': 0, 'vm_started': False,
              'training_jobs': 0, 'native_game_pixels_verified': False,
              'scope': 'Real Chromium + actual research HTTP handler + inert API fixture; '
                       'synthetic test-pattern WebM verifies browser playback only'}
    server = None
    thread = None

    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

    save()
    try:
        run_folder = folder / RUN_ID
        run_folder.mkdir()
        media = run_folder / 'synthetic-player-fixture.webm'
        encoder = ROOT / '.cache/native-tools/bin/ffmpeg'
        if not encoder.is_file():
            raise FileNotFoundError('仓库内 ffmpeg 不可用；没有下载新编码器或替代原生验收')
        command = [str(encoder), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                   '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10:duration=6',
                   '-c:v', 'libvpx-vp9', '-threads', '1', '-g', '10', '-b:v', '100k', str(media)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=60)
        (folder / 'fixture-encoding.stderr.log').write_bytes(result.stderr)
        report['synthetic_media_command'] = command
        if result.returncode:
            raise RuntimeError('合成播放器夹具编码失败，见 fixture-encoding.stderr.log')
        server = make_server(fixture, port=0, run_root=folder.relative_to(ROOT).as_posix())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {'PLAYWRIGHT_BROWSERS_PATH': str(ROOT / '.cache/cloud/playwright')}
        libraries = ROOT / '.cache/cloud/browser-libs/root'
        if libraries.is_dir():
            env['LD_LIBRARY_PATH'] = os.pathsep.join([str(libraries / 'usr/lib/x86_64-linux-gnu'),
                str(libraries / 'lib/x86_64-linux-gnu'), os.environ.get('LD_LIBRARY_PATH', '')])
        fonts = ROOT / '.cache/cloud/browser-libs/fonts.conf'
        if fonts.is_file():
            env['FONTCONFIG_FILE'] = str(fonts)
        with patch.dict(os.environ, env), patch('royale_phase2.probe.ProbeClient',
                side_effect=AssertionError('研究浏览器夹具不得连接 probe')):
            from playwright.sync_api import sync_playwright, expect
            with sync_playwright() as engine:
                browser = engine.chromium.launch(headless=True)
                report['browser_version'] = browser.version
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                page.on('console', lambda value: report['console_errors'].append(value.text)
                        if value.type == 'error' else None)
                base = f'http://127.0.0.1:{server.server_port}'
                page.goto(base + '/research', wait_until='networkidle')
                expect(page.locator('#connectionText')).to_have_text('工作台已连接')
                expect(page.locator('#submitButton')).to_be_enabled()
                expect(page.locator('#nativeFields')).to_be_hidden()
                assert not fixture.submissions and fixture.stop_calls == 0
                page.screenshot(path=str(folder / 'workbench-empty.png'), full_page=True)
                report['checks'].append('catalog_and_offline_home_without_native_commands')

                check_configured_defaults(page, fixture, report)

                page.locator('#dataset').select_option('portable-pilot')
                page.get_by_role('radio', name='评估', exact=True).check()
                expect(page.locator('#submitButton')).to_be_disabled()
                expect(page.locator('#datasetNote')).to_contain_text('不会自动替换来源')
                page.locator('#dataset').select_option('native-run-002')
                page.get_by_role('radio', name='完整实验', exact=True).check()
                expect(page.locator('#submitButton')).to_be_enabled()
                page.locator('#submitButton').click()
                expect(page.locator('#runStatus')).to_have_text('排队中')
                assert len(fixture.submissions) == 1
                submitted = fixture.submissions[0]
                assert submitted['mode'] == 'full' and submitted['games'] == 2
                assert submitted['record_video'] is True and submitted['record_game_indices'] == [0]
                assert 'checkpoint' not in submitted
                report['checks'].append('explicit_full_request_paired_games_preselected_recording_and_no_dataset_fallback')

                fixture.record.update(status='running', stage='train', curves=[
                    {'epoch': 1, 'training_loss': {'action': .6}, 'validation': {'action_nll': .4}},
                    {'epoch': 2, 'training_loss': {'action': .3}, 'validation': {'action_nll': .2}}])
                page.evaluate('researchRefresh()')
                expect(page.locator('#curveChart')).to_be_visible()
                expect(page.locator('#curveTable tbody tr')).to_have_count(2)
                fixture.record.update(status='failed', stage='failed',
                    error='fixture failure <img src=x onerror=alert(1)>',
                    logs=[{'path': 'fixture.stderr.log'}],
                    log_tails=[{'path': 'fixture.stderr.log', 'text': 'retained failure <script>alert(1)</script>'}])
                page.evaluate('researchRefresh()')
                expect(page.locator('#runError')).to_contain_text('fixture failure <img')
                page.locator('details.evidence summary').click()
                expect(page.locator('#runLogs')).to_contain_text('retained failure <script>')
                assert page.locator('#runLogs script, #runError img').count() == 0
                expect(page.locator('#cancelRun')).to_be_disabled()
                page.screenshot(path=str(folder / 'workbench-failure.png'), full_page=True)
                report['checks'].append('real_progress_curve_failure_log_and_untrusted_text_rendering')

                page.reload(wait_until='networkidle')
                expect(page.locator('#runSelect')).to_have_value(RUN_ID)
                expect(page.locator('#runStatus')).to_have_text('失败')
                assert len(fixture.submissions) == 1, '刷新不得重新提交实验'
                fixture.record.update(status='running', stage='evaluate', error=None)
                page.evaluate('researchRefresh()')
                expect(page.locator('#cancelRun')).to_be_enabled()
                page.locator('#cancelRun').click()
                expect(page.locator('#runStatus')).to_have_text('已停止')
                assert fixture.cancellations == [RUN_ID]
                report['checks'].append('refresh_restores_selected_run_and_explicit_cancel_does_not_resubmit')

                fixture.record.update(status='completed', stage='completed', cancel_requested=False,
                    checkpoint=fixture.checkpoint, offline_validation={'metrics': {
                        'action_nll': .2, 'event': {'recall': .75}}},
                    evaluation={'requested_games': 2, 'natural_terminal_games': 2,
                                'consumed': 9, 'wins': 0, 'losses': 1, 'draws': 1},
                    media=[{'id': 'video-000', 'path': media.relative_to(ROOT).as_posix(),
                            'status': 'complete', 'source': 'SYNTHETIC PLAYER FIXTURE — NOT NATIVE',
                            'episode_index': 0, 'native_owner': 0, 'width': 320, 'height': 180,
                            'duration_seconds': 6, 'mapping': 'Fixture intervals only; no native game timing',
                            'actions': [{'tick': 100, 'card_id': 1, 'owner': 0,
                                'card_consumed': True, 'video_seconds_range': [1.0, 1.5]},
                                {'tick': 120, 'card_id': 2, 'owner': 0, 'video_seconds': None},
                                {'tick': 131, 'card_id': 3, 'owner': 0,
                                 'anchor_tick': 130, 'tick_distance': 1,
                                 'video_seconds_range': [2., 2.5]}]}])
                page.evaluate('researchRefresh()')
                expect(page.locator('#metricGames')).to_have_text('2 / 2')
                expect(page.locator('#nativeVideo')).to_be_visible()
                page.wait_for_function('document.querySelector("#nativeVideo").readyState >= 1')
                duration = page.locator('#nativeVideo').evaluate('(video)=>video.duration')
                assert duration > 5
                buttons = page.locator('#mediaEvents button')
                expect(buttons.nth(0)).to_be_enabled()
                expect(buttons.nth(1)).to_be_disabled()
                buttons.nth(0).click()
                expect(page.locator('#eventHint')).to_contain_text('1.00–1.50 秒')
                page.wait_for_function('Math.abs(document.querySelector("#nativeVideo").currentTime-1)<.1')
                expect(buttons.nth(2)).to_have_text('定位邻近锚点')
                buttons.nth(2).click()
                expect(page.locator('#eventHint')).to_contain_text('与动作相差 1 tick')
                expect(page.locator('#eventHint')).to_contain_text('动作的精确视频时间未知')
                page.locator('#playbackSpeed').select_option('2')
                assert page.locator('#nativeVideo').evaluate('(video)=>video.playbackRate') == 2
                page.locator('#nativeVideo').evaluate('async video=>{await video.play();video.pause();video.currentTime=4.8;}')
                page.wait_for_function('Math.abs(document.querySelector("#nativeVideo").currentTime-4.8)<.1')
                assert len(fixture.submissions) == 1 and fixture.cancellations == [RUN_ID]
                report['checks'].append('real_html_video_decode_play_pause_rate_seek_and_verified_interval_only_events')
                page.locator('#stopRuntime').click()
                expect(page.locator('#notice')).to_contain_text('运行时已停止')
                page.locator('#nativeVideo').evaluate('video=>{video.currentTime=.3;}')
                page.wait_for_function('Math.abs(document.querySelector("#nativeVideo").currentTime-.3)<.1')
                assert fixture.stop_calls == 1
                assert page.locator('#nativeVideo').evaluate('(video)=>video.duration') == duration
                page.screenshot(path=str(folder / 'workbench-player.png'), full_page=True)
                report['checks'].append('inert_runtime_stop_keeps_file_player_read_only_and_seekable')

                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), '窄屏不应横向溢出'
                page.screenshot(path=str(folder / 'workbench-mobile.png'), full_page=True)
                assert not report['page_errors'], report['page_errors']
                assert not report['console_errors'], report['console_errors']
                report['submissions'] = fixture.submissions
                report['checks'].append('responsive_layout_no_page_or_console_errors')
                browser.close()
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=5)
        save()
    return report


class ResearchWorkbenchBrowserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ROYALE_RESEARCH_BROWSER_OUTPUT'),
                         '显式指定仓库内新目录才运行 Chromium；默认不增加浏览器负载')
    def test_workbench_with_isolated_service(self):
        report = check_research_browser(os.environ['ROYALE_RESEARCH_BROWSER_OUTPUT'])
        self.assertEqual(report['status'], 'passed', report.get('traceback', report))


if __name__ == '__main__':
    unittest.main()
