"""
三国志战略版伤害计算引擎
整合三个参考模型：
  - 模型1 (知乎 zhuanlan.zhihu.com/p/439300738)  ← 主干，实测拟合
  - 模型2 (铃鹿官方论坛)                          ← 简化版，仅做参考
  - 模型3 (百度贴吧 tieba.baidu.com/p/8176810879)  ← 补充：保底/阶段/等级差
"""

import math
import random

# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────

# 兵种适应性系数  [来源：模型1]
TROOP_COEF = {"S": 1.2, "A": 1.0, "B": 0.85, "C": 0.7}

# 兵种克制关系  骑>弓>枪>骑，盾克器，器特殊
TROOP_COUNTER = {
    ("cavalry", "bow"):    1.11,
    ("bow",     "spear"):  1.11,
    ("spear",   "cavalry"):1.11,
    ("shield",  "machine"):1.11,
}

# 伤害浮动：9个离散点，±4.4%，每档差1.1%  [来源：模型1]
FLOAT_STEPS = [round(1.0 - 4 * 0.011 + i * 0.011, 4) for i in range(9)]
# = [0.956, 0.967, 0.978, 0.989, 1.0, 1.011, 1.022, 1.033, 1.044]

# 士气减伤公式参数  [来源：模型1]
MORALE_K = 0.007

# 等级差伤害修正（每级差±0.8%）  [来源：模型3]
LEVEL_DIFF_RATE = 0.008

# 最低伤害下限  [来源：模型3]
MIN_DAMAGE = 10.8

# 满级  [来源：模型3]
MAX_LEVEL = 50


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────

def troop_adaptability(base_stat: float, troop_grade: str) -> float:
    """
    武将三维属性 × 兵种适应性系数
    base_stat  : 武将面板属性（武力/智力/统率）
    troop_grade: 兵种等级 'S'/'A'/'B'/'C'
    """
    return base_stat * TROOP_COEF.get(troop_grade.upper(), 1.0)


def troop_counter_coef(atk_troop: str, def_troop: str) -> float:
    """兵种克制系数，无克制关系返回 1.0"""
    return TROOP_COUNTER.get((atk_troop.lower(), def_troop.lower()), 1.0)


def morale_reduction(morale_diff: float) -> float:
    """
    士气减伤系数  [模型1]
    morale_diff : 攻方士气 - 守方士气（正数对攻方有利）
    返回守方的士气减伤乘数（< 1 时守方承受更多伤害）
    """
    return max(0.1, 1.0 - MORALE_K * max(morale_diff, 0))


def level_diff_mod(atk_level: int, def_level: int) -> float:
    """
    等级差伤害修正  [模型3]
    防守方每比攻击方低一级，受到伤害 +0.8%；反之 -0.8%
    """
    diff = atk_level - def_level          # 正数表示攻方等级更高
    return 1.0 + diff * LEVEL_DIFF_RATE


def level_attr_coef(level: int) -> float:
    """
    等级属性系数  [模型3]
    = log2(level+1) / ln(51)
    满级50时 ≈ 1.4427
    """
    return math.log2(level + 1) / math.log(51)


def apply_float(dmg: float, seed=None) -> float:
    """
    随机取伤害浮动（9个离散点中的一个）  [模型1]
    seed 用于复现测试
    """
    if seed is not None:
        random.seed(seed)
    return int(dmg * random.choice(FLOAT_STEPS))


# ─────────────────────────────────────────────
# 核心公式（模型1，知乎）
# ─────────────────────────────────────────────

def f1_troop(num: float, atk_minus_def: float) -> float:
    """
    兵力伤害项  [模型1 公式2]
    f1 = 0.1877 × Num^0.32 × (Atk - Def)
    """
    if num <= 0:
        return 0.0
    return 0.1877 * (num ** 0.32) * atk_minus_def


def f2_general(atk_minus_def: float) -> float:
    """
    武将属性伤害项  [模型1 公式3]
    f2 = 0.0005x² + 0.9x + 4.5，x = Atk - Def
    """
    x = atk_minus_def
    return 0.0005 * x ** 2 + 0.9 * x + 4.5


# ─────────────────────────────────────────────
# HP阶段（模型3，贴吧）
# ─────────────────────────────────────────────

def hp_phase(current_num: int, level: int = MAX_LEVEL) -> str:
    """
    判断当前兵力所处的HP阶段  [模型3]
    假血：level×100 ~ level×200
    真血：2001 ~ level×100
    残血：1 ~ 2000
    """
    if current_num > level * 100:
        return "fake"     # 假血
    elif current_num > 2000:
        return "real"     # 真血
    else:
        return "low"      # 残血


def base_damage_tieba(current_num: int, level: int = MAX_LEVEL) -> float:
    """
    保底伤害（花括号项）—— 不受增减伤影响  [模型3]
    假血阶段：固定90
    真血/残血：0.018 × 兵力
    """
    phase = hp_phase(current_num, level)
    if phase == "fake":
        return 90.0
    else:
        return 0.018 * current_num


def variable_damage_tieba(current_num: int, atk: float, def_: float,
                          level: int = MAX_LEVEL) -> float:
    """
    可变伤害（中括号项）—— 受增减伤影响  [模型3]
    假血/真血：90×log2(兵力) - 809.2 + 系数×(攻-防)
    残血：    0.09×兵力 + 系数×(攻-防)
    最小为0
    """
    coef = level_attr_coef(level)
    attr_part = coef * (atk - def_)
    phase = hp_phase(current_num, level)

    if phase in ("fake", "real"):
        troop_part = 90 * math.log2(max(current_num, 1)) - 809.2
    else:
        troop_part = 0.09 * current_num

    return max(0.0, troop_part + attr_part)


# ─────────────────────────────────────────────
# 主计算函数
# ─────────────────────────────────────────────

def calc_damage(
    num: int,                    # 攻方当前兵力
    atk: float,                  # 攻方攻击属性（兵刃=武力，谋略=智力）
    def_: float,                 # 守方防御属性（兵刃=统率，谋略=智力）
    def_num: int = None,         # 守方当前兵力（用于HP阶段判断，None则不用模型3阶段）
    atk_troop_grade: str = "A",  # 攻方兵种适应性等级
    def_troop_grade: str = "A",  # 守方兵种适应性等级
    atk_troop_type: str = None,  # 攻方兵种类型（用于克制）
    def_troop_type: str = None,  # 守方兵种类型（用于克制）
    skill_rate: float = 1.0,     # 战法伤害系数 R（普攻=1.0）
    inc_atk: float = 0.0,        # 攻方增伤
    dec_atk: float = 0.0,        # 攻方减伤（伤害降低）
    inc_def: float = 0.0,        # 守方易伤
    dec_def: float = 0.0,        # 守方减伤
    crit: float = 0.0,           # 会心加成（普通=0，触发=1.0即+100%）
    morale_diff: float = 0.0,    # 攻方-守方 士气差
    atk_level: int = MAX_LEVEL,  # 攻方等级
    def_level: int = MAX_LEVEL,  # 守方等级
    use_float: bool = True,      # 是否加入9档浮动
    use_tieba_base: bool = True, # 是否加入贴吧保底（需要 def_num）
) -> dict:
    """
    完整伤害计算

    返回 dict，包含：
      total       最终伤害（含浮动）
      base        保底伤害（不受增减伤影响）
      variable    可变伤害（受增减伤影响）
      phase       HP阶段（fake/real/low/N/A）
      float_step  本次浮动系数
    """
    # ── 1. 属性经过兵种适应性修正 ──────────────────────
    atk_eff  = troop_adaptability(atk,  atk_troop_grade)
    def_eff  = troop_adaptability(def_, def_troop_grade)
    x = atk_eff - def_eff

    # ── 2. 基础伤害（模型1） ──────────────────────────
    dmg_base_m1 = f1_troop(num, x) + f2_general(x)

    # ── 3. 增减伤乘区 ────────────────────────────────
    dec_def_capped = min(dec_def, 0.9)   # 减伤上限90%
    dec_atk_capped = min(dec_atk, 0.9)
    modifier = (
        (1 + inc_atk - dec_atk_capped)
        * (1 + inc_def - dec_def_capped)
        * skill_rate
        * (1 + crit)
    )

    # ── 4. 独立乘区 ──────────────────────────────────
    counter = troop_counter_coef(atk_troop_type or "", def_troop_type or "")
    morale  = morale_reduction(morale_diff)
    lv_mod  = level_diff_mod(atk_level, def_level)
    independent = counter * morale * lv_mod

    # ── 5. 保底 + 可变（模型3，可选） ────────────────
    phase = "N/A"
    if use_tieba_base and def_num is not None:
        phase   = hp_phase(def_num, def_level)
        base    = base_damage_tieba(def_num, def_level)
        var     = variable_damage_tieba(def_num, atk_eff, def_eff, def_level)
        dmg_variable = var * modifier * independent
        total_before_float = base + dmg_variable
    else:
        # 纯模型1
        base  = 0.0
        total_before_float = dmg_base_m1 * modifier * independent

    total_before_float = max(total_before_float, MIN_DAMAGE)

    # ── 6. 浮动 ──────────────────────────────────────
    if use_float:
        float_step = random.choice(FLOAT_STEPS)
        total = int(total_before_float * float_step)
    else:
        float_step = 1.0
        total = int(total_before_float)

    total = max(total, int(MIN_DAMAGE))

    return {
        "total":      total,
        "base":       round(base, 2),
        "variable":   round(total_before_float - base, 2),
        "phase":      phase,
        "float_step": float_step,
    }


# ─────────────────────────────────────────────
# 模型2 简化版（铃鹿官方）
# ─────────────────────────────────────────────

def calc_damage_simple(
    num: int,
    atk: float,
    def_: float,
    troop_counter_coef_val: float = 1.0,
    troop_type_coef: float = 1.0,
    skill_rate: float = 1.0,
    crit: float = 0.0,
) -> float:
    """
    模型2（铃鹿官方简化版）
    S = ((Z武力 - Z敌统率) / 150 + 1) × 兵力/20 × 战法伤害率 × 暴击加成
    """
    Z_atk = atk * troop_type_coef * troop_counter_coef_val
    Z_def = def_
    return ((Z_atk - Z_def) / 150 + 1) * (num / 20) * skill_rate * (1 + crit)


# ─────────────────────────────────────────────
# 简单测试
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 伤害计算引擎测试 ===\n")

    # 示例：SP荀彧（智力95）对战张辽（统率92），各5000兵，谋略伤害
    result = calc_damage(
        num=5000, atk=95, def_=92,
        def_num=5000,
        atk_troop_grade="S", def_troop_grade="A",
        atk_troop_type="cavalry", def_troop_type="spear",
        skill_rate=2.5,         # 战法系数250%
        inc_atk=0.2,            # 攻方增伤20%
        crit=1.0,               # 触发会心
        use_float=False,        # 关闭浮动便于对比
    )
    print(f"SP荀彧 vs 张辽（5000兵，谋略×250%，会心）")
    print(f"  总伤害:  {result['total']}")
    print(f"  保底:    {result['base']}")
    print(f"  可变:    {result['variable']}")
    print(f"  HP阶段:  {result['phase']}")

    # 浮动范围演示
    print(f"\n9档浮动系数: {FLOAT_STEPS}")
    print(f"等级属性系数（满级50）: {level_attr_coef(50):.4f}")
    print(f"兵种适应性 S/A/B/C: {[TROOP_COEF[k] for k in 'SABC']}")
