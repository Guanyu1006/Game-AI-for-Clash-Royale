"""Pure meminfo parsing and mocked reads; no ADB, VM, native RPC or processes."""
import unittest
from unittest.mock import patch

from tools.cloud.memory_resources import inspect_memory, parse_meminfo


class MemoryResourceTests(unittest.TestCase):
    def test_kilobytes_convert_to_bytes_and_gib_without_guest_claim(self):
        result = parse_meminfo('MemTotal:       16777216 kB\nMemAvailable:    12582912 kB\n'
                               'SwapTotal:        1048576 kB\nSwapFree:          524288 kB\n')
        self.assertEqual(result['total_bytes'], 16 * 1024**3)
        self.assertEqual(result['available_bytes'], 12 * 1024**3)
        self.assertEqual(result['swap_total_bytes'], 1024**3)
        self.assertEqual(result['swap_free_bytes'], 512 * 1024**2)
        self.assertEqual(result['total_gib'], 16.0)
        self.assertEqual(result['available_gib'], 12.0)
        self.assertEqual(result['status'], 'available')
        self.assertIn('not Android guest allocation', result['scope'])
        self.assertIn('cgroup', result['scope'])

    def test_missing_fields_remain_unknown_and_memfree_is_not_available(self):
        result = parse_meminfo('MemTotal: 8388608 kB\nMemFree: 300000 kB\n')
        self.assertEqual(result['total_gib'], 8.0)
        self.assertEqual(result['status'], 'partial')
        for name in ('available_bytes', 'available_gib', 'swap_total_bytes', 'swap_free_bytes'):
            self.assertIsNone(result[name])

    def test_explicit_zero_swap_is_distinguished_from_missing_swap(self):
        result = parse_meminfo('SwapTotal: 0 kB\nSwapFree: 0 kB\n')
        self.assertEqual(result['swap_total_bytes'], 0)
        self.assertEqual(result['swap_free_bytes'], 0)
        self.assertIsNone(result['total_bytes'])
        self.assertEqual(result['status'], 'partial')

    def test_unreadable_meminfo_does_not_report_zero_memory(self):
        for failure in (FileNotFoundError('fixture absent'), PermissionError('fixture denied'),
                        UnicodeError('fixture unreadable')):
            with self.subTest(error=type(failure).__name__), \
                    patch('tools.cloud.memory_resources.Path.read_text', side_effect=failure):
                result = inspect_memory()
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['source'], '/proc/meminfo')
            self.assertIn(type(failure).__name__, result['error'])
            for name in ('total_bytes', 'available_bytes', 'swap_total_bytes', 'swap_free_bytes',
                         'total_gib', 'available_gib'):
                self.assertIsNone(result[name])

    def test_empty_invalid_units_negative_values_and_duplicates_are_unknown(self):
        for content in ('', 'MemTotal: -1 kB\nMemAvailable: not-a-number kB\n',
                        'MemTotal: 16 GB\nMemAvailable: 5 bytes\n',
                        'MemTotal: 8 kB\nMemTotal: 16 kB\n'):
            with self.subTest(content=content):
                result = parse_meminfo(content)
                self.assertIsNone(result['total_bytes'])
                self.assertIsNone(result['available_bytes'])
                self.assertEqual(result['status'], 'unknown')

    def test_inspect_reads_meminfo_once_without_other_sources_or_processes(self):
        with patch('tools.cloud.memory_resources.Path.read_text', return_value='MemTotal: 1024 kB\n') as reader:
            result = inspect_memory()
        reader.assert_called_once_with(encoding='ascii')
        self.assertEqual(result['total_bytes'], 1024**2)
        self.assertEqual(result['total_gib'], 1 / 1024)
        self.assertIsNone(result['available_bytes'])

    def test_doctor_adds_memory_without_promoting_native_acceptance(self):
        from tools.cloud import workspace
        memory = parse_meminfo('MemTotal: 16777216 kB\nMemAvailable: 12582912 kB\n')
        with patch('tools.cloud.memory_resources.inspect_memory', return_value=memory), \
                patch('tools.cloud.native_capabilities.inspect', return_value={'native_runtime_verified': False}) as native, \
                patch.object(workspace, 'verify_assets', return_value={'status': 'fixture'}), \
                patch.object(workspace, 'write'):
            result = workspace.doctor()
        self.assertEqual(result['memory'], memory)
        self.assertFalse(result['native']['native_runtime_verified'])
        self.assertEqual(result['schema'], 'royale-cloud-doctor.v1')
        native.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
