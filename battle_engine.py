"""
三国志战略版 战斗模拟引擎 v0.1
规则来源：游研 + 玩家测试整理

战斗结构：
  准备回合 → 最多8战斗回合 → 平局则新一局（共最多10局）
  主将兵力≤0 → 立即结束
"""

from __future__ import annotations

import random
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from damage_engine import calc_damage, TROOP_COEF, troop_counter_coef

CONTROL_STATUSES = {"震慑", "计穷", "缴械", "混乱", "虚弱", "禁疗", "嘲讽", "伪报", "挑拨", "破坏", "捕获"}
COMMAND_PASSIVE_TYPES = {"指挥", "被动"}
PRE_BATTLE_TYPES = {"指挥", "被动", "兵种", "阵法"}
STRATEGY_DOT_STATUSES = {"灼烧", "水攻", "中毒", "沙暴"}
WEAPON_DOT_STATUSES = {"溃逃"}
TRUE_DOT_STATUSES = {"叛逃"}
HOT_STATUSES = {"休整"}

# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class SkillDef:
    """战法定义（静态配置）"""
    name: str
    skill_type: str          # 主动 / 突击 / 指挥 / 被动 / 兵种 / 阵法
    activation_rate: float = 1.0   # 发动概率
    requires_prep: bool = False    # 需要准备一回合

    # 伤害
    damage_rate: float = 0.0       # 战法系数（如 2.0 = 200%）
    damage_type: str = "兵刃"      # 兵刃 / 谋略
    target_mode: str = "single_enemy"  # single_enemy / all_enemy / self / all_ally / single_ally

    # 治疗
    heal_rate: float = 0.0         # 治疗率（相对于施法者武力/智力）
    heal_target: str = "self"      # self / all_ally / single_ally

    # 准备回合属性加成（指挥/被动战法）
    stat_bonus: dict = field(default_factory=dict)      # 绝对值 {"武力": 10}
    stat_pct_bonus: dict = field(default_factory=dict)  # 百分比 {"会心": 0.04}

    # 施加状态
    apply_status: Optional[str] = None  # 状态名
    status_duration: int = 2
    status_chance: float = 1.0

    # 特殊标记
    ignore_defense: bool = False   # 破阵
    guaranteed_hit: bool = False   # 必中

    # 结构化战法效果。description 只展示文本，战斗逻辑应逐步迁移到这里。
    effect_json: Optional[dict] = None


@dataclass
class GeneralConfig:
    """武将输入配置（不变）"""
    name: str
    force: int       # 武力
    intel: int       # 智力
    command: int     # 统率
    speed: int       # 速度
    troops: int      # 初始兵力
    troop_type: str  # cavalry / bow / spear / shield / machine
    troop_grade: str = "A"  # S / A / B / C 兵种适应性
    skills: list[SkillDef] = field(default_factory=list)


@dataclass
class Status:
    name: str
    rounds_left: int   # 剩余回合数（-1 = 永久/本局）
    value: float = 0.0
    value_type: str = "flat"  # flat / percent
    attr: Optional[str] = None
    operation: str = "add"
    source_skill: Optional[str] = None
    stackable: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class GeneralState:
    """武将战斗状态（随战斗变化）"""
    cfg: GeneralConfig
    current_troops: int
    max_troops: int       # 本局上限（治疗不超过此值）
    alive: bool = True
    is_main: bool = False  # 是否为主将（第一位）

    statuses: list[Status] = field(default_factory=list)

    # 本局临时属性加成（来自指挥/被动战法）
    bonus_force: int = 0
    bonus_intel: int = 0
    bonus_command: int = 0
    bonus_speed: int = 0
    bonus_crit_rate: float = 0.0     # 会心率加成
    bonus_qimou_rate: float = 0.0    # 奇谋率加成
    bonus_dmg_dealt: float = 0.0     # 增伤（兵刃+谋略）
    bonus_dmg_recv: float = 0.0      # 减伤

    # 准备战法等待状态
    prep_skill_pending: Optional[SkillDef] = None
    prep_rounds_left: int = 0

    @property
    def force(self):  return self._stat_with_status("武力", self.cfg.force + self.bonus_force)
    @property
    def intel(self):  return self._stat_with_status("智力", self.cfg.intel + self.bonus_intel)
    @property
    def command(self): return self._stat_with_status("统率", self.cfg.command + self.bonus_command)
    @property
    def speed(self):  return self._stat_with_status("速度", self.cfg.speed + self.bonus_speed)

    def has_status(self, name: str) -> bool:
        return any(s.name == name for s in self.statuses)

    def add_status(
        self,
        name: str,
        rounds: int = 2,
        *,
        value: float = 0.0,
        value_type: str = "flat",
        attr: Optional[str] = None,
        operation: str = "add",
        source_skill: Optional[str] = None,
        stackable: bool = False,
        meta: Optional[dict] = None,
    ):
        # 部分状态不可叠加，检查是否已有
        no_stack = {"先攻", "必中", "破阵", "抵御", "洞察", "连击", "军心动摇"}
        if not stackable and name in no_stack and self.has_status(name):
            return
        self.statuses.append(Status(
            name=name,
            rounds_left=rounds,
            value=value,
            value_type=value_type,
            attr=attr,
            operation=operation,
            source_skill=source_skill,
            stackable=stackable,
            meta=meta or {},
        ))

    def remove_status(self, name: str):
        self.statuses = [s for s in self.statuses if s.name != name]

    def status_value(self, name: str, *, operation: Optional[str] = None) -> float:
        total = 0.0
        for s in self.statuses:
            if s.name != name:
                continue
            if operation and s.operation != operation:
                continue
            total += s.value
        return total

    def _stat_with_status(self, attr: str, base: float) -> int:
        flat = 0.0
        percent = 0.0
        for s in self.statuses:
            if s.attr != attr:
                continue
            if s.value_type == "percent":
                percent += s.value
            else:
                flat += s.value
        return int((base + flat) * (1 + percent))

    def tick_statuses(self):
        """每回合末减少状态持续时间，移除到期状态"""
        still_active = []
        for s in self.statuses:
            if s.rounds_left > 0:
                s.rounds_left -= 1
                if s.rounds_left > 0:
                    still_active.append(s)
            else:
                still_active.append(s)
        self.statuses = still_active

    def take_damage(self, amount: int) -> int:
        """承受伤害，返回实际扣除量。10%死兵，90%伤兵（但治疗上限已限制）"""
        if not self.alive:
            return 0
        actual = min(amount, self.current_troops)
        self.current_troops -= actual
        if self.current_troops <= 0:
            self.current_troops = 0
            self.alive = False
        return actual

    def heal(self, amount: int) -> int:
        """治疗，返回实际回复量。上限为 max_troops（不超过本局开始兵力）"""
        if not self.alive:
            return 0
        if self.has_status("禁疗"):
            return 0
        before = self.current_troops
        self.current_troops = min(self.current_troops + amount, self.max_troops)
        return self.current_troops - before


@dataclass
class TechConfig:
    """科技配置"""
    tech_attack: int = 10    # 攻击科技等级（1级=1%兵刃伤害）
    tech_intel: int = 10     # 谋略科技等级（1级=1%谋略伤害）
    tech_defense: int = 10   # 防御科技等级（1级=1%减伤）
    jiugong: int = 0         # 九宫图等级（1级=3%造成兵刃+谋略）最多5级
    bagua: int = 0           # 八卦阵等级（1级=3%受到兵刃+谋略）最多5级

    @property
    def dealt_bonus(self) -> float:
        return self.tech_attack * 0.01 + min(self.jiugong, 5) * 0.03

    @property
    def recv_reduction(self) -> float:
        return self.tech_defense * 0.01 + min(self.bagua, 5) * 0.03


# ─────────────────────────────────────────────────────────────
# 战斗日志
# ─────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    engagement: int   # 第几局（1~10）
    round: int        # 0=准备，1~8=战斗
    actor: str        # 行动武将
    action: str       # 动作描述
    value: int = 0    # 伤害/治疗数值
    target: str = ""  # 目标武将
    event: str = "misc"
    source_skill: Optional[str] = None
    damage_type: Optional[str] = None
    troops_before: Optional[int] = None
    troops_after: Optional[int] = None
    details: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 战斗引擎
# ─────────────────────────────────────────────────────────────

class BattleEngine:
    """
    战斗结构：
      每局（engagement）= 准备回合 + 最多8个战斗回合
        - 8回合内主将兵力归零 → 立即结束，分出胜负
        - 8回合打完双方主将均存活 → 该局平局，以当前剩余兵力进入下一局
      最多进行 MAX_ENGAGEMENTS 局：
        - 中途任意一局分出胜负 → 战斗结束
        - MAX_ENGAGEMENTS 局全部以平局结束 → 整场判平
    """
    MAX_ENGAGEMENTS = 10
    MAX_ROUNDS = 8

    def __init__(
        self,
        team_a: list[GeneralConfig],  # 我方，index 0 = 主将
        team_b: list[GeneralConfig],  # 敌方，index 0 = 主将
        tech_a: TechConfig = None,
        tech_b: TechConfig = None,
        seed: int = None,
    ):
        self.team_a_cfg = team_a
        self.team_b_cfg = team_b
        self.tech_a = tech_a or TechConfig()
        self.tech_b = tech_b or TechConfig()
        self.seed = seed
        if seed is not None:
            random.seed(seed)

        self.log: list[LogEntry] = []
        self.winner: Optional[str] = None  # "A" / "B" / "draw"
        self.engagement_count = 0

    # ── 公开入口 ──────────────────────────────────────────────

    def run(self) -> dict:
        """运行完整战斗，返回结果摘要"""
        # 初始化两队状态
        states_a = self._init_states(self.team_a_cfg, is_main_first=True)
        states_b = self._init_states(self.team_b_cfg, is_main_first=True)

        for eng in range(1, self.MAX_ENGAGEMENTS + 1):
            self.engagement_count = eng
            result = self._run_engagement(states_a, states_b, eng)
            if result != "engagement_draw":
                # 本局分出胜负（A胜 / B胜）→ 整场结束
                self.winner = result
                break
            # 本局平局（8回合双方主将均存活）→ 继承兵力进入下一局
            self._reset_for_next_engagement(states_a)
            self._reset_for_next_engagement(states_b)
        else:
            # MAX_ENGAGEMENTS 局全部平局 → 整场判平
            self.winner = "draw"

        return self._build_summary(states_a, states_b)

    # ── 初始化 ────────────────────────────────────────────────

    def _init_states(self, cfgs: list[GeneralConfig], is_main_first: bool) -> list[GeneralState]:
        states = []
        for i, cfg in enumerate(cfgs):
            s = GeneralState(
                cfg=cfg,
                current_troops=cfg.troops,
                max_troops=cfg.troops,
                is_main=(i == 0 and is_main_first),
            )
            states.append(s)
        return states

    def _reset_for_next_engagement(self, states: list[GeneralState]):
        """新局开始：清空buff和状态，但保留当前兵力作为新上限"""
        for s in states:
            s.max_troops = s.current_troops  # 剩余兵力作为本局上限
            s.statuses.clear()
            s.bonus_force = s.bonus_intel = s.bonus_command = s.bonus_speed = 0
            s.bonus_crit_rate = s.bonus_qimou_rate = 0.0
            s.bonus_dmg_dealt = s.bonus_dmg_recv = 0.0
            s.prep_skill_pending = None
            s.prep_rounds_left = 0

    # ── 单局战斗 ──────────────────────────────────────────────

    def _run_engagement(
        self,
        states_a: list[GeneralState],
        states_b: list[GeneralState],
        eng: int,
    ) -> str:
        """运行一局（准备回合 + 最多8战斗回合），返回 'A'/'B'/'draw'"""

        # ── 准备回合 ──────────────────────────────────────────
        self._prep_round(states_a, states_b, eng)
        self._prep_round(states_b, states_a, eng)

        # ── 军心动摇检查（首回合生效） ────────────────────────
        self._check_junxin(states_a, states_b, eng)

        # ── 战斗回合 ──────────────────────────────────────────
        for rnd in range(1, self.MAX_ROUNDS + 1):
            self._round_start(states_a, states_b, eng, rnd)
            self._round_start(states_b, states_a, eng, rnd)

            # 所有存活武将按速度（+先攻）排序
            all_generals = [
                (s, "A") for s in states_a if s.alive
            ] + [
                (s, "B") for s in states_b if s.alive
            ]
            all_generals.sort(key=lambda x: (
                1 if x[0].has_status("先攻") else 0,
                x[0].speed
            ), reverse=True)

            for state, side in all_generals:
                if not state.alive:
                    continue
                enemies  = [s for s in (states_b if side == "A" else states_a) if s.alive]
                allies   = [s for s in (states_a if side == "A" else states_b) if s.alive]
                tech_self  = self.tech_a if side == "A" else self.tech_b
                tech_enemy = self.tech_b if side == "A" else self.tech_a

                self._act(state, enemies, allies, tech_self, tech_enemy, eng, rnd)

                # 检查胜负
                winner = self._check_winner(states_a, states_b)
                if winner:
                    return winner

            # 回合末：tick状态
            for s in states_a + states_b:
                if s.alive:
                    s.tick_statuses()

        # 8回合打完，双方主将均存活 → 本局平局，继承兵力进入下一局
        return "engagement_draw"

    # ── 准备回合 ──────────────────────────────────────────────

    def _prep_round(
        self,
        my_states: list[GeneralState],
        enemy_states: list[GeneralState],
        eng: int,
    ):
        """结算指挥/被动/兵种/阵法战法的属性加成"""
        for state in my_states:
            if not state.alive:
                continue
            for skill in state.cfg.skills:
                if skill.skill_type not in PRE_BATTLE_TYPES:
                    continue
                if self._is_skill_disabled(state, skill):
                    continue
                if random.random() > skill.activation_rate:
                    continue

                # 属性加成
                for attr, val in skill.stat_bonus.items():
                    self._apply_stat_bonus(state, attr, val)
                for attr, val in skill.stat_pct_bonus.items():
                    self._apply_pct_bonus(state, attr, val)

                # 触发状态
                if skill.apply_status and random.random() <= skill.status_chance:
                    target = self._pick_target(skill, my_states, enemy_states)
                    if target:
                        target.add_status(skill.apply_status, skill.status_duration)

                self._trigger_skill_effects(
                    "battle_start",
                    skill,
                    state,
                    my_states,
                    enemy_states,
                    eng,
                    0,
                )

                self._emit(
                    eng,
                    0,
                    state.cfg.name,
                    f"[准备] 发动【{skill.name}】",
                    event="skill_prepare",
                    source_skill=skill.name,
                )

    def _round_start(
        self,
        my_states: list[GeneralState],
        enemy_states: list[GeneralState],
        eng: int,
        rnd: int,
    ):
        for state in my_states:
            if not state.alive:
                continue
            self._apply_ongoing_statuses(state, my_states, enemy_states, eng, rnd)
            for skill in state.cfg.skills:
                # 主动/突击战法的 effect_json 要等实际发动时再接入，避免每回合白送效果。
                if skill.skill_type not in PRE_BATTLE_TYPES:
                    continue
                if self._is_skill_disabled(state, skill):
                    continue
                self._trigger_skill_effects("round_start", skill, state, my_states, enemy_states, eng, rnd)

    def _apply_stat_bonus(self, state: GeneralState, attr: str, val):
        mapping = {"武力": "bonus_force", "智力": "bonus_intel",
                   "统率": "bonus_command", "速度": "bonus_speed"}
        if attr in mapping:
            setattr(state, mapping[attr], getattr(state, mapping[attr]) + val)

    def _apply_pct_bonus(self, state: GeneralState, attr: str, val: float):
        mapping = {"会心": "bonus_crit_rate", "奇谋": "bonus_qimou_rate",
                   "增伤": "bonus_dmg_dealt", "减伤": "bonus_dmg_recv"}
        if attr in mapping:
            setattr(state, mapping[attr], getattr(state, mapping[attr]) + val)

    def _effective_damage_reduction(self, attacker: GeneralState, raw_reduction: float) -> float:
        """Apply states that bypass part of the defender's damage reduction."""
        bypass = min(attacker.status_value("看破"), 1.0)
        return raw_reduction * (1 - bypass)

    def _is_skill_disabled(self, state: GeneralState, skill: SkillDef) -> bool:
        return state.has_status("伪报") and skill.skill_type in COMMAND_PASSIVE_TYPES

    def _can_apply_status(self, target: GeneralState, status_name: str) -> bool:
        return not (status_name in CONTROL_STATUSES and target.has_status("洞察"))

    def _emit(
        self,
        eng: int,
        rnd: int,
        actor: str,
        action: str,
        value: int = 0,
        target: str = "",
        *,
        event: str = "misc",
        source_skill: Optional[str] = None,
        damage_type: Optional[str] = None,
        troops_before: Optional[int] = None,
        troops_after: Optional[int] = None,
        details: Optional[dict] = None,
    ):
        self.log.append(LogEntry(
            eng,
            rnd,
            actor,
            action,
            value,
            target,
            event,
            source_skill,
            damage_type,
            troops_before,
            troops_after,
            details or {},
        ))

    def _trigger_skill_effects(
        self,
        event: str,
        skill: SkillDef,
        caster: GeneralState,
        allies: list[GeneralState],
        enemies: list[GeneralState],
        eng: int,
        rnd: int,
        tech_self: Optional[TechConfig] = None,
        tech_enemy: Optional[TechConfig] = None,
        context: Optional[dict] = None,
    ) -> int:
        data = skill.effect_json or {}
        executed = 0
        target_cache: dict[str, list[GeneralState]] = self._context_targets(context)
        for effect in data.get("effects", []):
            if effect.get("event") != event:
                continue
            if not self._condition_matches(effect.get("condition"), rnd):
                continue
            chance = self._effect_value(effect.get("chance"), default=1.0)
            if random.random() > chance:
                continue

            action = effect.get("action")
            target_key = effect.get("target") or "self"
            if target_key not in target_cache:
                target_cache[target_key] = self._resolve_effect_targets(target_key, caster, allies, enemies)
            targets = target_cache[target_key]
            if action == "apply_status":
                self._effect_apply_status(effect, skill, caster, targets, eng, rnd)
                executed += 1
            elif action == "heal":
                self._effect_heal(effect, skill, caster, targets, eng, rnd)
                executed += 1
            elif action == "deal_damage":
                if tech_self is None or tech_enemy is None:
                    continue
                self._effect_deal_damage(effect, skill, caster, targets, allies, enemies, tech_self, tech_enemy, eng, rnd)
                executed += 1
            elif action == "modify_damage":
                # Damage modifiers are collected by _apply_damage_modifiers.
                continue
        return executed

    def _matching_effects(self, event: str, skill: SkillDef, rnd: int) -> list[dict]:
        data = skill.effect_json or {}
        effects = []
        for effect in data.get("effects", []):
            if effect.get("event") != event:
                continue
            if not self._condition_matches(effect.get("condition"), rnd):
                continue
            if random.random() > self._effect_value(effect.get("chance"), default=1.0):
                continue
            effects.append(effect)
        return effects

    def _context_targets(self, context: Optional[dict]) -> dict[str, list[GeneralState]]:
        targets: dict[str, list[GeneralState]] = {}
        for key, value in (context or {}).items():
            if isinstance(value, GeneralState):
                targets[key] = [value]
            elif isinstance(value, list) and all(isinstance(v, GeneralState) for v in value):
                targets[key] = value
        return targets

    def _effect_apply_status(
        self,
        effect: dict,
        skill: SkillDef,
        caster: GeneralState,
        targets: list[GeneralState],
        eng: int,
        rnd: int,
    ):
        status = effect.get("status") or {}
        name = status.get("name")
        if not name:
            return
        value = self._effect_value(status.get("value"), default=0.0)
        for target in targets:
            if not self._can_apply_status(target, name):
                self._emit(
                    eng,
                    rnd,
                    target.cfg.name,
                    f"洞察免疫【{name}】",
                    0,
                    caster.cfg.name,
                    event="status_immune",
                    source_skill=skill.name,
                    details={"status": name},
                )
                continue
            target.add_status(
                name,
                rounds=int(status.get("duration", effect.get("duration", 2))),
                value=value,
                value_type=status.get("value_type", "flat"),
                attr=status.get("attr"),
                operation=status.get("operation", "add"),
                source_skill=skill.name,
                stackable=bool(status.get("stackable", False)),
                meta={
                    "caster": caster.cfg.name,
                    "effect": effect,
                    "rate": value,
                    "source_attr": self._state_attr(caster, status.get("scales_with", status.get("attr", "智力"))),
                    "source_troops": caster.current_troops,
                    "source_troop_grade": caster.cfg.troop_grade,
                    "source_troop_type": caster.cfg.troop_type,
                },
            )
            self._emit(
                eng,
                rnd,
                caster.cfg.name,
                f"【{skill.name}】施加【{name}】",
                0,
                target.cfg.name,
                event="apply_status",
                source_skill=skill.name,
                details={"status": status},
            )

    def _effect_heal(
        self,
        effect: dict,
        skill: SkillDef,
        caster: GeneralState,
        targets: list[GeneralState],
        eng: int,
        rnd: int,
    ):
        heal = effect.get("heal") or {}
        rate = self._effect_value(heal.get("rate"), default=0.0)
        if rate <= 0:
            return
        attr_name = heal.get("scales_with", "智力")
        attr = self._state_attr(caster, attr_name)
        for target in targets:
            before = target.current_troops
            amount = int(attr * rate * caster.current_troops ** 0.1)
            healed = target.heal(amount)
            if healed == 0 and target.has_status("禁疗"):
                self._emit(
                    eng,
                    rnd,
                    target.cfg.name,
                    "禁疗阻止治疗",
                    0,
                    caster.cfg.name,
                    event="heal_blocked",
                    source_skill=skill.name,
                    troops_before=before,
                    troops_after=target.current_troops,
                    details={"attempted_heal": amount},
                )
                continue
            self._emit(
                eng,
                rnd,
                caster.cfg.name,
                f"【{skill.name}】结构化治疗",
                healed,
                target.cfg.name,
                event="heal",
                source_skill=skill.name,
                troops_before=before,
                troops_after=target.current_troops,
                details={"rate": rate, "scales_with": attr_name},
            )

    def _effect_deal_damage(
        self,
        effect: dict,
        skill: SkillDef,
        caster: GeneralState,
        targets: list[GeneralState],
        allies: list[GeneralState],
        enemies: list[GeneralState],
        tech_self: TechConfig,
        tech_enemy: TechConfig,
        eng: int,
        rnd: int,
    ):
        damage = effect.get("damage") or {}
        rate = self._effect_value(damage.get("rate"), default=0.0)
        if rate <= 0:
            return
        damage_type = damage.get("type", "兵刃")
        atk_attr = damage.get("scales_with") or ("武力" if damage_type == "兵刃" else "智力")
        def_attr = damage.get("defense_attr") or ("统率" if damage_type == "兵刃" else "智力")
        atk = self._state_attr(caster, atk_attr)
        inc_atk = caster.bonus_dmg_dealt + tech_self.dealt_bonus

        for target in targets:
            def_ = self._state_attr(target, def_attr)
            dec_def = self._effective_damage_reduction(
                caster,
                target.bonus_dmg_recv + tech_enemy.recv_reduction,
            )
            ignore_def = damage.get("ignore_defense", False) or caster.has_status("破阵")
            result = calc_damage(
                num=caster.current_troops,
                atk=atk,
                def_=(0 if ignore_def else def_),
                def_num=target.current_troops,
                atk_troop_grade=caster.cfg.troop_grade,
                def_troop_grade=target.cfg.troop_grade,
                atk_troop_type=caster.cfg.troop_type,
                def_troop_type=target.cfg.troop_type,
                skill_rate=rate,
                inc_atk=inc_atk,
                dec_def=dec_def,
                crit=caster.bonus_crit_rate if damage_type == "兵刃" else caster.bonus_qimou_rate,
            )
            actual = self._deal_damage(
                target,
                result["total"],
                caster,
                allies,
                eng,
                rnd,
                target_allies=enemies,
                target_enemies=allies,
                target_tech=tech_enemy,
                attacker_tech=tech_self,
            )
            self._emit(
                eng,
                rnd,
                caster.cfg.name,
                f"【{skill.name}】结构化{damage_type}伤害",
                actual,
                target.cfg.name,
                event="skill_damage",
                source_skill=skill.name,
                damage_type=damage_type,
                details={"rate": rate, "result": result},
            )

    def _resolve_effect_targets(
        self,
        target: Optional[str],
        caster: GeneralState,
        allies: list[GeneralState],
        enemies: list[GeneralState],
    ) -> list[GeneralState]:
        alive_allies = [s for s in allies if s.alive]
        alive_enemies = [s for s in enemies if s.alive]
        if target == "self":
            return [caster] if caster.alive else []
        if target in ("normal_attack_target", "damage_target", "damage_source"):
            return []
        if target == "ally_main":
            return alive_allies[:1]
        if target == "enemy_main":
            return alive_enemies[:1]
        if target == "ally_deputies":
            return alive_allies[1:]
        if target == "enemy_deputies":
            return alive_enemies[1:]
        if target in ("all_ally", "all_ally_shield_advanced"):
            return alive_allies
        if target == "all_enemy":
            return alive_enemies
        if target == "ally_group_2_3":
            return self._pick_group(alive_allies)
        if target == "enemy_group_2_3":
            return self._pick_group(alive_enemies)
        if target == "single_ally":
            return alive_allies[:1]
        if target == "random_enemy":
            return [random.choice(alive_enemies)] if alive_enemies else []
        if target == "random_ally":
            return [random.choice(alive_allies)] if alive_allies else []
        if target == "enemy_lowest_troops":
            return self._pick_by(alive_enemies, lambda s: s.current_troops, reverse=False)
        if target == "ally_lowest_troops":
            return self._pick_by(alive_allies, lambda s: s.current_troops, reverse=False)
        if target == "enemy_highest_force":
            return self._pick_by(alive_enemies, lambda s: s.force)
        if target == "ally_highest_force":
            return self._pick_by(alive_allies, lambda s: s.force)
        if target == "enemy_highest_intel":
            return self._pick_by(alive_enemies, lambda s: s.intel)
        if target == "ally_highest_intel":
            return self._pick_by(alive_allies, lambda s: s.intel)
        if target == "enemy_highest_command":
            return self._pick_by(alive_enemies, lambda s: s.command)
        if target == "ally_highest_command":
            return self._pick_by(alive_allies, lambda s: s.command)
        if target == "enemy_highest_speed":
            return self._pick_by(alive_enemies, lambda s: s.speed)
        if target == "ally_highest_speed":
            return self._pick_by(alive_allies, lambda s: s.speed)
        if target == "single_enemy":
            return alive_enemies[:1]
        return [caster] if caster.alive else []

    def _pick_group(self, states: list[GeneralState]) -> list[GeneralState]:
        if len(states) <= 2:
            return states
        size = random.randint(2, min(3, len(states)))
        return random.sample(states, size)

    def _pick_by(self, states: list[GeneralState], key, *, reverse: bool = True) -> list[GeneralState]:
        return [sorted(states, key=key, reverse=reverse)[0]] if states else []

    def _condition_matches(self, condition: Optional[str], rnd: int) -> bool:
        if not condition:
            return True
        condition = condition.strip()
        parts = re.split(r"\s+(?:AND|and)\s+", condition)
        if len(parts) > 1:
            return all(self._condition_matches(part, rnd) for part in parts)

        mod_match = re.fullmatch(r"round\s*%\s*(\d+)\s*==\s*(\d+)", condition)
        if mod_match:
            divisor = int(mod_match.group(1))
            expected = int(mod_match.group(2))
            return divisor > 0 and rnd % divisor == expected

        cmp_match = re.fullmatch(r"round\s*(==|!=|>=|<=|>|<)\s*(\d+)", condition)
        if cmp_match:
            op = cmp_match.group(1)
            value = int(cmp_match.group(2))
            if op == "==":
                return rnd == value
            if op == "!=":
                return rnd != value
            if op == ">=":
                return rnd >= value
            if op == "<=":
                return rnd <= value
            if op == ">":
                return rnd > value
            if op == "<":
                return rnd < value

        return False

    def _effect_value(self, value, *, default: float = 0.0) -> float:
        if value is None:
            return default
        if isinstance(value, dict):
            if value.get("max") is not None:
                return float(value["max"])
            if value.get("base") is not None:
                return float(value["base"])
            return default
        return float(value)

    def _apply_ongoing_statuses(
        self,
        state: GeneralState,
        allies: list[GeneralState],
        enemies: list[GeneralState],
        eng: int,
        rnd: int,
    ):
        for status in list(state.statuses):
            if status.name in STRATEGY_DOT_STATUSES:
                self._apply_status_tick_damage(status, state, "谋略", eng, rnd)
            elif status.name in WEAPON_DOT_STATUSES:
                self._apply_status_tick_damage(status, state, "兵刃", eng, rnd)
            elif status.name in TRUE_DOT_STATUSES:
                self._apply_status_tick_damage(status, state, "无视防御", eng, rnd)
            elif status.name in HOT_STATUSES or status.operation == "heal_over_time":
                self._apply_status_tick_heal(status, state, eng, rnd)

    def _apply_status_tick_damage(self, status: Status, target: GeneralState, damage_type: str, eng: int, rnd: int):
        rate = status.value or float(status.meta.get("rate", 0.0) or 0.0)
        if rate <= 0 or not target.alive:
            return
        source_attr = float(status.meta.get("source_attr", 100.0))
        before = target.current_troops
        if damage_type == "无视防御":
            amount = int(max(1, source_attr * rate * max(target.current_troops, 1) ** 0.1))
        else:
            def_attr = target.intel if damage_type == "谋略" else target.command
            result = calc_damage(
                num=int(status.meta.get("source_troops", target.current_troops)),
                atk=source_attr,
                def_=def_attr,
                def_num=target.current_troops,
                atk_troop_grade=status.meta.get("source_troop_grade", target.cfg.troop_grade),
                def_troop_grade=target.cfg.troop_grade,
                atk_troop_type=status.meta.get("source_troop_type", target.cfg.troop_type),
                def_troop_type=target.cfg.troop_type,
                skill_rate=rate,
            )
            amount = result["total"]
        actual = target.take_damage(amount)
        self._emit(
            eng,
            rnd,
            status.meta.get("caster", status.source_skill or status.name),
            f"【{status.name}】持续{damage_type}伤害",
            actual,
            target.cfg.name,
            event="status_tick_damage",
            source_skill=status.source_skill,
            damage_type=damage_type,
            troops_before=before,
            troops_after=target.current_troops,
            details={"status": status.name, "rate": rate, "source_attr": source_attr},
        )

    def _apply_status_tick_heal(self, status: Status, target: GeneralState, eng: int, rnd: int):
        rate = status.value or float(status.meta.get("rate", 0.0) or 0.0)
        if rate <= 0 or not target.alive:
            return
        before = target.current_troops
        source_attr = float(status.meta.get("source_attr", target.intel))
        amount = int(source_attr * rate * max(target.current_troops, 1) ** 0.1)
        healed = target.heal(amount)
        if healed == 0 and target.has_status("禁疗"):
            self._emit(
                eng,
                rnd,
                target.cfg.name,
                "禁疗阻止持续治疗",
                0,
                status.meta.get("caster", status.source_skill or status.name),
                event="heal_blocked",
                source_skill=status.source_skill,
                troops_before=before,
                troops_after=target.current_troops,
                details={"status": status.name, "attempted_heal": amount},
            )
            return
        self._emit(
            eng,
            rnd,
            status.meta.get("caster", status.source_skill or status.name),
            f"【{status.name}】持续治疗",
            healed,
            target.cfg.name,
            event="status_tick_heal",
            source_skill=status.source_skill,
            troops_before=before,
            troops_after=target.current_troops,
            details={"status": status.name, "rate": rate, "source_attr": source_attr},
        )

    def _state_attr(self, state: GeneralState, attr: str) -> float:
        mapping = {
            "武力": state.force,
            "智力": state.intel,
            "统率": state.command,
            "速度": state.speed,
        }
        return mapping.get(attr, state.intel)

    def _status_damage_multiplier(self, attacker: GeneralState, target: GeneralState) -> float:
        inc = attacker.status_value("造成伤害提升") + attacker.status_value("增伤")
        red = (
            target.status_value("受到伤害降低")
            + target.status_value("减伤")
            + target.status_value("警戒")
        )
        if attacker.has_status("虚弱"):
            return 0.0
        return max(0.0, 1 + inc - red)

    def _apply_damage_modifiers(
        self,
        amount: int,
        attacker: GeneralState,
        target: GeneralState,
        attacker_allies: list[GeneralState],
        target_allies: list[GeneralState],
        target_enemies: list[GeneralState],
        eng: int,
        rnd: int,
    ) -> int:
        before = amount
        amount = int(amount * self._status_damage_multiplier(attacker, target))

        for owner, allies, enemies in (
            (attacker, attacker_allies, target_allies),
            (target, target_allies, target_enemies),
        ):
            for skill in owner.cfg.skills:
                if self._is_skill_disabled(owner, skill):
                    continue
                for effect in self._matching_effects("before_damage", skill, rnd):
                    if effect.get("action") != "modify_damage":
                        continue
                    modifier = effect.get("damage_modifier") or {}
                    amount = self._apply_damage_modifier(amount, modifier)
                    self._emit(
                        eng,
                        rnd,
                        owner.cfg.name,
                        f"【{skill.name}】调整伤害",
                        amount - before,
                        target.cfg.name,
                        event="before_damage",
                        source_skill=skill.name,
                        details={"before": before, "after": amount, "modifier": modifier},
                    )

        if amount != before:
            self._emit(
                eng,
                rnd,
                target.cfg.name,
                "伤害修正",
                amount - before,
                target.cfg.name,
                event="before_damage",
                details={"before": before, "after": amount},
            )
        return max(0, amount)

    def _apply_damage_modifier(self, amount: int, modifier: dict) -> int:
        operation = modifier.get("operation", "multiply")
        value = self._effect_value(modifier.get("value"), default=1.0)
        if operation == "multiply":
            return int(amount * value)
        if operation == "increase_percent":
            return int(amount * (1 + value))
        if operation == "reduce_percent":
            return int(amount * max(0.0, 1 - value))
        if operation == "add":
            return int(amount + value)
        if operation == "subtract":
            return int(max(0.0, amount - value))
        return amount

    # ── 军心动摇 ──────────────────────────────────────────────

    def _check_junxin(
        self,
        states_a: list[GeneralState],
        states_b: list[GeneralState],
        eng: int,
    ):
        total_a = sum(s.current_troops for s in states_a if s.alive)
        total_b = sum(s.current_troops for s in states_b if s.alive)
        threshold_for_a = total_b * 0.10   # 己方兵力低于敌方总兵力10%则动摇
        threshold_for_b = total_a * 0.10

        for s in states_a:
            if s.alive and s.current_troops < threshold_for_a:
                s.add_status("军心动摇", rounds=1)
                self._emit(eng, 0, s.cfg.name, "军心动摇！首回合无法行动")

        for s in states_b:
            if s.alive and s.current_troops < threshold_for_b:
                s.add_status("军心动摇", rounds=1)
                self._emit(eng, 0, s.cfg.name, "军心动摇！首回合无法行动")

    # ── 武将行动 ──────────────────────────────────────────────

    def _act(
        self,
        state: GeneralState,
        enemies: list[GeneralState],
        allies: list[GeneralState],
        tech_self: TechConfig,
        tech_enemy: TechConfig,
        eng: int,
        rnd: int,
    ):
        if not enemies:
            return

        # 震慑/军心动摇 → 跳过
        if state.has_status("震慑") or state.has_status("军心动摇"):
            self._emit(eng, rnd, state.cfg.name, "无法行动（震慑/军心动摇）")
            return

        # ── 主动战法 ──────────────────────────────────────────
        if not state.has_status("计穷"):
            for skill in state.cfg.skills:
                if skill.skill_type != "主动":
                    continue
                if skill.requires_prep:
                    if state.prep_skill_pending == skill:
                        # 本回合发动
                        self._execute_skill(skill, state, enemies, allies,
                                            tech_self, tech_enemy, eng, rnd)
                        state.prep_skill_pending = None
                    else:
                        # 进入准备状态
                        state.prep_skill_pending = skill
                        self._emit(eng, rnd, state.cfg.name,
                                                  f"【{skill.name}】蓄力中…")
                    break  # 同时只处理一个主动战法
                elif random.random() <= skill.activation_rate:
                    self._execute_skill(skill, state, enemies, allies,
                                        tech_self, tech_enemy, eng, rnd)
                    break

        # ── 普通攻击（缴械则跳过） ────────────────────────────
        if not state.has_status("缴械"):
            target = enemies[0]  # 简化：打第1个存活敌人
            dmg = self._normal_attack(state, target, tech_self, tech_enemy)
            actual = self._deal_damage(
                target,
                dmg,
                state,
                allies,
                eng,
                rnd,
                target_allies=enemies,
                target_enemies=allies,
                target_tech=tech_enemy,
                attacker_tech=tech_self,
            )
            self._emit(eng, rnd, state.cfg.name,
                                      "普通攻击", actual, target.cfg.name)
            self._after_normal_attack(state, target, enemies, allies, tech_self, tech_enemy, eng, rnd)

            # 群攻
            if state.has_status("群攻") and len(enemies) > 1:
                for splash_target in enemies[1:]:
                    splash = int(dmg * 0.5)   # 溅射 50%（简化）
                    act2 = self._deal_damage(
                        splash_target,
                        splash,
                        state,
                        allies,
                        eng,
                        rnd,
                        target_allies=enemies,
                        target_enemies=allies,
                        target_tech=tech_enemy,
                        attacker_tech=tech_self,
                    )
                    self._emit(eng, rnd, state.cfg.name,
                                              "群攻溅射", act2, splash_target.cfg.name)

            # 连击：额外一次普攻
            if state.has_status("连击"):
                dmg2 = self._normal_attack(state, target, tech_self, tech_enemy)
                act2 = self._deal_damage(
                    target,
                    dmg2,
                    state,
                    allies,
                    eng,
                    rnd,
                    target_allies=enemies,
                    target_enemies=allies,
                    target_tech=tech_enemy,
                    attacker_tech=tech_self,
                )
                self._emit(eng, rnd, state.cfg.name,
                                          "连击（额外普攻）", act2, target.cfg.name)
                self._after_normal_attack(state, target, enemies, allies, tech_self, tech_enemy, eng, rnd)

    # ── 普攻后突击 ────────────────────────────────────────────

    def _after_normal_attack(
        self,
        state: GeneralState,
        normal_target: GeneralState,
        enemies: list[GeneralState],
        allies: list[GeneralState],
        tech_self: TechConfig,
        tech_enemy: TechConfig,
        eng: int,
        rnd: int,
    ):
        for skill in state.cfg.skills:
            if skill.skill_type != "突击":
                continue
            if state.has_status("缴械"):
                break
            if random.random() <= skill.activation_rate:
                executed = self._trigger_skill_effects(
                    "after_normal_attack",
                    skill,
                    state,
                    allies,
                    enemies,
                    eng,
                    rnd,
                    tech_self,
                    tech_enemy,
                    context={"normal_attack_target": normal_target},
                )
                if not executed:
                    self._execute_skill(skill, state, enemies, allies,
                                        tech_self, tech_enemy, eng, rnd)
            break  # 同时只处理一个突击战法

    # ── 执行战法 ──────────────────────────────────────────────

    def _execute_skill(
        self,
        skill: SkillDef,
        caster: GeneralState,
        enemies: list[GeneralState],
        allies: list[GeneralState],
        tech_self: TechConfig,
        tech_enemy: TechConfig,
        eng: int,
        rnd: int,
    ):
        structured_release = self._trigger_skill_effects(
            "skill_release",
            skill,
            caster,
            allies,
            enemies,
            eng,
            rnd,
            tech_self,
            tech_enemy,
        )
        if structured_release:
            return

        target = self._pick_target(skill, allies, enemies)
        if not target:
            return

        # 伤害
        if skill.damage_rate > 0 and skill.damage_type and target in enemies:
            atk = caster.force if skill.damage_type == "兵刃" else caster.intel
            def_ = target.command if skill.damage_type == "兵刃" else target.intel
            inc_atk = caster.bonus_dmg_dealt + tech_self.dealt_bonus
            dec_def = self._effective_damage_reduction(
                caster,
                target.bonus_dmg_recv + tech_enemy.recv_reduction,
            )
            ignore_def = skill.ignore_defense or caster.has_status("破阵")

            result = calc_damage(
                num=caster.current_troops,
                atk=atk, def_=(0 if ignore_def else def_),
                def_num=target.current_troops,
                atk_troop_grade=caster.cfg.troop_grade,
                def_troop_grade=target.cfg.troop_grade,
                atk_troop_type=caster.cfg.troop_type,
                def_troop_type=target.cfg.troop_type,
                skill_rate=skill.damage_rate,
                inc_atk=inc_atk,
                dec_def=dec_def,
                crit=caster.bonus_crit_rate,
            )
            dmg = result["total"]
            actual = self._deal_damage(
                target,
                dmg,
                caster,
                allies,
                eng,
                rnd,
                target_allies=enemies,
                target_enemies=allies,
                target_tech=tech_enemy,
                attacker_tech=tech_self,
            )
            self._emit(eng, rnd, caster.cfg.name,
                                      f"【{skill.name}】{skill.damage_type}伤害",
                                      actual, target.cfg.name)

        # 治疗
        if skill.heal_rate > 0:
            heal_targets = allies if skill.heal_target == "all_ally" else [caster]
            attr = caster.intel  # 治疗一般基于智力
            for t in heal_targets:
                before = t.current_troops
                amount = int(attr * skill.heal_rate * caster.current_troops ** 0.1)
                healed = t.heal(amount)
                if healed == 0 and t.has_status("禁疗"):
                    self._emit(eng, rnd, t.cfg.name,
                                              "禁疗阻止治疗",
                                              0, caster.cfg.name,
                                              event="heal_blocked",
                                              source_skill=skill.name,
                                              troops_before=before,
                                              troops_after=t.current_troops,
                                              details={"attempted_heal": amount})
                    continue
                self._emit(eng, rnd, caster.cfg.name,
                                          f"【{skill.name}】治疗",
                                          healed, t.cfg.name,
                                          event="heal",
                                          source_skill=skill.name,
                                          troops_before=before,
                                          troops_after=t.current_troops)

        # 施加状态
        if skill.apply_status and random.random() <= skill.status_chance:
            if self._can_apply_status(target, skill.apply_status):
                target.add_status(skill.apply_status, skill.status_duration)
                self._emit(eng, rnd, caster.cfg.name,
                                          f"【{skill.name}】施加【{skill.apply_status}】",
                                          0, target.cfg.name,
                                          event="apply_status",
                                          source_skill=skill.name)
            else:
                self._emit(eng, rnd, target.cfg.name,
                                          f"洞察免疫【{skill.apply_status}】",
                                          0, caster.cfg.name,
                                          event="status_immune",
                                          source_skill=skill.name)

    # ── 普通攻击伤害 ──────────────────────────────────────────

    def _normal_attack(
        self,
        attacker: GeneralState,
        defender: GeneralState,
        tech_self: TechConfig,
        tech_enemy: TechConfig,
    ) -> int:
        inc_atk = attacker.bonus_dmg_dealt + tech_self.dealt_bonus
        dec_def = self._effective_damage_reduction(
            attacker,
            defender.bonus_dmg_recv + tech_enemy.recv_reduction,
        )
        ignore_def = attacker.has_status("破阵")
        is_strategy_attack = attacker.has_status("法术普攻")
        atk = attacker.intel if is_strategy_attack else attacker.force
        def_ = defender.intel if is_strategy_attack else defender.command
        result = calc_damage(
            num=attacker.current_troops,
            atk=atk,
            def_=(0 if ignore_def else def_),
            def_num=defender.current_troops,
            atk_troop_grade=attacker.cfg.troop_grade,
            def_troop_grade=defender.cfg.troop_grade,
            atk_troop_type=attacker.cfg.troop_type,
            def_troop_type=defender.cfg.troop_type,
            skill_rate=1.0,
            inc_atk=inc_atk,
            dec_def=dec_def,
            crit=attacker.bonus_crit_rate,
        )
        return result["total"]

    # ── 造成伤害（含抵御/援护/分摊处理） ─────────────────────

    def _deal_damage(
        self,
        target: GeneralState,
        amount: int,
        attacker: GeneralState,
        attacker_allies: list[GeneralState],  # 攻方队友（用于分摊）
        eng: int,
        rnd: int,
        *,
        target_allies: Optional[list[GeneralState]] = None,
        target_enemies: Optional[list[GeneralState]] = None,
        target_tech: Optional[TechConfig] = None,
        attacker_tech: Optional[TechConfig] = None,
    ) -> int:
        if not target.alive:
            return 0

        # 抵御：免疫一次伤害（必中可穿透）
        if target.has_status("抵御") and not attacker.has_status("必中"):
            target.remove_status("抵御")
            self._emit(
                eng,
                rnd,
                target.cfg.name,
                "抵御格挡",
                0,
                attacker.cfg.name,
                event="before_damage",
                details={"blocked_damage": amount},
            )
            return 0

        amount = self._apply_damage_modifiers(
            amount,
            attacker,
            target,
            attacker_allies,
            target_allies or [target],
            target_enemies or attacker_allies,
            eng,
            rnd,
        )
        troops_before = target.current_troops
        actual = target.take_damage(amount)
        troops_after = target.current_troops
        if actual > 0:
            self._after_damage(
                target,
                attacker,
                actual,
                target_allies or [target],
                target_enemies or attacker_allies,
                target_tech,
                attacker_tech,
                eng,
                rnd,
            )
            self._on_damage_taken(
                target,
                attacker,
                actual,
                target_allies or [target],
                target_enemies or attacker_allies,
                target_tech,
                attacker_tech,
                eng,
                rnd,
            )
            self._emit(
                eng,
                rnd,
                attacker.cfg.name,
                "造成伤害",
                actual,
                target.cfg.name,
                event="after_damage",
                troops_before=troops_before,
                troops_after=troops_after,
                details={"requested_damage": amount},
            )

        # 反击：受到普通攻击时对攻方造成反伤
        if target.has_status("反击") and target.alive:
            counter = int(amount * 0.5)
            attacker.take_damage(counter)
            self._emit(eng, rnd, target.cfg.name, "反击", counter, attacker.cfg.name)

        # 倒戈：物理吸血
        if attacker.has_status("倒戈"):
            absorb = int(actual * 0.3)
            attacker.heal(absorb)

        return actual

    def _after_damage(
        self,
        target: GeneralState,
        attacker: GeneralState,
        actual_damage: int,
        target_allies: list[GeneralState],
        target_enemies: list[GeneralState],
        target_tech: Optional[TechConfig],
        attacker_tech: Optional[TechConfig],
        eng: int,
        rnd: int,
    ):
        for owner, allies, enemies, tech_self, tech_enemy in (
            (attacker, target_enemies, target_allies, attacker_tech, target_tech),
            (target, target_allies, target_enemies, target_tech, attacker_tech),
        ):
            for skill in owner.cfg.skills:
                if self._is_skill_disabled(owner, skill):
                    continue
                self._trigger_skill_effects(
                    "after_damage",
                    skill,
                    owner,
                    allies,
                    enemies,
                    eng,
                    rnd,
                    tech_self,
                    tech_enemy,
                    context={
                        "damage_target": target,
                        "damage_source": attacker,
                        "damage_amount": actual_damage,
                    },
                )

    def _on_damage_taken(
        self,
        target: GeneralState,
        attacker: GeneralState,
        actual_damage: int,
        target_allies: list[GeneralState],
        target_enemies: list[GeneralState],
        target_tech: Optional[TechConfig],
        attacker_tech: Optional[TechConfig],
        eng: int,
        rnd: int,
    ):
        for skill in target.cfg.skills:
            if skill.skill_type not in PRE_BATTLE_TYPES:
                continue
            if self._is_skill_disabled(target, skill):
                continue
            self._trigger_skill_effects(
                "on_damage_taken",
                skill,
                target,
                target_allies,
                target_enemies,
                eng,
                rnd,
                target_tech,
                attacker_tech,
                context={
                    "damage_target": target,
                    "damage_source": attacker,
                    "damage_amount": actual_damage,
                },
            )

    # ── 目标选择 ──────────────────────────────────────────────

    def _pick_target(
        self,
        skill: SkillDef,
        allies: list[GeneralState],
        enemies: list[GeneralState],
    ) -> Optional[GeneralState]:
        alive_enemies = [s for s in enemies if s.alive]
        alive_allies  = [s for s in allies  if s.alive]
        if skill.target_mode == "single_enemy":
            return alive_enemies[0] if alive_enemies else None
        elif skill.target_mode == "all_enemy":
            return alive_enemies[0] if alive_enemies else None  # 伤害由调用方循环处理
        elif skill.target_mode in ("self", "all_ally"):
            return allies[0] if allies else None
        elif skill.target_mode == "single_ally":
            # 优先选兵力最少的存活队友
            return min(alive_allies, key=lambda s: s.current_troops) if alive_allies else None
        return None

    # ── 胜负判定 ──────────────────────────────────────────────

    def _check_winner(
        self,
        states_a: list[GeneralState],
        states_b: list[GeneralState],
    ) -> Optional[str]:
        main_a = states_a[0]
        main_b = states_b[0]
        if not main_a.alive and not main_b.alive:
            return "draw"
        if not main_a.alive:
            return "B"
        if not main_b.alive:
            return "A"
        return None

    # ── 结果摘要 ──────────────────────────────────────────────

    def _build_summary(
        self,
        states_a: list[GeneralState],
        states_b: list[GeneralState],
    ) -> dict:
        def team_summary(states):
            return [
                {
                    "name": s.cfg.name,
                    "is_main": s.is_main,
                    "alive": s.alive,
                    "troops_left": s.current_troops,
                    "troops_start": s.cfg.troops,
                    "loss_pct": round(1 - s.current_troops / s.cfg.troops, 3),
                }
                for s in states
            ]

        log_dicts = [
            {
                "engagement": e.engagement,
                "round": e.round,
                "actor": e.actor,
                "action": e.action,
                "value": e.value,
                "target": e.target,
                "event": e.event,
                "source_skill": e.source_skill,
                "damage_type": e.damage_type,
                "troops_before": e.troops_before,
                "troops_after": e.troops_after,
                "details": e.details,
            }
            for e in self.log
        ]

        return {
            "winner": self.winner,
            "engagements": self.engagement_count,
            "team_a": team_summary(states_a),
            "team_b": team_summary(states_b),
            "log": log_dicts,
        }
