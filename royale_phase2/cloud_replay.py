"""Exact-tick replay of saved native journals; this module has no battle model.

A replay owns the same process-wide lease as native training, or runs inside an
explicitly declared external lease. Only its worker thread can issue RPC. Source
commands are registered at their original registration ticks, never all upfront.
Backward seeking explicitly resets the native match and replays the prefix.

Verification compares every *saved* minimal state, not unsaved 20 Hz states,
Android pixels, official-server equivalence, or policy strength.
"""
from bisect import bisect_right
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import threading

from .runtime import local
from .versioning import VersionMismatch


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _integer(value, name, low=0, high=2**31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {name}: {value!r}')
    return value


def _differences(expected, actual, path='$', output=None):
    output = [] if output is None else output
    if len(output) >= 30:
        return output
    if type(expected) is not type(actual):
        output.append({'path': path, 'expected': expected, 'actual': actual})
    elif isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                output.append({'path': path + '.' + key, 'expected_present': key in expected,
                               'actual_present': key in actual})
            else:
                _differences(expected[key], actual[key], path + '.' + key, output)
            if len(output) >= 30:
                break
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            output.append({'path': path + '.length', 'expected': len(expected), 'actual': len(actual)})
        for index, (left, right) in enumerate(zip(expected, actual)):
            _differences(left, right, f'{path}[{index}]', output)
            if len(output) >= 30:
                break
    elif expected != actual:
        output.append({'path': path, 'expected': expected, 'actual': actual})
    return output


def semantic_packet(raw, cumulative_events, event_origin):
    """Normalize generation/counter origin only; retain all other minimal fields."""
    packet = deepcopy(raw)
    packet.pop('generation', None)
    packet['next'] -= event_origin
    packet['plays'] = deepcopy(cumulative_events)
    for event in packet['plays']:
        event[0] -= event_origin
    return packet


def _registration_tick(action):
    receipt_tick = action.get('registration_receipt', {}).get('registeredAtTick')
    before_tick = action.get('before_registration', {}).get('tick')
    if receipt_tick is not None and before_tick is not None and receipt_tick != before_tick:
        raise ValueError('Conflicting original action registration ticks')
    tick = receipt_tick if receipt_tick is not None else before_tick
    if tick is None:
        raise ValueError('Replay needs original registration tick; execution tick minus one is not evidence')
    return _integer(tick, 'original registration tick')


@dataclass(frozen=True)
class NativeReplaySource:
    """A validated immutable-by-copy source; loading is filesystem-only."""
    journal: dict
    states: dict
    feedback: dict
    terminal: dict | None
    files: tuple = ()

    @classmethod
    def load(cls, path):
        path = local(path)
        if path.is_dir():
            folder = path
            if not (folder / 'journal.json').is_file() and (folder / 'native' / 'journal.json').is_file():
                folder = folder / 'native'
            journal_path = folder / 'journal.json'
        else:
            journal_path = path
            folder = path.parent
        files = []

        def read_json(candidate):
            candidate = local(candidate)
            payload = candidate.read_bytes()
            files.append({'path': candidate.relative_to(local('.')).as_posix(),
                          'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
            return json.loads(payload)

        journal = read_json(journal_path)
        init_path = folder / 'initialization.json'
        if init_path.is_file() and read_json(init_path) != journal.get('match'):
            raise ValueError('Saved initialization differs from journal match')
        states = []
        state_path = local(folder / 'states.jsonl')
        if state_path.is_file():
            payload = state_path.read_bytes()
            files.append({'path': state_path.relative_to(local('.')).as_posix(),
                          'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
            for line in payload.decode('utf-8-sig').splitlines():
                if line.strip():
                    row = json.loads(line)
                    states.append(row.get('raw_capture', row))
        feedback_path = folder / 'execution_feedback.json'
        feedback = read_json(feedback_path) if feedback_path.is_file() else []
        terminal = None
        for name in ('native_result.json', 'result.json'):
            candidate = folder / name
            if candidate.is_file():
                row = read_json(candidate)
                if 'tick' in row and 'finalized' in row:
                    terminal = row
                    break
        return cls.from_journal(journal, states=states, feedback=feedback,
                                terminal=terminal, files=files)

    @classmethod
    def from_journal(cls, journal, *, states=(), feedback=(), terminal=None, files=()):
        journal = deepcopy(journal)
        _canonical(journal)  # Reject nonfinite values before any engine mutation.
        if journal.get('schema') != 'royale-phase2-native-journal.v1':
            raise ValueError('Expected native journal v1')
        if not isinstance(journal.get('version_fingerprint'), str) or not journal['version_fingerprint']:
            raise ValueError('Journal needs a pinned version fingerprint')
        if not isinstance(journal.get('match'), dict) or not isinstance(journal['match'].get('battle'), dict):
            raise ValueError('Journal needs its complete native match configuration')
        initial = _integer(journal.get('initialization_tick'), 'initialization tick', 0, 90)
        if not isinstance(journal.get('actions'), list):
            raise ValueError('Journal actions must be an ordered list')
        request_ids = set()
        previous_registration = initial
        for action in journal['actions']:
            if action.get('kind') not in ('play_card', 'activate_ability'):
                raise ValueError('Unsupported journal action')
            _integer(action.get('owner'), 'owner', 0, 1)
            tick = _integer(action.get('tick'), 'execution tick', initial + 1)
            registered = _registration_tick(action)
            if not previous_registration <= registered < tick:
                raise ValueError('Actions must retain registration order and precede execution')
            previous_registration = registered
            request_id = action.get('request_id')
            if not isinstance(request_id, str) or not request_id or request_id in request_ids:
                raise ValueError('Every source request_id must be present and unique')
            request_ids.add(request_id)
            if action['kind'] == 'play_card':
                for key, low, high in (('card_id', 1, 2**31 - 1), ('x', 0, 17999), ('y', 0, 31999)):
                    _integer(action.get(key), key, low, high)
            elif not isinstance(action.get('hints', []), list) or not all(isinstance(h, str) for h in action.get('hints', [])):
                raise ValueError('Ability hints must be strings')
        state_map = {}
        previous_tick = initial
        event_rows = {}
        expected_packets = {}
        origin = None
        source_generation = None
        for state in states:
            state = deepcopy(state)
            _canonical(state)
            if state.get('schema') != 'cr-minimal.v4' or state.get('ok') is not True:
                raise ValueError('Unsupported saved native state')
            tick = _integer(state.get('tick'), 'saved state tick', initial)
            if tick < previous_tick:
                raise ValueError('Saved state ticks must be ordered')
            previous_tick = tick
            _integer(state.get('next'), 'saved next event counter')
            generation = _integer(state.get('generation'), 'saved generation', 1)
            if source_generation is None:
                source_generation = generation
            elif source_generation != generation:
                raise ValueError('Source states span multiple native generations')
            if origin is None:
                origin = state['next'] - 1
            for event in state.get('plays', []):
                if not isinstance(event, list) or len(event) != 5 or event[1] > tick:
                    raise ValueError('Invalid or future saved card event')
                if event[0] in event_rows and event_rows[event[0]] != event:
                    raise ValueError('Conflicting saved card event')
                event_rows[event[0]] = deepcopy(event)
            packet = semantic_packet(state, list(event_rows.values()), origin)
            if tick in expected_packets and packet != expected_packets[tick]:
                raise ValueError('Conflicting saved states at one tick')
            expected_packets[tick] = packet
            state_map[tick] = state
        if state_map and min(state_map) != initial:
            raise ValueError('Full replay source must include its initialization state')
        expected_events = journal.get('actual_card_events')
        if expected_events is not None and state_map and list(event_rows.values()) != expected_events:
            raise ValueError('Saved states and journal disagree on ordered actual card events')
        feedback_map = {}
        for row in feedback:
            request_id = row.get('request_id')
            if request_id not in request_ids or request_id in feedback_map:
                raise ValueError('Feedback has duplicate or unknown request_id')
            feedback_map[request_id] = deepcopy(row)
        if terminal is not None:
            terminal = deepcopy(terminal)
            _canonical(terminal)
            terminal_tick = _integer(terminal.get('tick'), 'terminal tick', initial)
            if state_map and terminal_tick != max(state_map):
                raise ValueError('Source result does not match its final saved state tick')
            if state_map:
                final = state_map[terminal_tick]
                for key in ('finalized', 'result', 'crowns'):
                    if key in terminal and terminal[key] != final.get(key):
                        raise ValueError('Source result disagrees with final saved state')
        # Keep normalized source packets private to the source, never to actor inputs.
        source = cls(journal, state_map, feedback_map, terminal, tuple(deepcopy(files)))
        object.__setattr__(source, '_packets', expected_packets)
        return source

    @property
    def initialization_tick(self):
        return self.journal['initialization_tick']

    @property
    def end_tick(self):
        if self.terminal is not None:
            return self.terminal['tick']
        if self.states:
            return max(self.states)
        return max([self.initialization_tick] + [a['tick'] for a in self.journal['actions']])

    def summary(self):
        return {'version_fingerprint': self.journal['version_fingerprint'],
                'journal_sha256': _digest(self.journal), 'initialization_tick': self.initialization_tick,
                'end_tick': self.end_tick, 'actions': len(self.journal['actions']),
                'saved_states': len(self.states), 'source_terminal': deepcopy(self.terminal),
                'files': list(deepcopy(self.files))}


class CloudNativeReplay:
    """One-thread, one-lease controller over a real backend or test transport.

    With ``lease_external=True`` the caller explicitly promises to hold
    ``royale_phase3.common.native_lock`` for this controller's whole lifetime.
    This declaration is not a new independent lock or proof of native execution.
    The backend is kept open by default for a console to reuse it after replay.
    """
    def __init__(self, engine, source, *, lease_external=False, close_backend=False,
                 progress_callback=None, stop_requested=None):
        self.engine = engine
        self.source = source if isinstance(source, NativeReplaySource) else NativeReplaySource.load(source)
        self.lease_external = bool(lease_external)
        self.close_backend = bool(close_backend)
        if progress_callback is not None and not callable(progress_callback):
            raise ValueError('progress_callback must be callable')
        self.progress_callback = progress_callback
        if stop_requested is not None and not callable(stop_requested):
            raise ValueError('stop_requested must be callable')
        self.stop_requested = stop_requested
        self._lease = None
        self._owner = None
        self.state = None
        self.error = None
        self.runs = []
        self.seek_history = []
        self._valid = False
        self._last_seek_interrupted = False
        self._requested_seek_tick = None

    def __enter__(self):
        if self._owner is not None:
            raise RuntimeError('Replay lease is already active')
        if self.engine.version['fingerprint'] != self.source.journal['version_fingerprint']:
            raise VersionMismatch('Replay journal uses another native version')
        if self.lease_external:
            lease = nullcontext()
        else:
            from royale_phase3.common import native_lock
            lease = native_lock()
        lease.__enter__()
        self._lease = lease
        self._owner = threading.get_ident()
        return self

    def _require_owner(self, *, state=True):
        if self._owner is None or self._owner != threading.get_ident():
            raise RuntimeError('Native replay RPC requires its lease-owning worker thread')
        if state and (not self._valid or self.state is None):
            raise RuntimeError('Replay is invalid or not started; explicitly start/reset it')

    def _failed(self, exc):
        self._valid = False
        self.error = f'{type(exc).__name__}: {exc}; no automatic mutation retry'
        if self.runs:
            self.runs[-1]['error'] = self.error

    def start(self):
        """Explicitly discard the current match and initialize a new replay run."""
        self._require_owner(state=False)
        self._valid = False
        self.error = None
        self._last_seek_interrupted = False
        self._requested_seek_tick = None
        self._action_cursor = 0
        self._events = {}
        self._feedback_ids = set()
        self._anchor_ticks = sorted(self.source.states)
        self.runs.append({'run_index': len(self.runs) + 1, 'comparisons': [],
                          'registrations': [], 'feedback': [], 'error': None})
        try:
            self.state = self.engine.reset(deepcopy(self.source.journal['match']),
                                           warmup_ticks=self.source.initialization_tick)
            if self.state['tick'] != self.source.initialization_tick:
                raise RuntimeError('Native reset missed the exact replay initialization tick')
            self._generation = self.state['generation']
            self._origin = self.state['next'] - 1
            self._valid = True
            self._record_state()
            if self.progress_callback is not None:
                self.progress_callback(deepcopy(self.state))
            return deepcopy(self.state)
        except BaseException as exc:
            self._failed(exc)
            raise

    def _record_state(self):
        tick = self.state['tick']
        if self.state['generation'] != self._generation:
            raise RuntimeError('Another controller reset the replay native generation')
        for event in self.state.get('plays', []):
            old = self._events.get(event[0])
            if old is not None and old != event:
                raise RuntimeError('Native event identity changed during replay')
            self._events[event[0]] = deepcopy(event)
        if tick in self.source._packets:
            expected = self.source._packets[tick]
            actual = semantic_packet(self.state, list(self._events.values()), self._origin)
            self.runs[-1]['comparisons'].append({
                'tick': tick, 'equal': expected == actual,
                'source_sha256': _digest(expected), 'replayed_sha256': _digest(actual),
                'differences': _differences(expected, actual) if expected != actual else []})

    def _register_current(self):
        actions = self.source.journal['actions']
        while self._action_cursor < len(actions):
            source = actions[self._action_cursor]
            registered = _registration_tick(source)
            if registered > self.state['tick']:
                break
            if registered != self.state['tick']:
                raise RuntimeError('Replay passed an original registration boundary')
            if self._stop_pending():
                return False
            if source['kind'] == 'play_card':
                receipt = self.engine.schedule_card(source['owner'], source['card_id'], source['x'],
                    source['y'], source['tick'], request_id=source['request_id'])
            else:
                receipt = self.engine.schedule_ability(source['owner'], deepcopy(source.get('hints', [])),
                    source['tick'], request_id=source['request_id'])
            actual_registration = receipt.get('registration_receipt', {}).get('registeredAtTick')
            equal = actual_registration == registered
            self.runs[-1]['registrations'].append({
                'request_id': source['request_id'], 'original_registration_tick': registered,
                'actual_registration_tick': actual_registration, 'equal': equal,
                'source_action': deepcopy(source), 'replayed_registration': deepcopy(receipt)})
            if not equal:
                raise RuntimeError('Native receipt did not retain original registration tick')
            self._action_cursor += 1
            if self._stop_pending():
                return False
        return True

    def _stop_pending(self):
        return bool(self.stop_requested is not None and self.stop_requested())

    def _record_feedback(self):
        actions = self.source.journal['actions'][:self._action_cursor]
        due = [a['request_id'] for a in actions
               if a['tick'] <= self.state['tick'] and a['request_id'] not in self._feedback_ids]
        fields = ('card_consumed', 'native_injected', 'actual_execution_tick', 'consumption_status')
        for request_id in due:
            if self._stop_pending():
                return False
            # One request per backend call gives pause/shutdown a boundary
            # between scheduler status RPCs as well as between advances.
            replies = self.engine.execution_feedback([request_id])
            if len(replies) != 1 or replies[0].get('request_id') != request_id:
                raise RuntimeError('Native feedback does not cover the due replay request exactly')
            actual = replies[0]
            expected = self.source.feedback.get(request_id)
            differences = [] if expected is None else [
                {'field': key, 'expected': expected[key], 'actual': actual.get(key)}
                for key in fields if key in expected and expected[key] != actual.get(key)]
            self.runs[-1]['feedback'].append({'request_id': request_id,
                'source_feedback_available': expected is not None,
                'equal': not differences if expected is not None else None,
                'differences': differences, 'original_feedback': deepcopy(expected),
                'replayed_feedback': deepcopy(actual)})
            self._feedback_ids.add(request_id)
            if self._stop_pending():
                return all(request in self._feedback_ids for request in due)
        return True

    def seek(self, tick):
        """Reach a native tick, or return a valid paused prefix on cancellation.

        Cancellation is cooperative at backend-operation boundaries. It does
        not cancel an in-flight RPC, retry a mutation, or undo registered work.
        Clear the caller's stop flag and seek again to continue this prefix.
        Backwards movement still explicitly resets before prefix replay.
        """
        self._require_owner()
        tick = _integer(tick, 'seek tick', self.source.initialization_tick, self.source.end_tick)
        before = self.state['tick']
        reset = tick < before
        self._last_seek_interrupted = False
        self._requested_seek_tick = tick
        try:
            if reset:
                self.start()
                self._requested_seek_tick = tick
            actions = self.source.journal['actions']
            while True:
                if self._stop_pending():
                    pending_feedback = any(a['tick'] <= self.state['tick']
                        and a['request_id'] not in self._feedback_ids for a in actions[:self._action_cursor])
                    self._last_seek_interrupted = self.state['tick'] < tick or pending_feedback
                    break
                if not self._record_feedback():
                    self._last_seek_interrupted = True
                    break
                if self.state['tick'] == tick:
                    break
                if self.state.get('finalized'):
                    raise RuntimeError('Native terminated before requested source tick')
                if not self._register_current() or self._stop_pending():
                    self._last_seek_interrupted = True
                    break
                current = self.state['tick']
                boundaries = [tick]
                if self._action_cursor < len(actions):
                    boundaries.append(_registration_tick(actions[self._action_cursor]))
                index = bisect_right(self._anchor_ticks, current)
                if index < len(self._anchor_ticks):
                    boundaries.append(self._anchor_ticks[index])
                boundaries.extend(a['tick'] for a in actions[:self._action_cursor] if a['tick'] > current)
                target = min(b for b in boundaries if b > current)
                after = self.engine.advance_exact_ticks(target - current)
                if after['tick'] != target:
                    raise RuntimeError('Replay terminated or stopped before the requested source boundary')
                self.state = after
                self._record_state()
                if self.progress_callback is not None:
                    self.progress_callback(deepcopy(self.state))
            self.seek_history.append({'before_tick': before, 'target_tick': tick,
                                      'after_tick': self.state['tick'], 'explicit_reset': reset,
                                      'target_reached': self.state['tick'] == tick,
                                      'interrupted': self._last_seek_interrupted,
                                      'run_index': self.runs[-1]['run_index']})
            return deepcopy(self.state)
        except BaseException as exc:
            self._failed(exc)
            raise

    def step(self, ticks=1):
        self._require_owner()
        ticks = _integer(ticks, 'step ticks', 1)
        return self.seek(min(self.state['tick'] + ticks, self.source.end_tick))

    def observe_actor(self, owner):
        """Current public observation only; source journal/labels are not returned."""
        self._require_owner()
        _integer(owner, 'owner', 0, 1)
        return deepcopy(self.engine.observe_visible(owner))

    def verify_all(self):
        """Freshly reset and compare all saved states through the source end tick."""
        self.start()
        self.seek(self.source.end_tick)
        return self.report()

    def status(self):
        return {'mode': 'native_journal_replay', 'valid': self._valid, 'error': self.error,
                'tick': self.state['tick'] if self.state else None,
                'generation': self.state['generation'] if self.state else None,
                'finalized': self.state.get('finalized') if self.state else None,
                'initialization_tick': self.source.initialization_tick,
                'end_tick': self.source.end_tick,
                'registered_actions': getattr(self, '_action_cursor', 0),
                'source_actions': len(self.source.journal['actions']),
                'runs': len(self.runs),
                'last_seek_interrupted': self._last_seek_interrupted,
                'requested_seek_tick': self._requested_seek_tick,
                'lease_mode': 'external_caller_asserted' if self.lease_external else 'shared_native_lock',
                'backward_seek': 'explicit_reset_and_prefix_replay',
                'training_samples': 0}

    def report(self):
        run = deepcopy(self.runs[-1]) if self.runs else None
        comparisons = run['comparisons'] if run else []
        compared = {row['tick'] for row in comparisons}
        all_states = bool(self.source.states) and compared == set(self.source.states)
        states_equal = all_states and all(row['equal'] for row in comparisons)
        all_actions = run is not None and len(run['registrations']) == len(self.source.journal['actions'])
        registrations_equal = all_actions and all(row['equal'] for row in run['registrations'])
        feedback_equal = all(row['equal'] is not False for row in run['feedback']) if run else None
        due_feedback_ids = {a['request_id'] for a in self.source.journal['actions'][:getattr(self, '_action_cursor', 0)]
                            if self.state and a['tick'] <= self.state['tick']}
        feedback_complete = due_feedback_ids <= getattr(self, '_feedback_ids', set())
        terminal_equal = None
        if self.state and self.source.terminal and (self.state['tick'] == self.source.end_tick or self.state.get('finalized')):
            terminal_equal = all(self.state.get(k) == self.source.terminal[k]
                                 for k in ('tick', 'finalized', 'result', 'crowns') if k in self.source.terminal)
        complete = bool(self._valid and states_equal and registrations_equal and feedback_equal and feedback_complete
                        and self.state['tick'] == self.source.end_tick and terminal_equal is not False)
        mismatch = bool(self.error or any(not r['equal'] for r in comparisons)
                        or run and any(not r['equal'] for r in run['registrations'])
                        or feedback_equal is False or terminal_equal is False)
        return {'schema': 'royale-cloud-native-replay-report.v1',
                'status': 'pass' if complete else 'fail' if mismatch else 'incomplete',
                'saved_state_trajectory_verified': complete,
                'source': self.source.summary(), 'current': self.status(), 'run': run,
                'source_saved_states': len(self.source.states), 'compared_saved_states': len(compared),
                'all_saved_states_equal': states_equal, 'all_registration_ticks_equal': registrations_equal,
                'available_consumption_feedback_equal': feedback_equal,
                'executed_feedback_complete': feedback_complete,
                'source_feedback_rows': len(self.source.feedback),
                'terminal_equal': terminal_equal,
                'natural_terminal_verified': bool(complete and self.source.terminal
                    and self.source.terminal.get('finalized') is True and self.state.get('finalized') is True),
                'seek_history': deepcopy(self.seek_history),
                'scope': 'Every saved minimal state and original registration tick; not unsaved states, pixels or official equivalence',
                'normalization': 'Only generation removed and monotonic play counter origin shifted; accumulated ordered plays retained',
                'native_execution_verified': False,
                'native_execution_note': 'This transport-agnostic report alone is not independent proof of Android execution'}

    def close(self):
        if self._owner is None:
            return
        self._require_owner(state=False)
        try:
            if self.close_backend:
                self.engine.close()
        finally:
            self._valid = False
            self._owner = None
            self._lease.__exit__(None, None, None)
            self._lease = None

    def __exit__(self, *_):
        self.close()
