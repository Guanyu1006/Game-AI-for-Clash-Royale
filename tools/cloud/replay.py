"""Read-only replay of saved fair inputs; never contacts a native runtime.

The stored row includes subsequent supervision. ``public`` returns actor-time
inputs only, while ``state`` exposes the current row's labels in a separate box.
No observation or terminal state is interpolated between saved decision ticks.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[2]


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReplaySession:
    def __init__(self, root=ROOT, metadata_path=None):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        manifest = json.loads(self._local('shared_assets/MANIFEST.json').read_text(encoding='utf-8-sig'))
        self._assets = {r.get('destination'): r for r in manifest['files'] if r.get('destination')}
        self.metadata_path = metadata_path or manifest['phase3']['metadata']
        path = self._asset(self.metadata_path)
        self.metadata = json.loads(path.read_text(encoding='utf-8-sig'))
        if self.metadata.get('observation_profile') != 'fair':
            raise ValueError('回放入口仅接受明确标记 fair 的保存观测')
        if self.metadata.get('feature_spec', {}).get('schema') != 'royale-phase3-player-vector.v1':
            raise ValueError('Unsupported saved vector schema')
        self.rows_path = self._asset(self.metadata['rows']['path'])
        if _hash(self.rows_path) != self.metadata['rows']['sha256']:
            raise ValueError('保存轨迹 SHA256 不匹配，拒绝回放')
        self._groups = {}
        for line in self.rows_path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            key = row['episode_id'], row['native_owner']
            self._groups.setdefault(key, []).append(row)
        if not self._groups:
            raise ValueError('No saved replay rows')
        for key, rows in self._groups.items():
            rows.sort(key=lambda row: row['tick'])
            if len({r['tick'] for r in rows}) != len(rows):
                raise ValueError('Duplicate episode/owner/tick')
            if len({r['split'] for r in rows}) != 1:
                raise ValueError('Episode/owner spans multiple splits')
            for row in rows:
                for name, width in self.metadata['feature_dims'].items():
                    if len(row[name]) != width or not all(math.isfinite(v) for v in row[name]):
                        raise ValueError('Saved feature dimensions/values invalid')
        self.provenance = {
            'metadata': self.metadata_path, 'metadata_sha256': _hash(path),
            'rows': self.metadata['rows']['path'], 'rows_sha256': _hash(self.rows_path),
            'version_fingerprint_sha256': self.metadata.get('version_fingerprint_sha256'),
            'source_scope': self.metadata.get('evidence_scope', 'saved_derived_data'),
            'source_fidelity': self.metadata.get('fidelity'),
            'feature_sources': copy.deepcopy(self.metadata.get('feature_sources', [])),
            'native_rpc_calls': 0,
            'raw_states_available': False,
            'raw_status': '此回放接口仅加载 fair 向量，不读取原生 states 或画面；不能还原未编码单位与最终部署点',
        }
        self.reset()

    def _local(self, value):
        path = (self.root / value).resolve()
        path.relative_to(self.root)
        return path

    def _asset(self, value):
        path = self._local(value)
        entry = self._assets.get(str(value))
        if not path.is_file() and entry:
            path = self._local(entry['source'])
        if not path.is_file():
            raise FileNotFoundError('缺少保存资产，请先运行 restore: ' + str(value))
        if entry and _hash(path) != entry['sha256']:
            raise ValueError('便携资产 SHA256 不匹配: ' + str(value))
        return path

    def catalog(self):
        with self.lock:
            return {'schema': 'cloud-replay-catalog.v1', 'mode': 'saved_fair_replay',
                'native_rpc_calls': 0, 'episodes': [
                    {'episode_id': episode, 'owner': owner, 'split': rows[0]['split'],
                     'frames': len(rows), 'first_tick': rows[0]['tick'], 'last_tick': rows[-1]['tick']}
                    for (episode, owner), rows in sorted(self._groups.items())],
                'provenance': copy.deepcopy(self.provenance)}

    def reset(self, episode_id=None, owner=None):
        if owner is not None and (type(owner) is not int or owner not in (0, 1)):
            raise ValueError('owner 必须为 0 或 1')
        with self.lock:
            available = [key for key in sorted(self._groups)
                if (episode_id is None or key[0] == episode_id)
                and (owner is None or key[1] == owner)]
            if not available:
                raise ValueError('Unknown saved episode/owner')
            self.key = available[0]
            self.index = 0
            return self.state()

    def seek(self, index):
        if type(index) is not int:
            raise ValueError('index 必须为整数')
        with self.lock:
            if not 0 <= index < len(self._groups[self.key]):
                raise ValueError('回放帧超出范围')
            self.index = index
            return self.state()

    def step(self, delta=1):
        if type(delta) is not int:
            raise ValueError('delta 必须为整数')
        with self.lock:
            return self.seek(max(0, min(len(self._groups[self.key])-1, self.index+delta)))

    def _decode(self, row):
        spec = self.metadata['feature_spec']; cards = spec['cards']; n = len(cards)
        vector = row['history']; hand = []; towers = []; history = []

        def card_id(values):
            selected = [cards[i] for i, value in enumerate(values[:n]) if value > .5]
            return selected[0] if len(selected) == 1 and values[n] < .5 else None

        for slot in range(4):
            start = 8 + slot*spec['widths']['hand']; values = vector[start:start+spec['widths']['hand']]
            if values[0] > .5:
                hand.append({'slot': slot, 'card_id': card_id(values[1:]),
                    'cost': round(values[n+2]*10, 6) if values[n+3] > .5 else None,
                    'affordable': bool(values[n+4] > .5)})
        start = 8 + 4*spec['widths']['hand']
        for side in ('own', 'opponent'):
            for role in ('left', 'right', 'king'):
                values = vector[start:start+6]; start += 6
                towers.append({'side': side, 'role': role, 'recorded': values[0] > .5,
                    'present': bool(values[1] > .5) if values[0] > .5 else None,
                    'hp': round(values[2]*10000, 6) if values[3] > .5 else None,
                    'max_hp': round(values[4]*10000, 6) if values[5] > .5 else None})
        # Fair pilot deliberately excludes entities. Zeros denote masked unknown,
        # not evidence that the battlefield contains no troops.
        entity_count = sum(vector[start+i*spec['widths']['entity']] > .5 for i in range(spec['max_entities']))
        start += spec['max_entities']*spec['widths']['entity']
        for i in range(spec['history_steps']):
            values = vector[start+i*spec['widths']['history']:start+(i+1)*spec['widths']['history']]
            if values[0] > .5:
                tick = round(row['tick'] - values[2]*20*30)
                if tick > row['tick']:
                    raise ValueError('Saved fair history contains a future event')
                history.append({'tick': tick, 'side': 'own' if values[1] > .5 else 'opponent',
                    'card_id': card_id(values[3:]),
                    'cost': round(values[n+4]*10, 6) if values[n+5] > .5 else None,
                    'position': None, 'position_status': '历史向量没有编码落点'})
        return {'tick': row['tick'], 'seconds': row['tick']/self.metadata['ticks_per_second'],
            'own_elixir': vector[1]*10, 'hand_complete': vector[2] > .5,
            'crowns': {'own': round(vector[3]*3), 'opponent': round(vector[4]*3)},
            'elapsed_seconds': vector[6] if vector[7] > .5 else None,
            'hand': hand, 'towers': towers, 'history': history,
            'entities': None, 'encoded_entity_slots': entity_count,
            'entities_status': 'unknown：fair 保存输入没有证明可见性的非塔单位，不表示战场为空',
            'recorded_affordability_mask': list(row['mask']),
            'legality_status': '保存的可负担 mask；不等于新原生状态的完整合法性',
            'previous_action_index': row['previous_action'], 'previous_feedback': list(row['feedback'])}

    def public(self):
        """Current actor-time data only: no label, future target, or future terminal."""
        with self.lock:
            rows = self._groups[self.key]; row = rows[self.index]
            return {'schema': 'cloud-replay-public.v1', 'episode_id': self.key[0], 'owner': self.key[1],
                'split': row['split'], 'index': self.index, 'frames': len(rows),
                'at_first_frame': self.index == 0, 'at_last_saved_frame': self.index == len(rows)-1,
                'mode': 'saved_fair_replay', 'observation': self._decode(row), 'native_rpc_calls': 0}

    def state(self):
        with self.lock:
            row = self._groups[self.key][self.index]
            state = self.public()
            state['provenance'] = copy.deepcopy(self.provenance)
            state['label'] = {key: copy.deepcopy(row.get(key)) for key in (
                'action_index', 'card_id', 'xy', 'position_index', 'position_mask',
                'planned_tick', 'actual_execution_tick', 'action_semantics', 'wait_semantics', 'loss_mask')}
            state['label']['requested_native_xy'] = [round(row['xy'][0]*17999), round(row['xy'][1]*31999)] if row['position_mask'] else None
            state['label']['information_boundary'] = '当前帧监督标签可以包含随后执行记录；不属于 actor fair 输入。请求坐标不等于实际部署点。'
            state['transition_label'] = {'terminated': row['terminated'], 'truncated': row['truncated'],
                'next_tick': row['tick'] + row['transition_dt_ticks'],
                'semantics': '下一保存转移的结束标签；当前原生终止画面未提供，不据此伪造最终状态'}
            return state
