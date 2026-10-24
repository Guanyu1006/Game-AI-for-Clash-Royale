"""跨平台入口的真实解析、子进程转发和失败语义；不连接原生。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import phase3
from tools.phase3_acceptance import dependency_inventory, run_check, required_modules
from royale_phase3.common import local

ROOT = Path(__file__).resolve().parents[1]


class CloudCliContracts(unittest.TestCase):
    def test_help_reaches_selected_training_parser(self):
        with patch.object(phase3.subprocess, 'call', return_value=0) as execute:
            self.assertEqual(phase3.main(['train', '--help']), 0)
        command = execute.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[-2:], ['train', '--help'])

    def test_baseline_uses_active_interpreter_and_preserves_arguments(self):
        with patch.object(phase3.subprocess, 'call', return_value=7) as execute:
            self.assertEqual(phase3.main(['baseline-audit', '--output', 'outputs/a b']), 7)
        self.assertEqual(execute.call_args.args[0],
            [sys.executable, '-B', '-m', 'royale_phase3.evaluation', '--output', 'outputs/a b'])

    def test_native_loop_entry_preserves_budget_and_return_code(self):
        with patch.object(phase3.subprocess, 'call', return_value=2) as execute:
            result=phase3.main(['native-loop','run','--output','outputs/a b','--games','4'])
        self.assertEqual(result,2)
        command=execute.call_args.args[0]
        self.assertEqual(command[:3],[sys.executable,'-B',str(ROOT/'tools/native_loop.py')])
        self.assertEqual(command[3:],['run','--output','outputs/a b','--games','4'])

    def test_missing_native_runtime_is_explicit_and_does_not_spawn(self):
        with patch.object(phase3.os, 'name', 'posix'), \
             patch.object(phase3.subprocess, 'call') as execute, \
             contextlib.redirect_stderr(io.StringIO()) as message:
            self.assertEqual(phase3.main(['play']), 2)
        execute.assert_not_called()
        result = json.loads(message.getvalue())
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['native_runtime_verified'])
        self.assertEqual(result['native_rpc_calls'], 0)

    def test_real_baseline_help_is_forwarded_without_windows_environment(self):
        result = subprocess.run([sys.executable, '-B', 'tools/phase3.py', 'baseline-audit', '--help'],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output', result.stdout)
        self.assertIn('baseline audit', result.stdout)

    def test_invalid_command_exits_nonzero(self):
        result = subprocess.run([sys.executable, '-B', 'tools/phase3.py', 'unknown-command'],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)

    def test_bootstrap_rejects_unknown_option_before_installation(self):
        if os.name == 'nt':
            self.skipTest('Bash bootstrap belongs to the Linux cloud profile')
        result = subprocess.run(['bash', 'tools/cloud/bootstrap.sh', '--not-an-option'],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unknown bootstrap option', result.stderr)

    def test_acceptance_checks_actual_exit_and_semantics(self):
        root = ROOT/'.tmp/phase3'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            folder = Path(directory)
            failed = run_check(folder, 'nonzero', ['-c', 'raise SystemExit(6)'])
            self.assertEqual(failed['exit_code'], 6)
            self.assertEqual(failed['status'], 'failed')
            invalid = run_check(folder, 'semantic', ['-c', 'print("{}")'],
                                validate=lambda value: value['must_exist'])
            self.assertEqual(invalid['exit_code'], 0)
            self.assertEqual(invalid['status'], 'failed')
            self.assertEqual(len(invalid['stdout']['sha256']), 64)
            progress = run_check(folder, 'progress', ['-c',
                'import json; print(json.dumps({"epoch": 1})); print(json.dumps({"passed": True}, indent=2))'],
                validate=lambda value: self.assertTrue(value['passed']))
            self.assertEqual(progress['status'], 'passed')

    def test_core_and_research_requirements_are_explicit(self):
        self.assertEqual(required_modules('core'), ('numpy',))
        self.assertEqual(set(required_modules('research')), {'numpy', 'torch', 'pyarrow', 'gymnasium'})

    def test_missing_dependency_retains_real_import_failure(self):
        name = 'royale_missing_dependency_contract_fixture'
        with patch('tools.phase3_acceptance.required_modules', return_value=(name,)):
            report = dependency_inventory('research')[name]
        self.assertFalse(report['available'])
        self.assertNotEqual(report['exit_code'], 0)
        self.assertIsNone(report['version'])
        self.assertIn('ModuleNotFoundError', report['error'])

    def test_project_paths_reject_foreign_drives_and_parent_escape(self):
        self.assertEqual(local(ROOT/'README.md'), ROOT/'README.md')
        with self.assertRaises(ValueError):
            local('../outside-project')
        if os.name != 'nt':
            for value in ('C:/Windows/win.ini', r'\\server\share\file', 'C:relative-drive'):
                with self.subTest(path=value), self.assertRaises(ValueError):
                    local(value)


if __name__ == '__main__':
    unittest.main()
