"""Guest display preparation contracts; no real ADB, Android or RPC calls."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.cloud.android_display import prepare_display
from royale_phase2.runtime import ROOT


class DisplayFixture:
    def __init__(self, **updates):
        self.before = dict(UID='0', BOOT='1', SDK='31', HARDWARE='redroid', BOOT_HARDWARE='',
            FREEZER_VALUE='null', SCREEN_TIMEOUT_VALUE='60000', STAY_ON_VALUE='0')
        self.before.update(updates)
        self.values = dict(self.before)
        self.calls = []
        self.fail = None
        self.freezer_readback = None
        self.server_version = b'0029'

    def __call__(self, label, argv, script, timeout):
        self.calls.append((label, argv, script, timeout))
        if label == self.fail:
            return subprocess.CompletedProcess(argv, 7, b'partial diagnostic', b'fixture command failure')
        if label == 'adb-version':
            value = b'Android Debug Bridge version 1.0.41\n'
        elif label.startswith('server-version-'):
            value = self.server_version
        elif label == 'get-state':
            value = b'device\n'
        elif label in ('read-display-before', 'read-display-after'):
            current = dict(self.values)
            if label == 'read-display-after' and self.freezer_readback is not None:
                current['FREEZER_VALUE'] = self.freezer_readback
            value = ''.join(f'{k}={v}\n' for k, v in current.items()).encode()
        elif label == 'disable-cached-apps-freezer':
            self.values['FREEZER_VALUE'] = 'disabled'; value = b''
        elif label == 'keep-display-on':
            self.values['STAY_ON_VALUE'] = '7'; value = b''
        elif label == 'wake-display':
            value = b''
        elif label == 'activity-settings-diagnostic':
            value = b'ACTIVITY MANAGER SETTINGS\n  mUseFreezer=false\n'
        else:
            raise AssertionError('Unexpected fixture command: '+label)
        return subprocess.CompletedProcess(argv, 0, value, b'')


class AndroidDisplayContracts(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='android-display-', dir=ROOT/'.tmp')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.adb = self.folder/'adb'
        self.adb.write_bytes(b'fixture only; never executed')

    def prepare(self, fixture=None, **kwargs):
        fixture = fixture or DisplayFixture()
        args = dict(adb=str(self.adb), adb_port=5038, serial='fixture:15555',
                    root_shell='su0', output=self.folder/'prepare', runner=fixture)
        args.update(kwargs)
        report = prepare_display(**args)
        return report, fixture

    def test_records_prior_values_restoration_and_verified_readback_without_other_mutations(self):
        report, fixture = self.prepare()
        self.assertEqual(report['status'], 'prepared')
        self.assertEqual(report['before']['cached_apps_freezer'], 'null')
        self.assertEqual(report['before']['screen_off_timeout'], '60000')
        self.assertEqual(report['before']['stay_on_while_plugged_in'], '0')
        self.assertEqual(report['after']['cached_apps_freezer'], 'disabled')
        self.assertTrue(report['freezer_setting_verified'])
        self.assertFalse(report['freezer_runtime_effect_verified'])
        self.assertFalse(report['rendering_verified'])
        self.assertFalse(report['future_uptime_verified'])
        self.assertEqual(report['evidence_scope'], 'interface_only')
        restore = (self.folder/'prepare/restore-guest-settings.stdin.sh').read_text()
        self.assertIn('settings delete global cached_apps_freezer\n', restore)
        self.assertIn('settings put global stay_on_while_plugged_in 0\n', restore)
        self.assertFalse(report['restore']['executed'])
        self.assertTrue(report['restore']['explicit_action_required'])
        self.assertEqual(report['activity_settings_diagnostic']['freezer_lines'], ['mUseFreezer=false'])
        self.assertEqual([row['operation'] for row in report['mutation_attempts']],
            ['disable-cached-apps-freezer', 'keep-display-on', 'wake-display'])
        self.assertTrue(all(row['status'] == 'command_completed' for row in report['mutation_attempts']))
        self.assertTrue(all(row.get('stdout') and row.get('stderr') for row in report['commands']))
        scripts = '\n'.join(script for _, _, script, _ in fixture.calls if script)
        for forbidden in ('force-stop', 'reboot', 'anr_show', 'show_anr', 'settings put system screen_off_timeout',
                          'settings --user 0 put system screen_off_timeout'):
            self.assertNotIn(forbidden, scripts)
        for _, argv, _, _ in fixture.calls:
            self.assertNotIn('start-server', argv)
            self.assertNotIn('kill-server', argv)
            if 'shell' in argv or 'get-state' in argv:
                self.assertIn('-H', argv)
                self.assertEqual(argv[argv.index('-s')+1], 'fixture:15555')

    def test_existing_values_are_restored_exactly_without_assuming_original_defaults(self):
        report, _ = self.prepare(DisplayFixture(FREEZER_VALUE='enabled', STAY_ON_VALUE='3'))
        self.assertEqual(report['status'], 'prepared')
        restore = (self.folder/'prepare/restore-guest-settings.stdin.sh').read_text()
        self.assertIn('settings put global cached_apps_freezer enabled\n', restore)
        self.assertIn('settings put global stay_on_while_plugged_in 3\n', restore)
        self.assertNotIn('screen_off_timeout', restore)

    def test_guest_must_be_booted_root_redroid_api31_before_any_mutation(self):
        for index, fields in enumerate(({'UID': '2000'}, {'BOOT': '0'}, {'SDK': '30'},
                                       {'HARDWARE': 'ranchu'}, {'SDK': ''})):
            with self.subTest(fields=fields):
                report, fixture = self.prepare(DisplayFixture(**fields), output=self.folder/f'reject-{index}')
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['mutation_attempts'], [])
                self.assertNotIn('disable-cached-apps-freezer', [label for label, *_ in fixture.calls])

    def test_existing_adb_protocol_mismatch_never_starts_server_or_contacts_guest(self):
        fixture = DisplayFixture(); fixture.server_version = b'0028'
        report, _ = self.prepare(fixture)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('server/client version mismatch', report['error'])
        self.assertEqual(report['mutation_attempts'], [])
        self.assertFalse(any('shell' in argv for _, argv, _, _ in fixture.calls))

    def test_partial_mutation_failure_preserves_restore_and_never_retries(self):
        fixture = DisplayFixture(); fixture.fail = 'keep-display-on'
        report, _ = self.prepare(fixture)
        self.assertEqual(report['status'], 'failed')
        self.assertTrue((self.folder/'prepare/restore-guest-settings.stdin.sh').is_file())
        self.assertEqual([row['status'] for row in report['mutation_attempts']],
                         ['command_completed', 'failed_or_unknown_outcome'])
        labels = [label for label, *_ in fixture.calls]
        self.assertEqual(labels.count('keep-display-on'), 1)
        self.assertNotIn('wake-display', labels)
        self.assertEqual(fixture.values['FREEZER_VALUE'], 'disabled')

    def test_wrong_freezer_readback_does_not_claim_prepared_or_effective(self):
        fixture = DisplayFixture(); fixture.freezer_readback = 'enabled'
        report, _ = self.prepare(fixture)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('readback is not disabled', report['error'])
        self.assertFalse(report['freezer_setting_verified'])
        self.assertFalse(report['freezer_runtime_effect_verified'])


if __name__ == '__main__':
    unittest.main()
