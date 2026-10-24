"""Isolated socket fixtures; never contact an Android/native/ADB service."""
import io
import json
import traceback
import unittest
from unittest.mock import patch

from royale_phase2.probe import ProbeClient, ProbeUnavailable


GREETING = b'{"ok":true,"session":"control-session.v1"}\n'


class FakeConnection:
    def __init__(self, responses):
        self.stream = io.BytesIO(responses)
        self.sent = []
        self.closed = False

    def makefile(self, mode):
        if mode != 'rb':
            raise AssertionError('Unexpected socket stream mode')
        return self.stream

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class NativeProbeTransportTests(unittest.TestCase):
    def request_failure(self, responses, *, max_reply=64, command='configure-native {}'):
        connection = FakeConnection(responses)
        client = ProbeClient(max_reply=max_reply)
        with patch('royale_phase2.probe.socket.create_connection', return_value=connection) as connect:
            with self.assertRaises(ProbeUnavailable) as caught:
                client.request(command)
            connect.assert_called_once_with(('127.0.0.1', 26789), timeout=15)
        self.assertTrue(connection.closed)
        self.assertFalse(client.valid)
        self.assertIsNone(client.connection)
        self.assertIsNone(client.stream)
        return caught.exception, connection

    def test_normal_handshake_and_multiple_requests_reuse_one_connection(self):
        connection = FakeConnection(GREETING + b'{"ok":true,"contentReady":true}\n'
                                    + b'{"ok":true,"sequence":7}\n')
        client = ProbeClient(max_reply=64)
        with patch('royale_phase2.probe.socket.create_connection', return_value=connection) as connect:
            try:
                self.assertTrue(client.status()['contentReady'])
                self.assertEqual(client.configure({'battle': {}}, rendered=True)['sequence'], 7)
                self.assertTrue(client.valid)
                connect.assert_called_once()
            finally:
                client.close()
        self.assertEqual(connection.sent, [b'session-v1\n', b'status\n',
                                          b'configure-native {"battle":{}}\n'])
        self.assertTrue(connection.closed)

    def test_handshake_empty_eof_prevents_mutating_command_from_being_sent(self):
        error, connection = self.request_failure(b'')
        self.assertEqual(connection.sent, [b'session-v1\n'])
        self.assertEqual(error.diagnostics, {'receive_phase': 'session_handshake',
            'failure_kind': 'empty_eof', 'received_bytes': 0, 'max_reply_bytes': 64})
        self.assertIn('phase=session_handshake', str(error))

    def test_command_empty_eof_does_not_reconnect_or_retry_mutation(self):
        error, connection = self.request_failure(GREETING)
        self.assertEqual(connection.sent, [b'session-v1\n', b'configure-native {}\n'])
        self.assertEqual(error.diagnostics['receive_phase'], 'command_response')
        self.assertEqual(error.diagnostics['failure_kind'], 'empty_eof')
        self.assertEqual(error.diagnostics['received_bytes'], 0)
        self.assertIn('request outcome unknown', str(error))

    def test_partial_eof_and_over_limit_are_distinct_at_both_receive_phases(self):
        private = b'PRIVATE_RESPONSE'
        for phase, prefix in (('session_handshake', b''), ('command_response', GREETING)):
            for kind, payload, received in [('partial', private, len(private)),
                    ('over_limit', private * 10 + b'\n', 65),
                    ('over_limit', private * 10, 65)]:
                with self.subTest(phase=phase, kind=kind, newline=payload.endswith(b'\n')):
                    error, connection = self.request_failure(prefix + payload)
                    self.assertEqual(error.diagnostics, {'receive_phase': phase,
                        'failure_kind': kind, 'received_bytes': received, 'max_reply_bytes': 64})
                    self.assertNotIn(private.decode(), str(error))
                    self.assertNotIn(private.decode(), json.dumps(error.diagnostics))
                    expected = [b'session-v1\n']
                    if phase == 'command_response':
                        expected.append(b'configure-native {}\n')
                    self.assertEqual(connection.sent, expected)

    def test_newline_beyond_bound_is_over_limit_even_if_entire_payload_arrived(self):
        payload = b' ' * 64 + b'\n'
        error, _ = self.request_failure(GREETING + payload)
        self.assertEqual(error.diagnostics['failure_kind'], 'over_limit')
        self.assertEqual(error.diagnostics['received_bytes'], 65)

    def test_exact_bound_with_newline_accepts_complete_json(self):
        payload = b'{"ok":true}' + b' ' * (64-len(b'{"ok":true}')-1) + b'\n'
        connection = FakeConnection(GREETING + payload)
        client = ProbeClient(max_reply=64)
        with patch('royale_phase2.probe.socket.create_connection', return_value=connection):
            try:
                self.assertEqual(client.status(), {'ok': True})
            finally:
                client.close()

    def test_invalid_json_is_bounded_and_does_not_expose_document_in_traceback(self):
        for prefix, phase in ((b'', 'session_handshake'), (GREETING, 'command_response')):
            for payload in (b'PRIVATE_RESPONSE\n', b'{"secret":"PRIVATE_RESPONSE",}\n',
                            b'{"secret":"PRIVATE_RESPONSE","number":NaN}\n', b'\xff\n'):
                with self.subTest(phase=phase, payload=payload):
                    error, _ = self.request_failure(prefix + payload)
                    self.assertEqual(error.diagnostics['receive_phase'], phase)
                    self.assertEqual(error.diagnostics['failure_kind'], 'invalid_json')
                    self.assertEqual(error.diagnostics['received_bytes'], len(payload))
                    self.assertNotIn('PRIVATE_RESPONSE', str(error))
                    rendered = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
                    self.assertNotIn('PRIVATE_RESPONSE', rendered)
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                    self.assertTrue(error.__suppress_context__)

    def test_failure_exception_remains_compatible_without_diagnostics(self):
        error = ProbeUnavailable('Existing caller')
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(str(error), 'Existing caller')
        self.assertEqual(error.diagnostics, {})


if __name__ == '__main__':
    unittest.main()
