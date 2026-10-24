"""Public-source conversion contracts; no Android or native RPC is used."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from royale_phase2.public_replay import PublicReplayError, PublicReplayPlan, import_public_replay
from royale_phase2.replays import parse_payload
from royale_phase2.runtime import ROOT


def public_fixture():
    """Small synthetic source schema, not an official replay acceptance fixture."""
    cards = ['knight', 'archers', 'giant', 'musketeer', 'mini-pekka', 'cannon', 'fireball', 'arrows']
    player = {'deck': [{'card_key': key, 'level': 11} for key in cards],
              'tower_card': {'card_key': 'tower-princess', 'level': 11}}
    events = []
    for i, side in enumerate(('team', 'opponent')):
        events.append({'source_index': i, 'side': side, 'kind': 'play_card', 'card_key': 'knight',
            'replay_tick_20hz': 100, 'time_seconds': 5.0, 'source_fields': {'data_i': 0},
            'coordinates': {'native_world_units': {'x': 3250, 'y': 12750}}})
    return {'schema_version': 'royaleapi-battle-actions.v1',
            'source': {'replay_tag': 'a'*32, 'anonymized': True},
            'battle': {'team': {'players': [deepcopy(player)], 'crowns': 0},
                       'opponent': {'players': [deepcopy(player)], 'crowns': 1},
                       'result': 'defeat', 'game_mode': 'Ranked'},
            'events': events, 'replay': {'duration': {'timeline_seconds': 180}}}


class PublicReplayContracts(unittest.TestCase):
    def test_both_owners_same_tick_preserved_and_source_is_untouched(self):
        payload = public_fixture(); before = deepcopy(payload)
        plan = PublicReplayPlan.from_mapping(payload)
        self.assertEqual(payload, before)
        self.assertEqual([c['source_event_index'] for c in plan.commands], [0, 1])
        self.assertEqual([c['owner'] for c in plan.commands], [1, 0])
        self.assertEqual([c['tick'] for c in plan.commands], [101, 101])
        self.assertEqual([(c['x'], c['y']) for c in plan.commands], [(3250, 19250)]*2)
        self.assertEqual(len(plan.match['battle']['deck0']['sp']), 8)
        self.assertFalse(plan.assumptions['official_trajectory_verified'])
        self.assertEqual(plan.normalized_payload, before)

    def test_row_and_source_episode_have_the_same_commands(self):
        payload = public_fixture()
        row = {'payload_json': json.dumps(payload), 'replay_tag': 'a'*32, 'event_count': 2,
               'result': 'defeat', 'schema_version': 'royaleapi-replay-parquet.v1'}
        a = PublicReplayPlan.from_mapping(row)
        episode = parse_payload(row)
        b = PublicReplayPlan.from_mapping(episode)
        self.assertEqual(a.commands, b.commands)
        self.assertEqual(a.match, b.match)

    def test_plan_roundtrip_refuses_changed_commands(self):
        plan = PublicReplayPlan.from_mapping(public_fixture())
        value = json.loads(json.dumps(plan.to_dict()))
        self.assertEqual(PublicReplayPlan.from_dict(value).match, plan.match)
        value['commands'][0]['tick'] += 1
        with self.assertRaisesRegex(PublicReplayError, 'commands'):
            PublicReplayPlan.from_dict(value)

    def test_missing_data_i_and_skill_actions_are_actionable(self):
        payload = public_fixture()
        for event in payload['events']: event.pop('source_fields')
        with self.assertRaisesRegex(PublicReplayError, 'data_i'):
            PublicReplayPlan.from_mapping(payload)
        payload = public_fixture(); payload['events'][1]['kind'] = 'activate_ability'
        with self.assertRaisesRegex(PublicReplayError, '不推断技能来源'):
            PublicReplayPlan.from_mapping(payload)

    def test_mixed_levels_are_not_silently_normalized(self):
        payload = public_fixture(); payload['battle']['team']['players'][0]['deck'][0]['level'] = 12
        with self.assertRaisesRegex(PublicReplayError, '拒绝静默'):
            PublicReplayPlan.from_mapping(payload)

    def test_source_order_time_and_coordinate_conflicts_are_rejected(self):
        for alteration in ('order', 'time', 'coordinate', 'offset'):
            payload = public_fixture()
            if alteration == 'order': payload['events'][1]['source_index'] = 0
            elif alteration == 'time': payload['events'][0]['time_seconds'] = 6
            elif alteration == 'coordinate': payload['events'][0]['coordinates']['native_world_units']['x'] = -1
            else: payload['events'][0]['coordinates']['subcell_offset_from_floor_center'] = {'x': .4, 'y': .4}
            with self.subTest(alteration=alteration), self.assertRaises(PublicReplayError):
                PublicReplayPlan.from_mapping(payload)

    def test_archive_keeps_raw_bytes_and_refuses_existing_directory(self):
        payload = public_fixture(); raw = json.dumps(payload, indent=3).encode()
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            output = Path(temp)/'import'
            report = import_public_replay(payload, output, filename='source.json', source_bytes=raw)
            self.assertEqual((output/'raw.json').read_bytes(), raw)
            self.assertEqual(report['raw_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(PublicReplayPlan.load(output/'plan.json').source['sha256'], report['raw_sha256'])
            with self.assertRaises(FileExistsError): import_public_replay(payload, output)


if __name__ == '__main__':
    unittest.main()
