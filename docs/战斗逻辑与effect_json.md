# 战斗逻辑与 effect_json 规范

本文档定义战法结构化数据的目标格式。`skills.description` 只用于展示原文说明，战斗引擎应读取 `skills.effect_json`，避免从中文描述中临时解析数值。

## 总体原则

- 一个战法可以拆成多个 `effects`。
- 每个 effect 必须声明触发事件 `event`、目标 `target` 和动作 `action`。
- 属性、状态、伤害、治疗、概率都用结构化字段描述。
- 状态字典表 `statuses` 解释“状态是什么”，战法 `effect_json` 解释“何时给谁施加什么数值”。
- 当前无法完整建模的复杂条件，先放入 `condition` 或 `notes`，但不要丢失原始数值。

## 顶层结构

```json
{
  "version": 1,
  "effects": [
    {
      "event": "battle_start",
      "target": "all_ally",
      "action": "apply_status",
      "status": {
        "name": "统率",
        "category": "属性状态",
        "attr": "统率",
        "operation": "add",
        "value": {"base": 18, "max": 36},
        "value_type": "flat",
        "duration": 8,
        "stackable": true
      }
    }
  ]
}
```

主动战法释放时示例：

```json
{
  "version": 1,
  "effects": [
    {
      "event": "skill_release",
      "target": "random_enemy",
      "action": "deal_damage",
      "damage": {
        "type": "谋略",
        "rate": {"base": 1.18, "max": 2.36},
        "scales_with": "智力",
        "defense_attr": "智力"
      }
    },
    {
      "event": "skill_release",
      "target": "random_enemy",
      "action": "apply_status",
      "status": {
        "name": "计穷",
        "category": "控制状态",
        "duration": 1,
        "stackable": false
      }
    }
  ]
}
```

突击战法普攻后示例：

```json
{
  "version": 1,
  "effects": [
    {
      "event": "after_normal_attack",
      "target": "normal_attack_target",
      "action": "deal_damage",
      "damage": {
        "type": "兵刃",
        "rate": {"base": 0.925, "max": 0.925},
        "scales_with": "武力",
        "defense_attr": "统率"
      }
    },
    {
      "event": "after_normal_attack",
      "target": "normal_attack_target",
      "action": "apply_status",
      "status": {
        "name": "计穷",
        "category": "控制状态",
        "duration": 1,
        "stackable": false
      }
    }
  ]
}
```

## 常用字段

| 字段 | 说明 |
|------|------|
| version | 格式版本，当前为 1 |
| effects | 战法拆解后的效果列表 |
| event | 触发时点 |
| target | 目标范围 |
| action | 动作类型 |
| condition | 触发条件 |
| chance | 触发概率 |
| status | 施加或修改的状态实例 |
| damage | 伤害结构 |
| heal | 治疗结构 |
| duration | 持续回合 |
| notes | 暂时无法完全结构化的补充说明 |

## 条件表达式

当前支持的 `condition`：

- `round >= 3`
- `round % 2 == 1`
- `caster.is_main`
- `not caster.is_main`
- `caster.has_status("连击")`
- `target.has_status("灼烧")`
- `target.force > target.intel`
- `caster.intel >= target.intel`
- `target.troops_pct < 0.5`

条件可以用 `AND` 连接，例如：

```json
{
  "condition": "round >= 2 AND caster.is_main"
}
```

## 事件枚举

| event | 说明 |
|------|------|
| battle_start | 战斗准备阶段 |
| round_start | 回合开始 |
| before_action | 武将行动前 |
| try_cast_skill | 尝试发动战法 |
| skill_prepare | 战法进入准备 |
| skill_release | 战法释放 |
| before_normal_attack | 普通攻击前 |
| after_normal_attack | 普通攻击后 |
| before_damage | 伤害计算前 |
| after_damage | 伤害造成后 |
| on_damage_taken | 受到伤害时 |
| on_heal | 恢复兵力时 |
| round_end | 回合结束 |
| battle_end | 战斗结束 |

## 动作枚举

| action | 说明 |
|------|------|
| apply_status | 施加状态 |
| remove_status | 移除状态 |
| deal_damage | 造成伤害 |
| heal | 恢复兵力 |
| modify_damage | 修改伤害 |
| modify_heal | 修改治疗 |
| modify_target | 修改目标 |
| disable_skill_type | 禁用战法类型 |
| trigger_attack | 触发一次攻击 |

伤害修正示例：

```json
{
  "event": "before_damage",
  "target": "damage_target",
  "action": "modify_damage",
  "damage_modifier": {
    "operation": "reduce_percent",
    "value": {"base": 0.2, "max": 0.4}
  }
}
```

## 目标枚举

| target | 说明 |
|------|------|
| self | 自身 |
| ally_main | 我方主将 |
| enemy_main | 敌方主将 |
| ally_deputies | 我方副将 |
| enemy_deputies | 敌方副将 |
| single_ally | 我方单体 |
| single_enemy | 敌军单体 |
| all_ally | 我军全体 |
| all_enemy | 敌军全体 |
| ally_group_2_3 | 我军群体 2-3 人 |
| enemy_group_2_3 | 敌军群体 2-3 人 |
| damage_source | 伤害来源 |
| damage_target | 受到伤害的目标 |
| normal_attack_target | 本次普通攻击目标 |
| random_enemy | 敌军随机武将 |
| random_ally | 我方随机武将 |
| enemy_lowest_troops | 敌军兵力最低武将 |
| ally_lowest_troops | 我方兵力最低武将 |
| enemy_highest_force | 敌军武力最高武将 |
| ally_highest_force | 我方武力最高武将 |
| enemy_highest_intel | 敌军智力最高武将 |
| ally_highest_intel | 我方智力最高武将 |
| enemy_highest_command | 敌军统率最高武将 |
| ally_highest_command | 我方统率最高武将 |
| enemy_highest_speed | 敌军速度最高武将 |
| ally_highest_speed | 我方速度最高武将 |

## 状态结构

```json
{
  "name": "看破",
  "category": "功能性状态",
  "operation": "ignore_damage_reduction",
  "value": {"base": 0.15, "max": 0.30},
  "value_type": "percent",
  "duration": 2,
  "stackable": false
}
```

| 字段 | 说明 |
|------|------|
| name | 状态名，对应 `statuses.name` |
| category | 状态分类 |
| attr | 属性状态对应的属性名，如 武力/智力/统率/速度 |
| operation | add/subtract/multiply/ignore_damage_reduction 等 |
| value | 数值，建议使用 `{base,max}` |
| value_type | flat/percent |
| duration | 持续回合，`-1` 表示本局永久 |
| stackable | 是否可叠加 |

## 已接入引擎的状态

当前 `battle_engine.py` 已支持状态携带数值，并初步接入：

- `属性状态`：通过 `attr/value/value_type` 修改武力、智力、统率、速度。
- `看破`：按状态值削弱目标减伤实际生效比例。
- `法术普攻`：普通攻击改用智力对智力的谋略普攻路径。
- `洞察`：免疫控制状态。
- `禁疗`：阻止普通治疗和持续治疗。
- `伪报`：禁用自身指挥/被动战法效果。
- `虚弱`：造成伤害为 0。
- `混乱`：普通攻击目标在敌我存活武将中随机。
- `嘲讽`：强制敌方普通攻击优先选择嘲讽来源。
- 非可叠加状态：同名状态再次施加时刷新/更新已有状态，而不是重复挂多层。
- `灼烧/水攻/中毒/沙暴`：回合开始结算谋略持续伤害。
- `溃逃`：回合开始结算兵刃持续伤害。
- `叛逃`：回合开始结算无视防御持续伤害。
- `休整`：回合开始结算持续治疗。

## 已接入的 effect_json 执行能力

当前 `battle_engine.py` 已有最小执行器：

- `SkillDef.effect_json` 可携带结构化效果。
- `/api/skills` 会返回 `effect_json`，前端选战法后会把它带入 `/api/simulate`。
- 已执行事件：`battle_start`、`round_start`、`skill_release`、`after_normal_attack`、`on_damage_taken`。
- 已执行动作：`apply_status`、`heal`、`deal_damage`。
- `round_start` 目前只执行 `指挥/被动/兵种/阵法` 的结构化效果，主动/突击战法必须等实际发动时才执行 `skill_release`，避免每回合自动触发。
- 如果战法存在 `skill_release` 结构化效果，释放时优先执行结构化效果并跳过旧字段伤害/治疗/施加状态，避免同一个战法重复结算。
- 突击战法现在挂在每次普通攻击后的 `after_normal_attack`；如果没有结构化效果，会回落到旧字段执行。
- `on_damage_taken` 目前会在实际受到伤害后触发受伤者自身的 `指挥/被动/兵种/阵法` 结构化效果。
- `before_damage` 已支持 `modify_damage`，可在扣兵前调整伤害。
- `after_damage` 会在实际扣兵后触发双方结构化效果，并输出兵力变化。
- `before_action` 会在武将行动前触发自身指挥/被动/兵种/阵法效果。
- `round_end` 会在状态持续时间扣减前触发；状态到期时输出 `status_expired` 战报事件。
- 战报日志保留旧字段，同时新增 `event`、`source_skill`、`damage_type`、`troops_before`、`troops_after`、`details`，用于逐步还原真实战报。
- 持续性状态在回合开始输出 `status_tick_damage` / `status_tick_heal` 战报事件。
- 数值默认取 `{base,max}` 里的 `max`，因为当前数据库录入按 10 级战法效果为准。

## 后续接入顺序

1. 增加更多结构化目标选择策略，例如女性、黄巾、特定阵营、指定站位。
2. 扩展条件表达式，例如兵力百分比、多个目标分别判断、阵营/性别/兵种判断。
3. 增强持续性状态结算：按来源当前属性、状态刷新/覆盖规则、同类状态冲突。
4. 把高频战法逐个写入 `effect_json`，每个复杂战法用真实战报核对触发时点和日志。
5. 暂不从 `description` 自动反推逻辑，避免错误解析。
