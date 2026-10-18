"""Fail-closed adapter to the actual pinned Android libg control protocol.

State returned by debug_state/reset/advance is oracle cr-minimal.v4 telemetry.
Only observe_visible selects the separate conservative actor whitelist. A
scheduler 'succeeded' receipt means native injection, not proven card use.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import csv
import gzip
import io
import lzma
from pathlib import Path
import time
import uuid

from .probe import ProbeClient, ProbeUnavailable
from .runtime import local, target
from .versioning import verify_manifest, VersionMismatch
from .visibility import project_actor, TowerAttestation


class NativeStateInvalid(RuntimeError):
    """A transport/mutation failure requires an explicit reset before reuse."""


class NativeCapabilityUnsupported(NotImplementedError):
    pass


def _integer(value, name, low=0, high=2**31-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {name}: {value!r}')
    return value


def _read_locked_elixir_rates(manifest):
    """Resolve mode IDs -> named CSV timelines, verifying pinned bytes first."""
    prefix = 'third_party/phase2/runtime-assets/full-update/csv_logic/'
    assets = {row['path'].replace('\\', '/'): row for row in manifest['assets']}
    def rows(name):
        path = prefix + name + '.csv'
        if path not in assets:
            return None
        data = local(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != assets[path]['sha256']:
            raise ValueError('Elixir timeline source differs from locked manifest: ' + path)
        plain = data if data.startswith(b'"Name"') else lzma.decompress(
            data[:5] + data[5:9] + b'\0' * 4 + data[9:], format=lzma.FORMAT_ALONE)
        return list(csv.DictReader(io.StringIO(plain.decode('utf-8-sig'))))[1:]
    modes = rows('game_modes'); timelines = rows('battle_timelines')
    if modes is None or timelines is None:
        return {}
    rates = {}; current = None; tick = 0; unsupported = set()
    for row in timelines:
        if row.get('Name'):
            current = row['Name']; rates[current] = []; tick = 0
        length = row.get('ElixirRateLength'); bar = row.get('ElixirFullBarMS')
        if current is not None and length and bar:
            duration = int(length) * 20; full_bar_ms = int(bar)
            if duration <= 0 or full_bar_ms <= 0:
                # Zero-length/PvE conventions need separate native evidence.
                unsupported.add(current)
                continue
            rates[current].append((tick, tick + duration, 100000 * 50 // full_bar_ms))
            tick += duration
    # Only Ladder/Default has measured native integer increments in the
    # current fixture suite; existence of another CSV mode is not evidence.
    return {72000000 + index: tuple(rates.get(row['BattleTimeline'], ()))
            if (72000000 + index == 72000006 and row['Name'] == 'Ladder'
                and row['BattleTimeline'] == 'Default' and row['BattleTimeline'] not in unsupported) else ()
            for index, row in enumerate(r for r in modes if r.get('Name'))}


def _pending_draw_boundary(action, receipt, candidates, competing, embedded, schedules, match):
    """Prove selected-slot removal while the next draw is pending.

    Unknown mode, phase boundary, missing exact-tick state, any other hand
    mutation, differing cycle, cost, packed parameter or elixir -> no proof.
    Event card identity remains the caller's exact/explicit-alias gate.
    """
    before = action.get('before_registration', {}); after = action.get('after_execution', {})
    tick = action['tick']
    if (action['kind'] != 'play_card' or receipt.get('state') != 'succeeded'
            or embedded or len(candidates) != 1 or len(competing) != 1
            or before.get('tick') != tick-1 or after.get('tick') != tick):
        return False
    registration = action.get('registration_receipt', {})
    if (receipt.get('owner') != action['owner'] or receipt.get('cardId') != action['card_id']
            or receipt.get('executeTick') != tick or receipt.get('registeredAtTick') != tick-1
            or receipt.get('queuedAtTick') != tick-1
            or receipt.get('sequence') != registration.get('sequence')
            or receipt.get('generation') != action.get('generation')
            or registration.get('executeTick') != tick or registration.get('registeredAtTick') != tick-1
            or candidates[0][1] != tick or candidates[0][2] != action['owner']):
        return False
    selected = [h for h in before.get('hand', ()) if h[1] == action.get('card_id')]
    if len(selected) != 1:
        return False
    selected = selected[0]
    hand_before = before.get('hand', ()); hand_after = after.get('hand', ())
    if (len(hand_before) != 4 or len(hand_after) != 3
            or len({h[0] for h in hand_before}) != 4 or len({h[0] for h in hand_after}) != 3
            or sorted(hand_after) != sorted(h for h in hand_before if h[0] != selected[0])
            or after.get('cycle') != before.get('cycle', []) + [action['card_id']]
            or receipt.get('resolvedCommandCardId') != action['card_id']
            or receipt.get('resolvedCardParameter') != selected[3]
            or candidates[0][4] != selected[2]):
        return False
    game_mode = match.get('battle', {}).get('gamemode')
    # Never guess whether the rate switch applies before or after a boundary
    # tick. Such uncommon boundaries remain unconfirmed until measured.
    regen = next((rate for start, end, rate in schedules.get(game_mode, ())
                  if start < tick < end and start <= before['tick'] < end), None)
    if regen is None or type(before.get('elixir')) is not int or type(after.get('elixir')) is not int:
        return False
    cost = selected[2] * 10000
    return bool(before['elixir'] >= cost
                and after['elixir'] == min(100000, before['elixir'] - cost + regen))


class NativeBattleBackend:
    ticks_per_second = 20
    state_schema = 'cr-minimal.v4'

    def __init__(self, version_manifest='data/phase2/version_manifest.json', port=26789,
                 rendered=False, timeout=60, identity_path=None, trace_path=None, advance_mode='atomic'):
        if advance_mode not in ('atomic', 'two_rpc'):
            raise ValueError('advance_mode must be atomic or two_rpc')
        self.advance_mode = advance_mode
        self.version = verify_manifest(version_manifest)
        manifest = self.version['manifest']
        if manifest['ticks_per_second'] != self.ticks_per_second:
            raise VersionMismatch('This adapter supports only the measured 20 Hz native protocol')
        receipt_path = identity_path or manifest.get('runtime_identity_receipt')
        if not receipt_path:
            raise VersionMismatch('Minimal profile requires an explicit actual Android identity receipt')
        raw_identity = local(receipt_path).read_bytes(); identity = json.loads(raw_identity)
        if (identity.get('schema') != 'phase2-native-adb-identity.v1'
                or not all(identity.get(key) is True for key in ('actual_adb_verified', 'loaded_libg_verified', 'content_verified'))):
            raise VersionMismatch('Runtime component identity has not been actually verified')
        libraries = {a['sha256'] for a in manifest['assets'] if a.get('role') == 'native_library'}
        if identity['libg_sha256'] not in libraries:
            raise VersionMismatch('Loaded libg differs from the version manifest')
        probes = {a['sha256'] for a in manifest['assets'] if a.get('role') == 'native_probe'}
        fingerprints = {a['sha256'] for a in manifest['assets'] if a.get('role') == 'content_fingerprint'}
        if identity.get('probe_sha256') not in probes or identity.get('content_fingerprint_sha256') not in fingerprints:
            raise VersionMismatch('Loaded probe or installed fingerprint differs from the manifest')
        if manifest.get('content_manifest_sha1') != identity.get('content_manifest_sha1'):
            raise VersionMismatch('Runtime content SHA1 identity differs from the manifest')
        if manifest.get('offline_apk_sha256') != identity.get('apk_sha256'):
            raise VersionMismatch('Installed offline client differs from the manifest')
        expected_content = manifest.get('content_version', manifest.get('runtime_content_version'))
        if not expected_content or identity.get('content_version') != expected_content:
            raise VersionMismatch('Runtime content version differs from the locked manifest')
        self.identity = identity; self.identity_receipt = str(local(receipt_path))
        self.identity_receipt_sha256 = hashlib.sha256(raw_identity).hexdigest()
        self._form_aliases = self._read_evolution_aliases(manifest)
        self._elixir_rates = _read_locked_elixir_rates(manifest)
        self.client = ProbeClient(port=port, timeout=timeout)
        self.rendered = bool(rendered); self.timeout = timeout
        self._state = None; self._valid = False; self._match = None; self._towers = None
        self._journal = []; self._plays = {}; self._request_ids = set(); self._wire = []
        self._last_advance = None
        self._initialization_tick = None
        self.capabilities = {'exact_native_ticks': True, 'two_player_card_schedule': True,
                             'ability_injection': True, 'ability_consumption_verified': False,
                             'snapshot_restore': False, 'full_placement_legality_oracle': False,
                             'native_renderer': self.rendered,
                             'actor_observation': 'conservative own HUD and attested public towers'}
        self.trace_path = target(trace_path) if trace_path else None

    @staticmethod
    def _read_evolution_aliases(manifest):
        """Join exact source names, never infer a root by subtracting ID bases."""
        csv_path = 'third_party/phase2/runtime-assets/full-update/csv_logic/spells_evolved.csv'
        specs_path = 'third_party/phase2/firstlight/native_runner/data/competitive/card_specs.json.gz'
        assets = {a['path'].replace('\\', '/'): a for a in manifest['assets']}
        if csv_path not in assets or specs_path not in assets: return {}
        encoded = local(csv_path).read_bytes()
        decoded = encoded if encoded.startswith(b'"Name"') else lzma.decompress(
            encoded[:5] + encoded[5:9] + b'\0' * 4 + encoded[9:], format=lzma.FORMAT_ALONE)
        rows = list(csv.DictReader(io.StringIO(decoded.decode('utf-8-sig'))))[1:]
        named = [r for r in rows if r.get('Name')]
        names = {r['Name']: 13_000_000 + index for index, r in enumerate(named)}
        specs = json.loads(gzip.decompress(local(specs_path).read_bytes()))['specs']
        result = {}
        for spec in specs:
            evo = spec.get('evolution') or {}; name = evo.get('evolution_form_id')
            if name in names:
                result[spec['card_id']] = {'event_card_id': names[name], 'form_name': name,
                    'evidence': [{'path': path, 'sha256': assets[path]['sha256']} for path in (csv_path, specs_path)]}
        return result

    @staticmethod
    def _own_capture(state, owner):
        player = next(p for p in state['players'] if p['owner'] == owner)
        return {'tick': state['tick'], 'hand': deepcopy(player['hand']),
                'cycle': deepcopy(player['cycle']), 'elixir': player['elixir']}

    def _request(self, command):
        record = {'request_index': len(self._wire) + 1, 'command': command,
                  'started_utc': datetime.now(timezone.utc).isoformat()}
        start = time.monotonic()
        try:
            response = self.client.request(command); record['response'] = deepcopy(response)
            return response
        except Exception as exc:
            self._valid = False; self._state = None
            record.update(error=f'{type(exc).__name__}: {exc}', outcome='unknown_or_rejected; no automatic retry')
            raise
        finally:
            record['elapsed_seconds'] = time.monotonic() - start; self._wire.append(record)
            if self.trace_path:
                with self.trace_path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n')

    def _require_state(self):
        if not self._valid or self._state is None:
            raise NativeStateInvalid('Native episode is invalid; explicitly reset before further actions')
        return self._state

    def _capture(self, generation=0, cursor=0):
        state = self._request(f'minimal {generation} {cursor}')
        return self._accept_capture(state, generation)

    def _accept_capture(self, state, generation=0):
        if state.get('schema') != self.state_schema or state.get('ok') is not True:
            self._valid = False
            raise NativeStateInvalid('Unsupported native capture schema')
        _integer(state.get('generation'), 'generation', 1); _integer(state.get('tick'), 'native tick')
        if generation and state['generation'] != generation:
            self._valid = False; raise NativeStateInvalid('Another controller reset this native generation')
        self._state = state
        for action in self._journal:
            if state['tick'] >= action['tick'] and 'after_execution' not in action:
                action['after_execution'] = self._own_capture(state, action['owner'])
        for event in state['plays']:
            if len(event) != 5: raise NativeStateInvalid('Unsupported native card consumption layout')
            self._plays[event[0]] = list(event)
        return deepcopy(state)

    def reset(self, match, warmup_ticks=90):
        """Configure a native replay dict; cmd/evt contents are retained verbatim."""
        _integer(warmup_ticks, 'warmup ticks', 0, 90)
        if not isinstance(match, dict) or not isinstance(match.get('battle'), dict):
            raise ValueError('Expected an explicit native replay JSON object with battle')
        # Serializing first validates numbers and prevents later caller mutation.
        encoded = json.dumps(match, separators=(',', ':'), allow_nan=False)
        self._valid = False; self._state = None; self._towers = None
        self._journal = []; self._plays = {}; self._request_ids = set()
        self.client.close()
        status = self._request('status')
        if not status.get('contentReady'):
            raise NativeStateInvalid('Native content runtime is not initialized')
        if not self.rendered and status.get('mode') != 'headless':
            raise NativeStateInvalid('Headless reset requires an application restart after native-render mode')
        configured = self._request(('configure-native ' if self.rendered else 'configure ') + encoded)
        if self.rendered:
            sequence = configured['sequence']; deadline = time.monotonic() + self.timeout
            while True:
                status = self._request('status')
                if status.get('nativeRenderReady') and status.get('nativeRenderLoaded', 0) >= sequence: break
                if time.monotonic() >= deadline: raise NativeStateInvalid('Native renderer did not create a scene')
                time.sleep(0.05)
            paused = self._request('pause'); current_tick = paused['tick']
            if current_tick > warmup_ticks: raise NativeStateInvalid('Native renderer missed initialization boundary')
            if current_tick < warmup_ticks: self._request(f'advance-native {warmup_ticks-current_tick}')
        elif warmup_ticks:
            self._request(f'step {warmup_ticks}')
        self._request('replay-schedule-clear')
        state = self._capture(); self._match = json.loads(encoded); self._valid = True
        self._initialization_tick = state['tick']
        try: self._towers = TowerAttestation.from_reset(state)
        except ValueError: self._towers = None  # Explicitly omit unsupported/special tower rosters.
        return state

    def debug_state(self):
        return deepcopy(self._require_state())

    def observe_visible(self, owner):
        return project_actor(self._require_state(), owner, self._towers)

    def advance_exact_ticks(self, ticks):
        _integer(ticks, 'advance tick count', 1, 1_000_000)
        before = self._require_state(); generation = before['generation']; cursor = before['next']
        if before['finalized']:
            raise NativeStateInvalid('Episode is already terminal; reset before advancing')
        self._valid = False
        if self.advance_mode == 'atomic' and not self.rendered:
            response = self._request(f'minimal-transition {generation} {cursor} {ticks} 0')
            after = self._accept_capture(response, generation)
            response = {'mode': 'atomic-minimal-transition', 'schema': after['schema'],
                        'generation': after['generation'], 'tick': after['tick']}
        else:
            response = self._request(('advance-native ' if self.rendered else 'step ') + str(ticks))
            after = self._capture(generation, cursor)
        advanced = after['tick'] - before['tick']
        if not 0 <= advanced <= ticks or (advanced != ticks and not after['finalized']):
            raise NativeStateInvalid('Native clock did not reach requested boundary or terminal')
        self._last_advance = {'requested_ticks': ticks, 'advanced_ticks': advanced,
                              'terminal_shortened': advanced < ticks, 'native_response': response}
        self._valid = True
        return after

    def _new_request(self, request_id):
        request_id = str(request_id) if request_id is not None else str(uuid.uuid4())
        if not request_id or request_id in self._request_ids:
            raise ValueError('Every request_id must be unique; mutations are never replayed implicitly')
        self._request_ids.add(request_id); return request_id

    def schedule_card(self, owner, card, x, y, tick, request_id=None):
        state = self._require_state(); _integer(owner, 'owner', 0, 1)
        _integer(card, 'card ID', 1); _integer(x, 'x', 0, 17999); _integer(y, 'y', 0, 31999)
        _integer(tick, 'execution tick', state['tick'] + 1)
        request_id = self._new_request(request_id)
        receipt = self._request(f'replay-schedule-card {owner} {card} {x} {y} {tick}')
        row = {'request_id': request_id, 'kind': 'play_card', 'owner': owner, 'card_id': card,
               'x': x, 'y': y, 'tick': tick, 'generation': state['generation'], 'registration_receipt': receipt,
               'before_registration': self._own_capture(state, owner)}
        self._journal.append(row); return deepcopy(row)

    def schedule_ability(self, owner, hints, tick, request_id=None):
        state = self._require_state(); _integer(owner, 'owner', 0, 1)
        _integer(tick, 'execution tick', state['tick'] + 1)
        if not isinstance(hints, (list, tuple)) or not all(isinstance(h, str) for h in hints):
            raise ValueError('Ability hints must be an explicit sequence of source names')
        sanitized = list(dict.fromkeys(''.join(c for c in h.casefold() if c.isascii() and c.isalnum()) for h in hints))
        encoded = ','.join(h for h in sanitized if h) or '-'
        if len(encoded) > 260: raise ValueError('Ability name hints exceed native protocol limit')
        request_id = self._new_request(request_id)
        receipt = self._request(f'replay-schedule-ability {owner} {encoded} {tick}')
        row = {'request_id': request_id, 'kind': 'activate_ability', 'owner': owner, 'hints': list(hints),
               'tick': tick, 'generation': state['generation'], 'registration_receipt': receipt}
        self._journal.append(row); return deepcopy(row)

    def schedule_journal(self, actions):
        """Register canonical actions in source order; a partial failure is not retried.

        Card rows: kind='play_card',owner,card_id,x,y,tick,request_id(optional).
        Ability rows: kind='activate_ability',owner,hints,tick,request_id(optional).
        """
        receipts = []
        for action in actions:
            if action['kind'] == 'play_card':
                receipt = self.schedule_card(action['owner'], action['card_id'], action['x'], action['y'], action['tick'], action.get('request_id'))
            elif action['kind'] == 'activate_ability':
                receipt = self.schedule_ability(action['owner'], action.get('hints', ()), action['tick'], action.get('request_id'))
            else: raise NativeCapabilityUnsupported(f'Unsupported native journal kind: {action["kind"]}')
            receipts.append(receipt)
        return receipts

    def execution_feedback(self, request_ids=None):
        """Inspect all requests, or an explicit subset, without changing evidence rules.

        Competition is still checked against the full journal. The subset is
        useful for a long online episode and never upgrades injection to use.
        """
        self._require_state(); rows = []
        selected_ids = None if request_ids is None else set(request_ids)
        for action in self._journal:
            if selected_ids is not None and action['request_id'] not in selected_ids:
                continue
            receipt = self._request('replay-schedule-status ' + str(action['registration_receipt']['sequence']))
            candidates = [event for event in self._plays.values() if event[1] == action['tick'] and event[2] == action['owner']]
            competing = [a for a in self._journal if a['kind'] == 'play_card' and a['tick'] == action['tick'] and a['owner'] == action['owner']]
            expected_cards = {action.get('card_id'), receipt.get('resolvedCommandCardId')}
            alias = self._form_aliases.get(action.get('card_id'))
            if alias: expected_cards.add(alias['event_card_id'])
            embedded_ambiguous = bool(self._match.get('cmd'))
            before = action.get('before_registration', {}); after = action.get('after_execution', {})
            selected = [h for h in before.get('hand', ()) if h[1] == action.get('card_id')]
            own_boundary_evidence = False
            if len(selected) == 1 and before['tick'] == action['tick'] - 1 and after:
                hand_card = selected[0]
                replacement = [h for h in after['hand'] if h[0] == hand_card[0]]
                own_boundary_evidence = (receipt.get('resolvedCardParameter') == hand_card[3]
                    and len(candidates) == 1 and candidates[0][4] == hand_card[2]
                    and len(replacement) == 1 and replacement[0][1:] != hand_card[1:]
                    and after['cycle'] != before['cycle'])
            pending_draw_evidence = _pending_draw_boundary(
                action, receipt, candidates, competing, embedded_ambiguous, self._elixir_rates, self._match)
            own_boundary_evidence = own_boundary_evidence or pending_draw_evidence
            alias_match = (len(candidates) == 1 and alias and candidates[0][3] == alias['event_card_id'])
            event_identity_matches = (len(candidates) == 1 and candidates[0][3] in expected_cards
                                      and (not alias_match or own_boundary_evidence))
            opaque_form_root_match = (len(candidates) == 1 and candidates[0][3] == 0
                                     and own_boundary_evidence)
            consumed = (action['kind'] == 'play_card' and receipt['state'] == 'succeeded'
                        and len(candidates) == 1 and len(competing) == 1
                        and (event_identity_matches or opaque_form_root_match) and not embedded_ambiguous)
            rows.append({**deepcopy(action), 'scheduler_feedback': receipt,
                         'native_injected': receipt['state'] == 'succeeded',
                         'card_consumed': consumed,
                         'matched_by': ('unique_native_event_exact_card_id' if consumed and candidates[0][3] == action['card_id'] else
                                        'unique_native_event_source_evolution_alias' if consumed and alias and candidates[0][3] == alias['event_card_id'] else
                                        'unique_native_event_and_own_boundary_hand_cost_parameter_cycle' if consumed and opaque_form_root_match else None),
                         'effective_form_identity': ('unknown' if consumed and candidates[0][3] == 0 else
                             {'kind': 'evolution', **deepcopy(alias)} if consumed and alias and candidates[0][3] == alias['event_card_id'] else
                             {'kind': 'native_event_id', 'card_id': candidates[0][3]} if consumed else None),
                         'own_boundary_consumption_evidence': own_boundary_evidence,
                         'own_boundary_evidence_kind': ('pending_draw_exact_hand_cycle_cost_parameter_elixir' if pending_draw_evidence else
                             'same_slot_replacement' if own_boundary_evidence else None),
                         'own_boundary_evidence_version': 'strict-partial-draw.v1',
                         'consumption_status': 'confirmed_native_card_event' if consumed else
                             'ability_consumption_unavailable_in_minimal' if action['kind'] == 'activate_ability' else
                             'embedded_command_origin_ambiguous' if candidates and embedded_ambiguous else
                             'ambiguous_same_owner_tick' if candidates and len(competing) > 1 else 'not_observed',
                         'actual_execution_tick': candidates[0][1] if consumed else None,
                         'card_event': deepcopy(candidates[0]) if consumed else None,
                         'full_placement_legality_oracle': False})
        return rows

    def result(self):
        state = self._require_state()
        return {'finalized': state['finalized'], 'result': state['result'],
                'crowns': list(state['crowns']), 'tick': state['tick'], 'generation': state['generation']}

    def export_journal(self):
        return {'schema': 'royale-phase2-native-journal.v1', 'version_fingerprint': self.version['fingerprint'],
                'identity_receipt_sha256': self.identity_receipt_sha256, 'match': deepcopy(self._match),
                'initialization_tick': self._initialization_tick,
                'actions': deepcopy(self._journal), 'actual_card_events': list(deepcopy(self._plays).values()),
                'last_advance': deepcopy(self._last_advance)}

    def replay_from_start(self, journal=None, end_tick=None):
        journal = deepcopy(journal or self.export_journal())
        if journal['version_fingerprint'] != self.version['fingerprint']:
            raise VersionMismatch('Journal uses another native version')
        state = self.reset(journal['match'], warmup_ticks=journal.get('initialization_tick', 90))
        self.schedule_journal(journal['actions'])
        if end_tick is not None:
            _integer(end_tick, 'replay end tick', state['tick'])
            if end_tick > state['tick']: state = self.advance_exact_ticks(end_tick - state['tick'])
        return state

    def snapshot(self):
        raise NativeCapabilityUnsupported('Pinned minimal probe does not expose snapshot/restore; use journal replay')

    def close(self):
        self.client.close(); self._valid = False; self._state = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
