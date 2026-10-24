"""真实 Chromium 验证文件导入 UI；原生会话是不会执行的 HTTP 夹具。"""
import hashlib
import json
import os
from pathlib import Path
import threading
import traceback
import unittest
from unittest.mock import patch

from tests_cloud.test_cloud_native_http import FakeNativeSession
from tools.cloud.native_browser import ROOT, make_server


def check_public_replay_browser(output, source=None):
    folder = Path(output).resolve()
    folder.relative_to(ROOT)
    folder.mkdir(parents=True, exist_ok=False)
    if source is None:
        candidates = [ROOT / 'outputs/cloud/public-replay-sample-20261024-001' / name
                      for name in ('sample-row-15.json', 'sample-ordinary-row.json')]
        source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        raise FileNotFoundError('缺少 92 事件真实来源单行 JSON；没有用合成文件替代本次浏览器验收')
    source = Path(source).resolve(); source.relative_to(ROOT)
    report = {'schema': 'public-replay-browser-check.v1', 'status': 'running',
              'checks': [], 'page_errors': [], 'console_errors': [],
              'source': str(source.relative_to(ROOT)),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'native_rpc_calls': 0, 'adb_calls': 0, 'vm_started': False,
              'native_game_pixels_verified': False,
              'scope': 'Real Chromium + actual public-source converter + inert fake native HTTP queue only'}
    def save():
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    save()
    session = FakeNativeSession(folder)
    base_public = session.public
    state_overlay = {}
    session.public = lambda: {**base_public(), **state_overlay}
    sources = folder / 'sources'
    server = make_server(session, port=0, source_root=sources)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {'PLAYWRIGHT_BROWSERS_PATH': str(ROOT / '.cache/cloud/playwright'),
           'CODESPACE_NAME': 'mirror-browser-fixture'}
    libs = ROOT / '.cache/cloud/browser-libs/root'
    if libs.is_dir():
        env['LD_LIBRARY_PATH'] = os.pathsep.join([str(libs/'usr/lib/x86_64-linux-gnu'),
                                                 str(libs/'lib/x86_64-linux-gnu'),
                                                 os.environ.get('LD_LIBRARY_PATH', '')])
    fonts = ROOT / '.cache/cloud/browser-libs/fonts.conf'
    if fonts.is_file(): env['FONTCONFIG_FILE'] = str(fonts)
    try:
        with patch.dict(os.environ, env), patch('royale_phase2.probe.ProbeClient',
                                               side_effect=AssertionError('浏览器夹具不得连接原生 probe')):
            from playwright.sync_api import sync_playwright, expect
            base = f'http://127.0.0.1:{server.server_port}'
            with sync_playwright() as engine:
                browser = engine.chromium.launch(headless=True)
                report['browser_version'] = browser.version
                page = browser.new_page(viewport={'width': 1440, 'height': 1200})
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                page.on('console', lambda message: report['console_errors'].append(message.text)
                        if message.type == 'error' else None)
                page.goto(base+'/native', wait_until='networkidle')
                expect(page.locator('#sourceTab')).to_have_class('active')
                expect(page.locator('#sourceControls')).to_be_visible()
                expect(page.locator('#liveControls')).to_be_hidden()
                expect(page.locator('#manualHelp')).to_be_hidden()
                page.locator('#liveTab').click()
                expect(page.locator('#manualHelp')).to_be_visible()
                page.locator('#replayTab').click()
                expect(page.locator('#manualHelp')).to_be_hidden()
                page.locator('#sourceTab').click()
                expect(page.locator('#manualHelp')).to_be_hidden()
                expect(page.locator('#connectionBadge')).to_have_text('页面服务已连接')
                for name in ('sourceRun', 'sourcePause', 'sourceStep', 'sourceSeek', 'openSource'):
                    expect(page.locator('#'+name)).to_be_disabled()
                assert not session.submissions, '页面打开不应提交任何原生命令'
                report['checks'].append('default_source_tab_and_no_premature_native_controls')
                page.screenshot(path=str(folder/'source-default.png'), full_page=True)

                page.locator('#sourceFile').set_input_files(str(source))
                expect(page.locator('#importSource')).to_be_enabled()
                page.locator('#importSource').click()
                expect(page.locator('#message')).to_contain_text('导入完成', timeout=30000)
                expect(page.locator('#sourceSummary')).to_contain_text('92 次动作')
                expect(page.locator('#sourceSelect option')).to_have_count(1)
                source_id = page.locator('#sourceSelect').input_value()
                catalog = page.request.get(base+'/api/native/sources').json()['sources']
                assert len(catalog) == 1 and catalog[0]['source_id'] == source_id
                assert catalog[0]['source_summary']['events'] == 92
                assert (sources/source_id/'raw.json').read_bytes() == source.read_bytes()
                assert not session.submissions, '导入及转换不得执行原生建局'
                for name in ('sourceRun', 'sourcePause', 'sourceStep', 'sourceSeek'):
                    expect(page.locator('#'+name)).to_be_disabled()
                expect(page.locator('#openSource')).to_be_enabled()
                report['checks'].append('real_92_event_json_import_catalog_and_raw_byte_preservation')

                page.locator('#reloadSources').click()
                expect(page.locator('#sourceSelect')).to_have_value(source_id)
                expect(page.locator('#sourceSummary')).to_contain_text('92 次动作')
                page.screenshot(path=str(folder/'source-imported.png'), full_page=True)
                page.locator('#openSource').click()
                page.wait_for_function("document.querySelector('#message').textContent.includes('请求已返回')")
                assert session.submissions == [('source_open', {
                    'plan_path': (sources/source_id/'plan.json').relative_to(ROOT).as_posix(),
                    'source_id': source_id, 'owner': 1})], session.submissions
                for name in ('sourceRun', 'sourceStep', 'sourceSeek'):
                    expect(page.locator('#'+name)).to_be_disabled()
                report['checks'].append('open_source_submits_exactly_one_saved_plan_with_no_reset_or_play')

                state_overlay.update(mode='source', running=False,
                    source_replay={'valid': True, 'source_actions': 92, 'end_tick': 7200,
                                   'initialization_tick': 90})
                page.evaluate('poll()')
                for name in ('sourceRun', 'sourceStep', 'sourceSeek'):
                    expect(page.locator('#'+name)).to_be_enabled()
                state_overlay.update(error='fixture: native result is unknown',
                    source_replay={'valid': False, 'source_actions': 92, 'end_tick': 7200,
                                   'initialization_tick': 90})
                page.evaluate('poll()')
                expect(page.locator('#message')).to_contain_text('native result is unknown')
                for name in ('sourceRun', 'sourceStep', 'sourceSeek'):
                    expect(page.locator('#'+name)).to_be_disabled()
                assert len(session.submissions) == 1, '失败态不得自动重试、重置或继续执行'
                page.screenshot(path=str(folder/'source-failed.png'), full_page=True)
                report['checks'].append('source_failure_disables_advancement_and_does_not_retry')
                state_overlay.clear()
                state_overlay.update(archive_id='archive-fixture', tick=670)
                page.evaluate('poll()')

                page.locator('#saveButton').click()
                expect(page.locator('#savedInfo')).to_have_text('保存请求已排队，尚未完成。')
                expect(page.locator('#saveButton')).to_be_disabled()
                assert session.submissions[-1] == ('save', {}) and len(session.submissions) == 2
                save_text = page.locator('#savedInfo').inner_text()
                assert session.csrf_token not in save_text and 'csrf_token' not in save_text
                assert '{' not in save_text and len(save_text) < 160
                assert page.locator('#savedInfo').bounding_box()['height'] < 100
                page.screenshot(path=str(folder/'save-queued.png'), full_page=True)
                state_overlay['jobs'] = {
                    'job-1': {'command': 'save', 'status': 'done', 'tick': 999},
                    'job-2': {'command': 'save', 'status': 'running'}}
                page.evaluate('poll()')
                expect(page.locator('#savedInfo')).to_have_text('正在保存，等待任务完成…')
                state_overlay['jobs']['job-2'] = {'command': 'save', 'status': 'done', 'tick': 670}
                page.evaluate('poll()')
                expect(page.locator('#savedInfo')).to_have_text('已保存至 tick 670 · 归档 archive-fixture')
                expect(page.locator('#saveButton')).to_be_enabled()
                page.screenshot(path=str(folder/'save-completed.png'), full_page=True)
                report['checks'].append('async_save_202_tracks_own_job_without_response_json_or_token_in_ui')

                state_overlay.pop('jobs')
                page.evaluate('poll()')
                page.locator('#saveButton').click()
                expect(page.locator('#savedInfo')).to_have_text('保存请求已排队，尚未完成。')
                state_overlay['jobs'] = {'job-3': {'command': 'save', 'status': 'failed',
                    'error': {'csrf_token': session.csrf_token, 'detail': 'fixture failure'}}}
                page.evaluate('poll()')
                expect(page.locator('#savedInfo')).to_have_text('保存失败，请查看操作提示与执行记录。')
                expect(page.locator('#saveButton')).to_be_enabled()
                assert session.csrf_token not in page.locator('#savedInfo').inner_text()
                assert len(session.submissions) == 3, '失败保存不得自动重发'
                report['checks'].append('failed_save_shows_short_failure_and_does_not_retry')
                state_overlay.clear()
                page.evaluate('poll()')

                page.get_by_text('在本机打开 Android 镜像', exact=True).click()
                with page.expect_download() as download_info:
                    page.get_by_role('link', name='下载镜像启动脚本', exact=True).click()
                download = download_info.value
                target = folder/'downloaded-android-mirror.ps1'
                download.save_as(str(target))
                code = target.read_bytes()
                assert download.suggested_filename == 'android_mirror.ps1'
                assert code.startswith(b'\xef\xbb\xbf') and code.count(b'\xef\xbb\xbf') == 1
                decoded = code.decode('utf-8-sig')
                assert '__ROYALE_CODESPACE__' not in decoded
                assert "$Codespace = 'mirror-browser-fixture'" in decoded
                assert '--no-control' in decoded and '--no-audio' in decoded
                report['checks'].append('downloaded_mirror_script_has_current_codespace_and_one_utf8_bom')
                report['fake_submissions'] = session.submissions
                assert not report['page_errors'], report['page_errors']
                page.screenshot(path=str(folder/'source-open-queued.png'), full_page=True)
                browser.close()
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
        save()
    return report


class PublicReplayBrowserTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ROYALE_PUBLIC_REPLAY_BROWSER_OUTPUT'),
                         '显式指定仓库内输出目录才启动 Chromium；默认测试不增加浏览器负载')
    def test_actual_json_import_and_mirror_download(self):
        report = check_public_replay_browser(os.environ['ROYALE_PUBLIC_REPLAY_BROWSER_OUTPUT'])
        self.assertEqual(report['status'], 'passed', report.get('traceback', report))


if __name__ == '__main__':
    unittest.main()
