"""镜像计划的隔离与参数检查；不连接实际 Android 或运行 Windows GUI。"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.cloud.android_mirror import ROOT, main, mirror_plan, powershell_quote, write_plan


class AndroidMirrorPlanTests(unittest.TestCase):
    def test_plan_separates_local_and_remote_ports_without_starting_tools(self):
        with patch('socket.create_connection') as network, patch('subprocess.run') as process:
            value = mirror_plan('example-codespace-123')
        network.assert_not_called(); process.assert_not_called()
        tunnel = value['tunnel_argv']
        self.assertIn('127.0.0.1:15038:127.0.0.1:5038', tunnel)
        self.assertIn('127.0.0.1:27184:127.0.0.1:27183', tunnel)
        self.assertEqual(tunnel[tunnel.index('--')+1:tunnel.index('--')+3], ['-N', '-T'])
        view = value['mirror_argv']
        self.assertEqual(view[view.index('--port')+1], '27183')
        self.assertEqual(view[view.index('--tunnel-port')+1], '27184')
        self.assertIn('--force-adb-forward', view)
        self.assertIn('--no-control', view)
        self.assertIn('--no-audio', view)
        self.assertIn('--no-power-on', view)
        self.assertEqual(value['mirror_environment'], {'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:15038'})
        self.assertFalse(value['live_mirror_verified'])

    def test_untrusted_codespace_device_and_conflicting_ports_are_rejected(self):
        for name in ('', None, '-R', 'name; echo secret', 'a`whoami`', 'https://example', 'a\nb'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                mirror_plan(name)
        for options in ({'serial': '0.0.0.0:15555'}, {'serial': '127.0.0.1:99999'},
                        {'serial': '127.0.0.1:15555 --tcpip'}, {'local_adb_port': True},
                        {'local_video_port': 15038}, {'remote_video_port': 5038},
                        {'max_fps': 0}, {'max_fps': 61}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                mirror_plan('sample', **options)

    def test_custom_ports_reach_both_plan_and_local_entry(self):
        value = mirror_plan('sample', local_adb_port=25038, local_video_port=27185,
                            remote_video_port=27190, max_fps=1)
        self.assertIn('127.0.0.1:27185:127.0.0.1:27190', value['tunnel_argv'])
        self.assertIn('-RemoteVideoPort 27190', value['powershell_command'])
        self.assertIn('--max-fps=1', value['mirror_argv'])

    def test_literal_powershell_quotes_cannot_expand_code(self):
        self.assertEqual(powershell_quote("a'b $() `test`"), "'a''b $() `test`'")
        with self.assertRaises(ValueError): powershell_quote('a\r\nb')

    def test_cli_uses_current_codespace_without_contacting_it(self):
        with patch.dict(os.environ, {'CODESPACE_NAME': 'current-example'}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['plan']), 0)
        self.assertEqual(json.loads(output.getvalue())['codespace'], 'current-example')

    def test_report_cannot_escape_repo_or_overwrite_previous_evidence(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='mirror-test-') as directory:
            path = Path(directory) / 'plan.json'
            value = mirror_plan('sample')
            self.assertEqual(write_plan(path, value), path)
            with self.assertRaises(FileExistsError): write_plan(path, value)
            self.assertEqual(json.loads(path.read_text())['network_calls'], 0)
        with self.assertRaises(ValueError): write_plan(ROOT.parent / 'mirror-escape.json', {})


if __name__ == '__main__':
    unittest.main()
