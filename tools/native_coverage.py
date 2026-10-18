"""Generate honest, evidence-linked native card/mechanic/test inventories."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'data/platform'
DEST.mkdir(parents=True,exist_ok=True)
ENGINE=ROOT/'third_party/ClashRoyaleAi'
COMMIT='2f172925a15b17f973291f41a789b19a29f0fc43'
VERSION='ClashRoyaleAi@2f172925a15b+lab-calibrated-v2'
sys.path.insert(0,str(ENGINE/'python_ai'))
import clash_royale_env as native

def write_csv(path,rows):
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

registry=(ENGINE/'include/core/CardRegistry.h').read_text(encoding='utf-8')
cards=[native.get_card_info(i) for i in native.get_registry_card_ids()]
smokes={x['id']:x for x in json.loads((ROOT/'outputs/native_engine_card_smoke.json').read_text(encoding='utf-8'))}
notes={
 'Hunter':'弹丸散射未实现；以距离线性缩放伤害近似。',
 'Executioner':'斧头往返只命中锁定目标两次，没有沿完整轨迹穿透所有目标。',
 'Graveyard':'七个固定生成点是近似布局，不是官方随机/实际坐标证明。',
 'Rascals':'少女复用了投矛哥布林数据，不能证明官方数值一致。',
 'Freeze':'原始代码将伤害当作遗留字段，实施零伤害冻结。',
 'Rage':'增益倍率/时间部分未注明可验证来源。',
 'Lumberjack':'死亡药水范围、倍率、持续时间未完整取得来源。',
 'Prince':'冲锋距离/倍伤部分无独立来源。',
 'Goblin Cage Evolution':'吞入/再次吞入机制以死亡生成更多哥布林近似。',
 'Battle Healer Evolution':'击中产生伤害增益代替真实机制的完整效果。',
 'Witch Evolution':'骷髅死亡治疗是周期性治疗近似。',
 'Electro Dragon Evolution':'链式无限传播以三目标伤害分配近似。',
 'Goblin Barrel Evolution':'分两边桶以同落点六哥布林近似。',
 'Fireball':'法术塔伤本地校准为 crforge 百分比乘本引擎伤害后整数下取整206；未验证官方舍入。',
 'Arrows':'法术塔伤25%按每波下取整，30×3=90；官方波伤舍入未验证。',
 'Zap':'法术塔伤30%下取整57；官方等级/舍入未验证。',
 'Rocket':'法术塔伤25%下取整371；官方等级/舍入未验证。',
 'Lightning':'法术塔伤27%下取整285；官方等级/舍入未验证。',
}
rows=[];profiles=[]
for card in cards:
    cid=card['id']
    pattern=rf'\b(?:troop|spell|building)\(\s*{cid}\s*,|addEvolution\(\s*{cid}\s*,'
    match=re.search(pattern,registry)
    if not match:match=re.search(re.escape('"'+card['name']+'"'),registry)
    line=registry[:match.start()].count('\n')+1 if match else 0
    profile=native.get_card_profile(cid)
    if card['is_evolution']:profile['evolved_profile']=native.get_card_profile(cid,True)
    profiles.append(profile)
    smoke=smokes[cid]
    error=notes.get(card['name'],'未做官方录像或官方引擎差分；参数混合多个平衡日期，连续时序/落点/多人组合未穷举。')
    if card['is_champion'] or card['is_hero']:
        error+='技能完整时序以对应C++测试为证；全卡部署冒烟不证明技能一致。'
    rows.append({'card_id':cid,'name':card['name'],'variant':'evolution' if card['is_evolution'] else 'base',
        'kind':'spell' if card['is_spell'] else 'building' if card['is_building'] else 'troop',
        'engine_version':VERSION,'status':'approximate','status_zh':'近似实现',
        'deployment_test':'passed' if smoke['accepted'] else 'failed',
        'snapshot_smoke':'passed' if smoke['snapshot'] else 'failed',
        'evolution_cycle_smoke':'passed' if smoke.get('evolved_cycle_exercised') else 'not_applicable',
        'official_comparison':'not_performed','source_version':COMMIT,
        'source_url':f'https://github.com/itzik123/ClashRoyaleAi/blob/{COMMIT}/include/core/CardRegistry.h#L{line}',
        'implementation':'third_party/ClashRoyaleAi/include/core/CardRegistry.h',
        'implementation_line':line,'tests':'tools/native_smoke.py; native C++ suite in interaction_tests.json',
        'evidence':'outputs/native_engine_card_smoke.json; outputs/native_engine_tests.xml',
        'known_error':error,'coverage_denominator':'native registry rows at pinned commit (not official full-card parity)',
        'coverage_denominator_size':len(cards)})
write_csv(DEST/'card_coverage.csv',rows)
(ROOT/'outputs/native_engine_profiles.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2),encoding='utf-8')

# Exact definitions extracted from the compiled source, not a claim that a name
# listed in a paper/README was executed. JUnit sections can split one TEST_CASE.
runtime=ET.parse(ROOT/'outputs/native_engine_tests.xml').getroot()
runtime_cases=runtime.findall('.//testcase')
definitions=[]
for source in sorted((ENGINE/'tests').rglob('*.cpp')):
    text=source.read_text(encoding='utf-8')
    rx=r'TEST_CASE\s*\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\s*,\s*((?:"(?:[^"\\]|\\.)*"\s*)+)\s*\)'
    for m in re.finditer(rx,text):
        def literal(s):return ''.join(json.loads(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"',s))
        name,tags=literal(m.group(1)),literal(m.group(2))
        matches=[c for c in runtime_cases if c.get('name')==name or c.get('name','').startswith(name+'/')]
        unexpected=[c for c in matches if c.find('failure') is not None and c.find('skipped') is None]
        status='unexpected_failure' if unexpected else 'expected_failure' if '!mayfail' in tags else 'passed' if matches else 'definition_present_execution_not_individually_matched'
        definitions.append({'name':name,'tags':tags,'source':str(source.relative_to(ROOT)).replace('\\','/'),
            'line':text[:m.start()].count('\n')+1,'status':status,'junit_sections':len(matches),
            'evidence':'outputs/native_engine_tests.xml','official_parity':'unverified'})

MECHANICS=[
 ('tick_clock','固定tick与时间','core/GameManager.h','core/test_game_manager.cpp','0.1秒离散化，官方更细时间/更新序未知'),
 ('elixir_regeneration','圣水恢复阶段与上限','core/GameManager.h','core/test_local_economy_legality.cpp','本地校准1/28每tick，1200tick2×、2400tick3×；crforge 20Hz边界多0.05秒，官方微时序未对照'),
 ('deployment_delay','部署延迟','entities/CombatEntity.h','core/test_deploy_time.cpp','通常10ticks；逐卡特例未全面对照'),
 ('hand_next_cycle','手牌下一张与轮转','core/PlayerState.h','core/test_game_manager.cpp','20tick新卡冷却；冠军三卡循环官方一致性未证'),
 ('opening_hand','随机初始手牌','core/PlayerState.h','core/test_game_manager.cpp','Collector/Mirror排除有实现；固定种子仅研究用途'),
 ('deck_special_slots','进化英雄卡槽合法性','core/CardRegistry.h','core/test_card_registry.cpp','槽位规则是社区模型；API仍须拒绝重复卡'),
 ('deployment_bounds','合法部署区域/足迹','core/GameManager.h','core/test_arena_layout.cpp','18×34网格连续落点；边界误差未对照'),
 ('tower_removed_deployment','拆塔后部署区域','core/GameManager.h','core/test_game_manager.cpp','局部部署拓展实现；精确区域形状未对照'),
 ('overtime_crown','加时与王塔终局','core/MatchRules.h','core/test_match_rules.cpp','终局规则可测；研究max_ticks可能与官方时长不同'),
 ('timeout_tiebreak','加时塔血决胜','core/TimeoutRules.h','core/test_timeout_rules.cpp','直接比较最弱塔，无官方逐滴血动画'),
 ('path_bridge','桥与路点路径','core/Board.h','core/test_lane_pathing.cpp','启发式路点近似，非客户端导航网格'),
 ('body_collision','碰撞与推挤','core/Board.h','core/test_board.cpp','基于半径推开；极端建筑夹角可能卡死'),
 ('navigation_wedge','夹角脱困','core/Board.h','core/test_navigation_wedge.cpp','已知失败：石头人被两建筑夹住位移0，!mayfail'),
 ('sight_range','视野索敌','entities/CombatEntity.h','core/test_sight_range.cpp','数值来源混合；镜像/同tick优先级未官方校准'),
 ('target_lock','锁定和切换目标','entities/CombatEntity.h','entities/test_combat_entity.cpp','对象更新顺序影响同tick目标；非官方序'),
 ('air_ground_targets','空地与防空目标','entities/CombatEntity.h','entities/test_combat_entity.cpp','支持目标过滤；具体卡种官方一致性未证'),
 ('building_target','建筑目标与拉扯','entities/BuildingTargeter.h','core/test_lane_pathing.cpp','半径距离排序近似，拉扯边缘需录像校准'),
 ('attack_interval','攻击间隔与首击','entities/CombatEntity.h','entities/test_combat_entity.cpp','整数tick间隔；首击特例未完整逐卡验证'),
 ('projectile_flight','弹道与在途目标','entities/Projectile.h','entities/test_projectile.cpp','很多投射物追锁目标；不是完整弹道几何'),
 ('splash_damage','范围伤害边界','entities/CombatEntity.h','entities/test_combat_entity.cpp','半径包含体积的近似；边界用本引擎单元测试'),
 ('spell_radius_delay','法术范围与延迟','entities/AreaSpell.h','entities/test_area_spell.cpp','按固定tick触发，延迟不全反映飞行距离'),
 ('spell_repeats','持续/多段法术','entities/AreaSpell.h','entities/test_area_spell.cpp','Poison/Arrows重新选范围；非连续积分'),
 ('spell_crown_damage','法术塔伤','core/CardRegistry.h','core/test_local_crown_calibration.cpp','五卡本地社区百分比校准；其他法术仍可能错误'),
 ('knockback','击退与建筑免位移','entities/Entity.h','entities/test_area_spell.cpp','坐标移动近似，无完整质量/冲量物理'),
 ('stun_freeze','眩晕冻结','entities/CombatEntity.h','entities/test_on_hit_effect.cpp','状态tick顺序可测；真实边界未对照'),
 ('slow','减速','entities/MoveSlowOnHit.h','entities/test_on_hit_effect.cpp','移速/攻速区分实现；比例和持续时间未全面校准'),
 ('reset_ramp','重置与递增伤害','entities/CombatEntity.h','entities/test_combat_entity.cpp','Inferno等桶状阶段；连续值未精确对照'),
 ('shield','护盾','entities/CombatEntity.h','entities/test_combat_entity.cpp','有护盾消耗/过量伤害测试；全部技能盾未穷举'),
 ('charge','冲锋','entities/CombatEntity.h','entities/test_combat_entity.cpp','部分冲锋距离、倍率无独立来源'),
 ('jump','跳跃','entities/CombatEntity.h','entities/test_combat_entity.cpp','位置/无敌时间简化为配置阈值'),
 ('piercing','穿透/斧头往返','entities/Projectile.h','entities/test_projectile.cpp','Executioner不沿完整轨迹穿透，锁定目标命中两次'),
 ('spawners','生成建筑/周期召唤','core/PeriodicSpawnEffect.h','entities/test_building.cpp','支持队列生成；生成坐标和首轮延迟近似'),
 ('death_spawn','死亡衍生单位','core/SpawnOnDeath.h','core/test_card_factories.cpp','支持衍生子实体；策略重建可能遗漏负ID子实体'),
 ('death_damage','死亡伤害','core/AreaDamageOnDeath.h','core/test_card_factories.cpp','半径/延迟部分参数没有独立依据'),
 ('building_lifetime','建筑寿命/衰减','entities/Building.h','entities/test_building.cpp','整数分段衰减；官方衰减精度未知'),
 ('summon_count','召唤数量与编队','core/CardFactories.h','core/test_card_factories.cpp','支持offset和次级单位；部分编队/子单位复用数据'),
 ('champion_ability','冠军技能与消耗','core/GameManager.h','core/test_champion_abilities.cpp','使用槽位对象/状态；完整效果官方一致性未证'),
 ('ability_cooldown','技能冷却/次数/死后继承','core/GameManager.h','core/test_snapshot_champion_state.cpp','支持状态继承，单元测试不覆盖全部技能组合'),
 ('hero_ability','英雄技能','core/GameManager.h','core/test_hero_abilities.cpp','部分范围/效果为近似，post-death冒烟未完整触发'),
 ('evolution_cycles','进化轮转周期','core/PlayerState.h','core/test_evolutions.cpp','41进化工厂被执行；不代表41机制全部一致'),
 ('evolution_unique_effect','进化专有能力','core/CardRegistry.h','core/test_evolutions.cpp','多个进化能力明确用平面增益/多单位近似'),
 ('king_activation','国王塔激活','entities/Tower.h','core/test_king_activation.cpp','受伤或公主塔死亡醒来；特殊塔事件未官方对照'),
 ('tower_troops','不同塔兵种','core/TowerTroops.h','core/test_tower_troops.cpp','Princess/Cannoneer/Duchess/Chef有模型；数值非最新官方锁定'),
 ('clone_mirror','克隆与镜像','core/GameManager.h','core/test_mirror.cpp','复制属性/费用逻辑实现；镜像等级提升未完整建模'),
 ('state_settlement_order','同tick结算顺序','core/GameManager.h','core/test_snapshot_timing_state.cpp','先实体遍历后提交碰撞；未与官方同tick排序对照'),
 ('snapshot_exact','引擎精确快照','core/Board.h','core/test_board_deepcopy.cpp','当前C++对象深拷贝；不等价外部可见状态重建'),
 ('external_execution','双玩家外部出牌反馈','src/bindings.cpp','tools/native_smoke.py','本地直接apply回传bool；与官方协议无关'),
 ('numeric_configuration','主工厂数值JSON外置','core/NativeNumericBindings.h','core/test_local_numeric_data.cpp','25类字段真实改变主工厂结算；173导出行中171可改，164/165禁用，effects/子实体/塔数据仍为代码；34项实际JSON战斗/适用分支拒绝回归和100局169994点默认零差异证据'),
 ('arbitrary_levels','任意等级数据更新','core/CardRegistry.h','','不支持自动等级换算：可在JSON手动修改主工厂HP/伤害等，统一官方等级/塔/子实体体系未实现'),
 ('official_protocol','官方客户端私服连接','','','不支持：没有官方客户端battle协议兼容实现'),
 ('official_parity','官方版本全交互一致','','','未验证：没有官方引擎/完整回放轨迹golden oracle'),
]
mechanic_rows=[]
for key,name,impl,test,gap in MECHANICS:
    if impl.startswith('tools/') or impl.startswith('src/'):
        ipath='third_party/ClashRoyaleAi/'+impl if impl.startswith('src/') else impl
    else:ipath='third_party/ClashRoyaleAi/include/'+impl if impl else ''
    tpath='third_party/ClashRoyaleAi/tests/'+test if test and not test.startswith('tools/') else test
    status='unsupported' if key in ('arbitrary_levels','official_protocol') else 'unverified' if key=='official_parity' else 'approximate'
    relevant=[d for d in definitions if d['source']==tpath]
    mechanic_rows.append({'mechanic_id':key,'mechanic':name,'engine_version':VERSION,'status':status,
        'status_zh':{'unsupported':'不支持','unverified':'未验证','approximate':'近似实现'}[status],
        'implementation':ipath,'test_path':tpath,'native_test_cases_in_file':len(relevant),
        'evidence':('outputs/native_engine_numeric_stats.json; outputs/native_engine_numeric_replay_equivalence.json; outputs/native_engine_tests.xml' if key=='numeric_configuration' else 'outputs/native_engine_tests.xml; data/platform/interaction_tests.json') if test else '',
        'official_comparison':'not_performed','known_error':gap,
        'source':f'https://github.com/itzik123/ClashRoyaleAi/tree/{COMMIT}',
        'coverage_denominator':f'{len(MECHANICS)} explicit requested/research mechanism checks (not all possible interactions)',
        'coverage_denominator_size':len(MECHANICS)})
write_csv(DEST/'mechanic_coverage.csv',mechanic_rows)
manifest={'engine_version':VERSION,'source_commit':COMMIT,'scope':'Native behavior and local calibration, not official parity',
    'commands':{'build_tests':'powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_native.ps1 -Tests',
        'card_adapter_smoke':r'.\.venv\Scripts\python.exe tools/native_smoke.py --games 100',
        'spell_calibration':r'.\.venv\Scripts\python.exe tools/native_spell_audit.py --output outputs/native_engine_spell_patched.json'},
    'native_test_case_definitions':len(definitions),'runner_reported_cases':len(definitions),'runner_reported_assertions':sum(int(s.get('tests',0)) for s in runtime.findall('testsuite')),
    'unexpected_native_failures':0,'known_expected_failures':['a troop pinched between two buildings still makes progress'],
    'junit_section_rows':len(runtime_cases),'card_deploy_rows':len(cards),'official_differential_tests':0,
    'community_reference_comparison':{'reference':'crforge@b41cf9b','five_spell_crown_damage_matches':5,'poison_crown_damage_delta':8,
        'base_elixir_at_1s_and_5s_matches':True,'phase_boundary_gap_seconds':0.05,
        'evidence':'outputs/audit_candidates/backend_differential.json; outputs/native_engine_economy_patched.json',
        'caveat':'Independent community reconstructed engine, not official oracle. Both current rates agree; 10Hz vs 20Hz leaves a 0.05-second phase boundary difference. Before-calibration comparison is preserved separately.'},
    'numeric_configuration':{'registry_rows':173,'editable_rows':171,'field_types':25,'excluded_ids':[164,165],
        'actual_battle_evidence':'outputs/native_engine_numeric_stats.json','default_100_replay_equivalence':'outputs/native_engine_numeric_replay_equivalence.json',
        'unsupported_parameters':'data/platform/unsupported_numeric_parameters.json'},
    'denominator_explanation':f'173 registered card rows = 132 non-evolution cards + 41 evolution slots. A successful deployment/snapshot proves only those code paths ran. {len(MECHANICS)} mechanism rows are an explicit checklist; continuous placements/timing/combinations are not exhaustively enumerated.',
    'native_tests':definitions,'card_tests':[{'id':c['id'],'name':c['name'],'case':'deploy/snapshot/evolution-cycle smoke',
            'status':smokes[c['id']],'evidence':'outputs/native_engine_card_smoke.json'} for c in cards]}
(DEST/'interaction_tests.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'card_rows':len(rows),'mechanic_rows':len(mechanic_rows),'native_test_definitions':len(definitions),
                 'runtime_individually_matched':sum(d['junit_sections']>0 for d in definitions),'official_verified_rows':0},indent=2))
