"""Offline public event JSON import for the pinned native backend.

The source remains an input record, never an official state/seed attestation.
No Android, ADB, RPC, package installation or dataset download happens here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from .runtime import ROOT, local

SCHEMA = 'royale-public-replay-plan.v1'
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PLAN_BYTES = 8 * 1024 * 1024


class PublicReplayError(ValueError):
    """An actionable source-format or pinned-runtime compatibility error."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _decode(raw):
    def reject(value):
        raise PublicReplayError(f'JSON 含非有限数值 {value}；请修正来源文件。')
    try:
        result = json.loads(raw, parse_constant=reject)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PublicReplayError(f'无法读取 JSON：{exc}') from exc
    if not isinstance(result, dict):
        raise PublicReplayError('JSON 根节点须为对象，支持 payload_json 行、battle/events 或 source episode。')
    return result


def _payload(mapping):
    """Retain source-only episode fields while recovering its raw event objects."""
    source_format = 'royaleapi_structured_payload'
    if 'payload_json' in mapping:
        value = mapping['payload_json']
        if not isinstance(value, (str, dict)):
            raise PublicReplayError('payload_json 须为 JSON 文本或对象。')
        payload = _decode(value) if isinstance(value, str) else deepcopy(value)
        if mapping.get('replay_tag') is not None and mapping['replay_tag'] != payload.get('source', {}).get('replay_tag'):
            raise PublicReplayError('行 replay_tag 与 payload.source.replay_tag 不一致。')
        if mapping.get('event_count') is not None and mapping['event_count'] != len(payload.get('events', [])):
            raise PublicReplayError('行 event_count 与 payload.events 数量不一致。')
        source_format = 'il_replay_row'
    elif mapping.get('schema_version') == 'royale-phase2-source-episode.v1':
        config = mapping.get('match_config', {})
        players = config.get('players', [])
        battle = {'game_mode': config.get('game_mode'), 'battle_type': config.get('battle_type')}
        terminal = mapping.get('reference_terminal', {})
        battle['result'] = terminal.get('result_from_team_view')
        for side in ('team', 'opponent'):
            selected = [p for p in players if p.get('source_side') == side]
            if len(selected) != 1:
                raise PublicReplayError(f'source episode 的 {side} 需要恰好一名玩家。')
            battle[side] = {'players': [{'deck': deepcopy(selected[0].get('deck')),
                'tower_card': deepcopy(selected[0].get('tower_card'))}],
                'crowns': terminal.get('crowns', {}).get(side)}
        actions = mapping.get('action_journal')
        if not isinstance(actions, list) or any(not isinstance(a, dict) or not isinstance(a.get('raw_event'), dict) for a in actions):
            raise PublicReplayError('source episode 必须保留每条 action_journal.raw_event；请使用原始事件 JSON。')
        payload = {'battle': battle, 'events': [deepcopy(a['raw_event']) for a in actions],
                   'source': {'replay_tag': mapping.get('episode_id'), 'anonymized': True},
                   'replay': {'duration': terminal.get('source_duration')}}
        source_format = 'phase2_source_episode'
    else:
        payload = deepcopy(mapping)
    if not isinstance(payload.get('battle'), dict) or not isinstance(payload.get('events'), list):
        raise PublicReplayError('缺少 battle/events。需要双方各八卡配置及逐动作记录，普通战绩摘要不能执行。')
    return payload, source_format


def _converter():
    folder = local('third_party/phase2/firstlight')
    os.environ['CR_WORKSPACE_ROOT'] = str(folder)
    os.environ['CR_COMPETITIVE_DATA_ROOT'] = str(folder/'native_runner/data/competitive')
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    try:
        from native_runner.royaleapi_replay import prepare_collected_replay
        from native_runner.match_factory import MatchConfig
    except (ImportError, FileNotFoundError) as exc:
        raise PublicReplayError('缺少已固定的 FirstLight 转换器/卡牌表；请恢复项目 third_party/phase2/firstlight。') from exc
    return prepare_collected_replay, MatchConfig


def _validate_payload(payload):
    """Guard against upstream coercion, sorting, mixed-level normalization or skill inference."""
    levels = []
    for side in ('team', 'opponent'):
        side_data = payload['battle'].get(side)
        players = side_data.get('players') if isinstance(side_data, dict) else None
        if not isinstance(players, list) or len(players) != 1:
            raise PublicReplayError(f'battle.{side}.players 须恰好一名玩家；当前只支持 1v1。')
        deck = players[0].get('deck')
        if not isinstance(deck, list) or len(deck) != 8:
            raise PublicReplayError(f'{side} 需要完整八卡 deck（card_key、level）。')
        for card in deck:
            if not isinstance(card, dict) or not isinstance(card.get('card_key'), str):
                raise PublicReplayError(f'{side} 的每张卡需要 card_key。')
            value = card.get('level')
            if value is not None:
                if type(value) is not int or not 1 <= value <= 16:
                    raise PublicReplayError(f'{side} 卡牌等级须为 1..16 的整数；当前固定版本不能保留 {value!r}。')
                levels.append(value)
        tower = players[0].get('tower_card')
        if isinstance(tower, dict) and tower.get('level') is not None:
            value = tower['level']
            if type(value) is not int or not 1 <= value <= 16:
                raise PublicReplayError(f'{side} 塔兵等级须为 1..16 的整数。')
            levels.append(value)
    if len(set(levels)) > 1:
        raise PublicReplayError('原局存在不同卡牌/塔兵等级；当前固定 match 只支持统一等级，拒绝静默改为众数。请使用等级一致的来源或先实现逐卡等级配置。')
    events = payload['events']
    if not events:
        raise PublicReplayError('events 为空；请提供至少一条明确的出牌事件。')
    previous = -1
    indices = set()
    last_index = None
    flags = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise PublicReplayError(f'events[{index}] 必须为对象。')
        kind = event.get('kind')
        if kind != 'play_card':
            raise PublicReplayError(f'events[{index}] 的 {kind!r} 暂不能无歧义执行：当前导入器只接 play_card，不推断技能来源。请保留完整原件，改用已有原生 journal 或补充受支持的明确技能接口。')
        if event.get('side') not in ('team', 'opponent'):
            raise PublicReplayError(f'events[{index}].side 须为 team/opponent。')
        tick = event.get('replay_tick_20hz')
        if type(tick) is not int or tick < 90:
            raise PublicReplayError(f'events[{index}].replay_tick_20hz 须为不小于 90 的整数；固定引擎首个可操作状态为 tick 90，不移动来源时间。')
        if tick < previous:
            raise PublicReplayError(f'events[{index}] 时间倒序；请按来源顺序提供事件，不自动排序。')
        source_index = event.get('source_index', index)
        if type(source_index) is not int or source_index < 0 or source_index in indices:
            raise PublicReplayError(f'events[{index}].source_index 须唯一且为非负整数。')
        if tick == previous and last_index is not None and source_index <= last_index:
            raise PublicReplayError(f'events[{index}] 同 tick 的 source_index 顺序冲突；请核对原始事件顺序。')
        seconds = event.get('time_seconds')
        if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or abs(seconds * 20 - tick) > 1e-6):
            raise PublicReplayError(f'events[{index}] 的 time_seconds 与 20 Hz tick 不一致；请核对时间单位。')
        fields = event.get('source_fields', {})
        flag = fields.get('data_i') if isinstance(fields, dict) else None
        if flag is not None:
            if type(flag) is not int or flag not in (0, 1):
                raise PublicReplayError(f'events[{index}].source_fields.data_i 须为 0/1。')
            flags.add(flag)
        coords = event.get('coordinates')
        if not isinstance(coords, dict):
            raise PublicReplayError(f'events[{index}] 缺少 coordinates。请提供 native_world_units 的 x/y。')
        if coords.get('inside_18x32_arena') is False:
            raise PublicReplayError(f'events[{index}] 来源明确标记落点不在竞技场；请核对来源坐标，不能覆盖该标记。')
        native = coords.get('native_world_units')
        if isinstance(native, dict):
            x, y = native.get('x'), native.get('y')
            if (type(x) not in (int, float) or type(y) not in (int, float) or
                    not math.isfinite(x) or not math.isfinite(y) or not 0 <= x < 18000 or not 0 <= y < 32000):
                raise PublicReplayError(f'events[{index}] 原生坐标须满足 0≤x<18000、0≤y<32000；不裁剪来源落点。')
            grid = {'x': int(x//1000), 'y': int(y//1000)}
            offset = {'x': x/1000-grid['x']-.5, 'y': y/1000-grid['y']-.5}
            if 'grid_cell_floor' in coords and coords['grid_cell_floor'] != grid:
                raise PublicReplayError(f'events[{index}] grid_cell_floor 与 native_world_units 冲突。')
            given_offset = coords.get('subcell_offset_from_floor_center')
            if given_offset is not None and (not isinstance(given_offset, dict) or any(
                    type(given_offset.get(k)) not in (int, float) or not math.isfinite(given_offset[k])
                    or abs(given_offset[k]-offset[k]) > 1e-6 for k in ('x', 'y'))):
                raise PublicReplayError(f'events[{index}] 格内偏移与 native_world_units 冲突。')
            coords.setdefault('grid_cell_floor', grid)
            coords.setdefault('subcell_offset_from_floor_center', offset)
            coords['inside_18x32_arena'] = True
        elif not isinstance(coords.get('grid_cell_floor'), dict):
            raise PublicReplayError(f'events[{index}] 需要 native_world_units 或明确的 grid_cell_floor。屏幕像素不可直接当原生坐标。')
        else:
            grid = coords['grid_cell_floor']
            if any(type(grid.get(k)) is not int or not 0 <= grid[k] < n for k, n in (('x', 18), ('y', 32))):
                raise PublicReplayError(f'events[{index}] grid_cell_floor 越界。')
            offset = coords.get('subcell_offset_from_floor_center', {'x': 0, 'y': 0})
            if not isinstance(offset, dict) or any(type(offset.get(k)) not in (int, float)
                    or not math.isfinite(offset[k]) or not -.5 <= offset[k] < .5 for k in ('x', 'y')):
                raise PublicReplayError(f'events[{index}] 格内偏移须位于 [-0.5,0.5)。')
            coords['inside_18x32_arena'] = True
        previous, last_index = tick, source_index
        indices.add(source_index)
    if len(flags) != 1:
        raise PublicReplayError('缺少一致的 source_fields.data_i（0/1），无法确定 RoyaleAPI 真蓝/真红坐标变换。请保留采集器该字段，不猜玩家朝向。')


@dataclass(frozen=True)
class PublicReplayPlan:
    _data: dict

    @classmethod
    def load(cls, path):
        source_path = local(path)
        if source_path.stat().st_size > MAX_PLAN_BYTES:
            raise PublicReplayError('公开对局文件超过 8 MiB 计划读取预算。')
        raw = source_path.read_bytes()
        value = _decode(raw)
        if value.get('schema') == SCHEMA:
            return cls.from_dict(value)
        if len(raw) > MAX_SOURCE_BYTES:
            raise PublicReplayError('公开事件 JSON 超过 2 MiB；请导出单局而不是整个数据集。')
        return cls.from_mapping(value, source_bytes=raw)

    @classmethod
    def from_mapping(cls, mapping, *, source_bytes=None):
        if not isinstance(mapping, dict):
            raise PublicReplayError('公开事件输入必须为 JSON 对象。')
        raw_json = _json(mapping).encode('utf-8')
        if len(raw_json) > MAX_SOURCE_BYTES:
            raise PublicReplayError('公开事件 JSON 超过 2 MiB；请导出单局。')
        payload, source_format = _payload(mapping)
        original_payload = deepcopy(payload)
        _validate_payload(payload)
        source = payload.setdefault('source', {})
        if not isinstance(source, dict):
            raise PublicReplayError('source 必须为对象。')
        tag = source.get('replay_tag')
        generated_id = not tag
        if generated_id:
            source.update(replay_tag=_sha(original_payload)[:32], anonymized=True)
        elif len(str(tag)) == 32 and all(c in '0123456789abcdef' for c in str(tag)):
            source['anonymized'] = True
        convert, MatchConfig = _converter()
        try:
            prepared = convert(payload)
            converted = prepared.replay.to_dict()
            config = converted['episode_config']; tags = config['tags']
            options = {k: config[k] for k in ('deck0', 'deck1', 'seed', 'game_mode', 'arena')}
            for name in ('location', 'level_cap', 'minimum_card_level', 'king_tower_level',
                         'tower_troop0_id', 'tower_troop1_id', 'deck0_form_availability', 'deck1_form_availability'):
                if name in tags: options[name] = tags[name]
            # Reuse the reviewed portable schema, since the upstream optional
            # native_match.json is deliberately absent from the cloud package.
            from royale_phase3.actions import MATCH_TEMPLATE, MATCH_TEMPLATE_SHA256
            template = local(MATCH_TEMPLATE)
            if hashlib.sha256(template.read_bytes()).hexdigest() != MATCH_TEMPLATE_SHA256:
                raise PublicReplayError('项目原生配置模板 SHA256 不一致；请恢复已审查的 configs/native 模板。')
            match = MatchConfig(**options, end_tick=7200).to_replay_dict(template_path=template)
            commands = []
            for operation in prepared.replay.operations:
                for action in operation.actions:
                    if action.kind.value == 'wait':
                        continue  # Upstream control padding, never a source event.
                    item = action.to_dict(); i = item['metadata']['source_event_index']
                    grid, offset = item['target_grid'], item.get('subcell_offset') or [0, 0]
                    xy = [int(grid[k])*1000+500+int(round(offset[k]*1000)) for k in (0, 1)]
                    # This explicit integer conversion is the pinned native interface rule.
                    xy = [min(max(xy[0], 0), 17999), min(max(xy[1], 0), 31999)]
                    tick = operation.start_native_tick + max(1, action.execute_offset_ticks or 1)
                    event = original_payload['events'][i]
                    commands.append({'kind': item['kind'], 'owner': item['owner'], 'card_id': item['card_id'],
                        'x': xy[0], 'y': xy[1], 'tick': tick, 'target_engine_tick': tick,
                        'source_event_index': i, 'source_index': event.get('source_index', i),
                        'source_tick': event['replay_tick_20hz'], 'source_side': event['side'],
                        'request_id': f'public:{source["replay_tag"]}:{i}'})
        except PublicReplayError:
            raise
        except Exception as exc:
            raise PublicReplayError(f'当前固定版本无法转换该局：{exc}。请核对 card_key、完整卡组、坐标和来源 data_i；原始动作不会被跳过。') from exc
        if [c['source_event_index'] for c in commands] != list(range(len(payload['events']))):
            raise PublicReplayError('转换器改变了来源动作顺序或数量，已拒绝导入；请保留原始文件检查。')
        missing = ['original_rng_seed', 'original_initial_hand', 'original_initial_cycle',
                   'original_starting_elixir', 'original_king_tower_level', 'original_build', 'balance_date']
        assumptions = {
            'initial_hand': '按完整公开出牌顺序选一组可执行初手/轮转；不是原始隐藏状态',
            'native_rng_seed': {'value': options['seed'], 'basis': '固定 FirstLight NATIVE_DEAL_SEED；不是来源 RNG'},
            'timing': '来源 20 Hz command boundary 加 1 tick；不移除/挪动源动作',
            'coordinates': '固定 FirstLight data_i 真红/真蓝变换；格心+格内偏移转整数原生坐标，边界按0..17999/31999限定',
            'levels': {'uniform_level': tags['level_cap'], 'known_source_levels_changed': False,
                       'unknown_levels': '缺少的卡等沿用本次统一等级；并非已恢复真值'},
            'king_tower_level': '使用本次统一等级；源国王塔等级未知',
            'game_mode_arena': {'game_mode': options['game_mode'], 'arena': options['arena'],
                                'basis': '固定转换器按来源模式选择本地配置'},
            'missing_source_fields': missing, 'generated_replay_id': generated_id,
            'official_trajectory_verified': False, 'native_execution_verified': False,
        }
        source_summary = {'replay_tag': source['replay_tag'], 'format': source_format,
            'events': len(commands), 'play_card_events': len(commands),
            'first_source_tick': commands[0]['source_tick'], 'last_source_tick': commands[-1]['source_tick'],
            'source_result': original_payload.get('battle', {}).get('result'),
            'warnings': list(prepared.warnings), 'missing_initialization': missing}
        data = {'schema': SCHEMA, 'source_id': _sha(mapping)[:24], 'initialization_tick': 90,
            'source_end_tick': commands[-1]['source_tick'], 'match': match, 'commands': commands,
            'opening_cards_hypothesis': deepcopy(prepared.opening_cards),
            'cycle_hypothesis': deepcopy(prepared.queue_cards), 'assumptions': assumptions,
            'source_summary': source_summary, 'normalized_payload': original_payload,
            'source': {'sha256': hashlib.sha256(source_bytes if source_bytes is not None else raw_json).hexdigest(),
                       'mapping_sha256': _sha(mapping), 'payload_sha256': _sha(original_payload), 'format': source_format},
            'converter': {'path': 'third_party/phase2/firstlight/native_runner/royaleapi_replay.py',
                'sha256': hashlib.sha256(local('third_party/phase2/firstlight/native_runner/royaleapi_replay.py').read_bytes()).hexdigest(),
                'match_template': MATCH_TEMPLATE, 'match_template_sha256': MATCH_TEMPLATE_SHA256}}
        return cls(data)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get('schema') != SCHEMA:
            raise PublicReplayError('不是受支持的公开对局计划。')
        payload = value.get('normalized_payload')
        if not isinstance(payload, dict) or _sha(payload) != value.get('source', {}).get('payload_sha256'):
            raise PublicReplayError('计划中的来源 payload 哈希不一致。')
        # Reconvert instead of trusting caller-supplied executable commands/match.
        regenerated = cls.from_mapping(payload).to_dict()
        for key in ('match', 'commands', 'opening_cards_hypothesis', 'cycle_hypothesis', 'initialization_tick', 'source_end_tick'):
            if _json(value.get(key)) != _json(regenerated[key]):
                raise PublicReplayError(f'计划 {key} 与固定转换器/来源不一致；请从原始 JSON 重新导入。')
        return cls(deepcopy(value))

    def to_dict(self):
        return deepcopy(self._data)

    def __getattr__(self, name):
        if name in self._data:
            return deepcopy(self._data[name])
        raise AttributeError(name)


def import_public_replay(payload, output, *, filename=None, source_bytes=None):
    """Save one fresh offline import directory without touching source/history."""
    plan = PublicReplayPlan.from_mapping(payload, source_bytes=source_bytes)
    raw = source_bytes if source_bytes is not None else _json(payload).encode('utf-8')
    if len(raw) > MAX_SOURCE_BYTES:
        raise PublicReplayError('单局原始 JSON 超过 2 MiB。')
    if source_bytes is not None and _json(_decode(source_bytes)) != _json(payload):
        raise PublicReplayError('source_bytes 与解析后的 payload 不一致。')
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'raw.json').write_bytes(raw)
    (folder/'plan.json').write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    report = {'schema': 'royale-public-replay-import.v1', 'status': 'imported', 'source_id': plan.source_id,
        'filename': Path(filename).name if filename else None,
        'raw_path': str((folder/'raw.json').relative_to(ROOT)),
        'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'plan_path': str((folder/'plan.json').relative_to(ROOT)),
        'report_path': str((folder/'report.json').relative_to(ROOT)),
        'source_summary': plan.source_summary, 'assumptions': plan.assumptions,
        'native_rpc_calls': 0, 'native_execution_verified': False}
    (folder/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report
