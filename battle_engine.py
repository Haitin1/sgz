"""
三国志战略版 战斗模拟引擎 v0.1
规则来源：游研 + 玩家测试整理

战斗结构：
  准备回合 → 最多8战斗回合 → 平局则新一局（共最多10局）
  主将兵力≤0 → 立即结束
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from damage_engine import calc_damage, TROOP_COEF, troop_counter_coef

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
    def force(self):  return self.cfg.force  + self.bonus_force
    @property
    def intel(self):  return self.cfg.intel  + self.bonus_intel
    @property
    def command(self): return self.cfg.command + self.bonus_command
    @property
    def speed(self):  return self.cfg.speed  + self.bonus_speed

    def has_status(self, name: str) -> bool:
        return any(s.name == name for s in self.statuses)

    def add_status(self, name: str, rounds: int = 2):
        # 部分状态不可叠加，检查是否已有
        no_stack = {"先攻", "必中", "破阵", "抵御", "洞察", "连击", "军心动摇"}
        if name in no_stack and self.has_status(name):
            return
        self.statuses.append(Status(name=name, rounds_left=rounds))

    def remove_status(self, name: str):
        self.statuses = [s for s in self.statuses if s.name != name]

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
                if skill.skill_type not in ("指挥", "被动", "兵种", "阵法"):
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

                self.log.append(LogEntry(
                    engagement=eng, round=0,
                    actor=state.cfg.name,
                    action=f"[准备] 发动【{skill.name}】",
                ))

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
                self.log.append(LogEntry(eng, 0, s.cfg.name, "军心动摇！首回合无法行动"))

        for s in states_b:
            if s.alive and s.current_troops < threshold_for_b:
                s.add_status("军心动摇", rounds=1)
                self.log.append(LogEntry(eng, 0, s.cfg.name, "军心动摇！首回合无法行动"))

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
            self.log.append(LogEntry(eng, rnd, state.cfg.name, "无法行动（震慑/军心动摇）"))
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
                        self.log.append(LogEntry(eng, rnd, state.cfg.name,
                                                  f"【{skill.name}】蓄力中…"))
                    break  # 同时只处理一个主动战法
                elif random.random() <= skill.activation_rate:
                    self._execute_skill(skill, state, enemies, allies,
                                        tech_self, tech_enemy, eng, rnd)
                    break

        # ── 普通攻击（缴械则跳过） ────────────────────────────
        if not state.has_status("缴械"):
            target = enemies[0]  # 简化：打第1个存活敌人
            dmg = self._normal_attack(state, target, tech_self, tech_enemy)
            actual = self._deal_damage(target, dmg, state, allies, eng, rnd)
            self.log.append(LogEntry(eng, rnd, state.cfg.name,
                                      "普通攻击", actual, target.cfg.name))

            # 群攻
            if state.has_status("群攻") and len(enemies) > 1:
                for splash_target in enemies[1:]:
                    splash = int(dmg * 0.5)   # 溅射 50%（简化）
                    act2 = self._deal_damage(splash_target, splash, state, allies, eng, rnd)
                    self.log.append(LogEntry(eng, rnd, state.cfg.name,
                                              "群攻溅射", act2, splash_target.cfg.name))

            # 连击：额外一次普攻
            if state.has_status("连击"):
                dmg2 = self._normal_attack(state, target, tech_self, tech_enemy)
                act2 = self._deal_damage(target, dmg2, state, allies, eng, rnd)
                self.log.append(LogEntry(eng, rnd, state.cfg.name,
                                          "连击（额外普攻）", act2, target.cfg.name))

        # ── 突击战法 ──────────────────────────────────────────
        for skill in state.cfg.skills:
            if skill.skill_type != "突击":
                continue
            if state.has_status("缴械"):
                break
            if random.random() <= skill.activation_rate:
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
        target = self._pick_target(skill, allies, enemies)
        if not target:
            return

        # 伤害
        if skill.damage_rate > 0 and skill.damage_type and target in enemies:
            atk = caster.force if skill.damage_type == "兵刃" else caster.intel
            def_ = target.command if skill.damage_type == "兵刃" else target.intel
            inc_atk = caster.bonus_dmg_dealt + tech_self.dealt_bonus
            dec_def = target.bonus_dmg_recv + tech_enemy.recv_reduction
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
            actual = self._deal_damage(target, dmg, caster, allies, eng, rnd)
            self.log.append(LogEntry(eng, rnd, caster.cfg.name,
                                      f"【{skill.name}】{skill.damage_type}伤害",
                                      actual, target.cfg.name))

        # 治疗
        if skill.heal_rate > 0:
            heal_targets = allies if skill.heal_target == "all_ally" else [caster]
            attr = caster.intel  # 治疗一般基于智力
            for t in heal_targets:
                amount = int(attr * skill.heal_rate * caster.current_troops ** 0.1)
                healed = t.heal(amount)
                self.log.append(LogEntry(eng, rnd, caster.cfg.name,
                                          f"【{skill.name}】治疗",
                                          healed, t.cfg.name))

        # 施加状态
        if skill.apply_status and random.random() <= skill.status_chance:
            target.add_status(skill.apply_status, skill.status_duration)
            self.log.append(LogEntry(eng, rnd, caster.cfg.name,
                                      f"【{skill.name}】施加【{skill.apply_status}】",
                                      0, target.cfg.name))

    # ── 普通攻击伤害 ──────────────────────────────────────────

    def _normal_attack(
        self,
        attacker: GeneralState,
        defender: GeneralState,
        tech_self: TechConfig,
        tech_enemy: TechConfig,
    ) -> int:
        inc_atk = attacker.bonus_dmg_dealt + tech_self.dealt_bonus
        dec_def = defender.bonus_dmg_recv + tech_enemy.recv_reduction
        ignore_def = attacker.has_status("破阵")
        result = calc_damage(
            num=attacker.current_troops,
            atk=attacker.force,
            def_=(0 if ignore_def else defender.command),
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
    ) -> int:
        if not target.alive:
            return 0

        # 抵御：免疫一次伤害（必中可穿透）
        if target.has_status("抵御") and not attacker.has_status("必中"):
            target.remove_status("抵御")
            self.log.append(LogEntry(eng, rnd, target.cfg.name, "抵御格挡", 0, attacker.cfg.name))
            return 0

        actual = target.take_damage(amount)

        # 反击：受到普通攻击时对攻方造成反伤
        if target.has_status("反击") and target.alive:
            counter = int(amount * 0.5)
            attacker.take_damage(counter)
            self.log.append(LogEntry(eng, rnd, target.cfg.name, "反击", counter, attacker.cfg.name))

        # 倒戈：物理吸血
        if attacker.has_status("倒戈"):
            absorb = int(actual * 0.3)
            attacker.heal(absorb)

        return actual

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
