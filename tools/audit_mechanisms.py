"""Generate the requested mechanics denominator without asserting engine fidelity."""
from __future__ import annotations
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
 'economy_and_rules': [
  ('elixir_regen_phases','圣水恢复与单倍/双倍/三倍阶段'), ('elixir_cap','圣水上限与溢出'),
  ('deploy_delay','下牌同步与部署延迟'), ('hand_next_card','四张手牌与下一张'),
  ('card_cycle','八卡轮转'), ('placement_regions','合法落点与空地/法术区域'),
  ('post_tower_placement','拆塔后的可部署区域'), ('overtime','常规时间/加时/突然死亡'),
  ('tiebreak','终局比较和决胜减血规则'), ('levels','等级/稀有度数值缩放'),
  ('wait_timing','等待动作与下牌时机'),
 ],
 'movement_and_combat': [
  ('river_bridge_path','河流/桥/地面寻路'), ('collision_push','体积/碰撞/推挤'),
  ('sight_targeting','视野与索敌'), ('target_lock_switch','锁定/丢失/切换目标'),
  ('air_ground','空地目标掩码'), ('building_target','仅建筑目标/拉扯'),
  ('attack_period','攻击间隔'), ('first_hit','首击前摇与重置'),
  ('projectile','弹道/飞行/命中延迟'), ('splash','范围伤害与范围边界'),
  ('minimum_range','攻击最小距离'), ('invisibility','隐身/可见性/目标切换'),
 ],
 'spells_and_status': [
  ('spell_radius_duration','法术范围/持续时间'), ('spell_tower_damage','法术皇冠塔减伤'),
  ('knockback','击退与免疫'), ('stun','眩晕'), ('slow','减速与叠加'),
  ('freeze','冻结'), ('reset','电击/眩晕对充能重置'), ('shield','护盾溢出与破盾'),
  ('charge','冲锋触发/中断'), ('dash_jump','突进/跃击'), ('pierce','穿透/直线弹道'),
  ('effect_order','状态/伤害/死亡/生成的结算顺序'), ('reflect','反射伤害/弹道归属'),
  ('chain','链式闪电/弹射'), ('rage','狂暴与速度/攻速'), ('heal','治疗与上限'),
 ],
 'special_units': [
  ('spawners','生成器与生成时序'), ('death_spawn','死亡衍生单位'),
  ('death_damage','死亡伤害'), ('building_lifetime','建筑寿命'),
  ('multi_spawn','召唤数量/队形/分路'), ('champion_skill','冠军主动技能'),
  ('hero_skill','英雄主动技能'), ('skill_cooldown','技能圣水与冷却'),
  ('evolution_cycle','进化周期/卡组配置'), ('evolution_effects','各进化特有行为'),
  ('king_activation','国王塔激活'), ('tower_troops','不同塔兵种机制'),
  ('elixir_death_reward','圣水戈仑死亡圣水给予'), ('collector','圣水收集器生产'),
  ('clone','克隆与召唤附属状态'), ('mirror','镜像等级/费用/历史'),
 ]
}

if __name__ == '__main__':
    rows = []
    for group, items in GROUPS.items():
        for key, label in items:
            rows.append({'mechanic_id':key, 'category':group, 'name_zh':label,
                         'required_by':'CODEX_BUILD_PROMPT.txt section 3',
                         'target_reference_date':'2026-10-06', 'verification_status':'unverified',
                         'official_agreement_proven':False,
                         'status_note':'Requirement inventory; implementations and tests must be joined from backend coverage matrix'})
    out = ROOT / 'data/audit'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'requested_mechanics.json').write_text(json.dumps({'denominator':len(rows),
      'counting_rule':'distinct named mechanism families explicitly expanded from section 3; family implementation is not full interaction coverage',
      'rows':rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    with (out / 'requested_mechanics.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(len(rows), 'mechanism families')
