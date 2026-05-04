-- Status dictionary and initial structured skill effect samples.
-- Generated from the data applied on 2026-05-04.

BEGIN;

CREATE TABLE IF NOT EXISTS status_categories (
  id SERIAL PRIMARY KEY,
  name VARCHAR(50) UNIQUE NOT NULL,
  description TEXT,
  display_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS statuses (
  id SERIAL PRIMARY KEY,
  name VARCHAR(50) UNIQUE NOT NULL,
  category_id INTEGER NOT NULL REFERENCES status_categories(id) ON DELETE RESTRICT,
  description TEXT NOT NULL DEFAULT '',
  aliases TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  notes TEXT,
  is_open_ended BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO status_categories (name, description, display_order) VALUES
  ('属性状态', '影响武将属性、概率、伤害增减等数值类状态。', 1),
  ('持续性状态', '按回合持续造成伤害或恢复兵力的状态。', 2),
  ('功能性状态', '改变行动、伤害结算、普攻规则、保护机制等战斗功能的状态。', 3),
  ('控制状态', '限制行动、普通攻击、战法发动、恢复或目标选择等控制类状态。', 4),
  ('特殊状态', '由特定战法产生、规则相对独立或不可完全枚举的专属状态。', 5)
ON CONFLICT (name) DO UPDATE SET
  description = EXCLUDED.description,
  display_order = EXCLUDED.display_order,
  updated_at = now();

WITH data(name, category_name, description, aliases, notes, is_open_ended) AS (
  VALUES
  ('武力', '属性状态', '增加或降低武将武力。', ARRAY[]::TEXT[], NULL, false),
  ('智力', '属性状态', '增加或降低武将智力。', ARRAY[]::TEXT[], NULL, false),
  ('统率', '属性状态', '增加或降低武将统率。', ARRAY[]::TEXT[], NULL, false),
  ('速度', '属性状态', '增加或降低武将速度。', ARRAY[]::TEXT[], NULL, false),
  ('政治', '属性状态', '增加或降低武将政治。', ARRAY[]::TEXT[], NULL, false),
  ('魅力', '属性状态', '增加或降低武将魅力。', ARRAY[]::TEXT[], NULL, false),
  ('会心几率', '属性状态', '造成双倍兵刃伤害的概率。', ARRAY['会心']::TEXT[], NULL, false),
  ('奇谋几率', '属性状态', '造成双倍谋略伤害的概率。', ARRAY['奇谋']::TEXT[], NULL, false),
  ('发动几率', '属性状态', '改变战法发动概率。', ARRAY[]::TEXT[], NULL, false),
  ('战法造成伤害', '属性状态', '改变自身战法造成的伤害。', ARRAY[]::TEXT[], NULL, false),
  ('受到战法伤害', '属性状态', '改变自身受到战法伤害。', ARRAY[]::TEXT[], NULL, false),

  ('灼烧', '持续性状态', '每回合造成谋略伤害，受智力影响；对藤甲兵有额外意义。', ARRAY[]::TEXT[], NULL, false),
  ('水攻', '持续性状态', '每回合造成谋略伤害，受智力影响。', ARRAY[]::TEXT[], NULL, false),
  ('中毒', '持续性状态', '每回合造成谋略伤害，受智力影响。', ARRAY[]::TEXT[], NULL, false),
  ('沙暴', '持续性状态', '每回合造成谋略伤害，受智力影响。', ARRAY[]::TEXT[], NULL, false),
  ('溃逃', '持续性状态', '每回合造成兵刃伤害，受武力影响。', ARRAY[]::TEXT[], NULL, false),
  ('叛逃', '持续性状态', '每回合造成无视防御伤害，取武力或智力较高项影响。', ARRAY[]::TEXT[], NULL, false),
  ('急救', '持续性状态', '受到伤害时恢复兵力，受智力影响。', ARRAY[]::TEXT[], NULL, false),
  ('休整', '持续性状态', '每回合恢复一次兵力，受智力影响。', ARRAY[]::TEXT[], NULL, false),

  ('连击', '功能性状态', '额外进行一次普通攻击。', ARRAY[]::TEXT[], NULL, false),
  ('规避', '功能性状态', '回避伤害，不触发攻击附带效果和突击。', ARRAY[]::TEXT[], NULL, false),
  ('抵御', '功能性状态', '免疫伤害，但仍触发攻击附带效果和突击。', ARRAY[]::TEXT[], NULL, false),
  ('群攻', '功能性状态', '普通攻击对目标同部队其他武将造成伤害。', ARRAY[]::TEXT[], NULL, false),
  ('反击', '功能性状态', '受到普通攻击时对攻击者造成伤害。', ARRAY[]::TEXT[], NULL, false),
  ('分摊', '功能性状态', '多个目标分别承担一部分伤害。', ARRAY[]::TEXT[], NULL, false),
  ('分担', '功能性状态', '为指定目标承担一部分伤害。', ARRAY[]::TEXT[], NULL, false),
  ('免疫', '功能性状态', '免疫指定状态。', ARRAY[]::TEXT[], NULL, false),
  ('洞察', '功能性状态', '免疫控制状态。', ARRAY[]::TEXT[], NULL, false),
  ('先攻', '功能性状态', '优先行动；多名先攻按速度排序。', ARRAY[]::TEXT[], NULL, false),
  ('遇袭', '功能性状态', '行动滞后；与先攻同时存在时互相抵消。', ARRAY[]::TEXT[], NULL, false),
  ('必中', '功能性状态', '命中目标时无视规避和抵御。', ARRAY[]::TEXT[], NULL, false),
  ('破阵', '功能性状态', '造成伤害时无视目标统率和智力。', ARRAY[]::TEXT[], NULL, false),
  ('倒戈', '功能性状态', '造成兵刃伤害时，按伤害量恢复自身兵力。', ARRAY[]::TEXT[], NULL, false),
  ('攻心', '功能性状态', '造成谋略伤害时，按伤害量恢复自身兵力。', ARRAY[]::TEXT[], NULL, false),
  ('穷追', '功能性状态', '普通攻击锁定目标。', ARRAY[]::TEXT[], NULL, false),
  ('铁索连环', '功能性状态', '任一目标受到伤害时，反馈一定比例伤害给其他目标。', ARRAY[]::TEXT[], NULL, false),
  ('援护', '功能性状态', '为目标承担普通攻击。', ARRAY[]::TEXT[], NULL, false),
  ('警戒', '功能性状态', '减少受到的伤害，但仍触发攻击附带效果和突击。', ARRAY[]::TEXT[], NULL, false),
  ('准备时间', '功能性状态', '改变战法准备回合。', ARRAY[]::TEXT[], NULL, false),
  ('看破', '功能性状态', '造成伤害时无视目标一定比例的受到伤害降低效果。', ARRAY[]::TEXT[], '用户补充截图确认。', false),
  ('严密', '功能性状态', '抵御状态可叠加，且抵御持续时间延长至8回合。', ARRAY[]::TEXT[], '用户补充截图确认。', false),
  ('法术普攻', '功能性状态', '普通攻击转为谋略伤害。', ARRAY[]::TEXT[], '用户补充截图确认。', false),

  ('震慑', '控制状态', '无法行动。', ARRAY[]::TEXT[], NULL, false),
  ('计穷', '控制状态', '无法发动主动战法。', ARRAY['技穷']::TEXT[], '库内建议统一使用“计穷”。', false),
  ('缴械', '控制状态', '无法普通攻击。', ARRAY[]::TEXT[], NULL, false),
  ('混乱', '控制状态', '攻击和战法无差别选择目标。', ARRAY[]::TEXT[], NULL, false),
  ('虚弱', '控制状态', '无法造成伤害。', ARRAY[]::TEXT[], NULL, false),
  ('禁疗', '控制状态', '无法恢复兵力。', ARRAY[]::TEXT[], NULL, false),
  ('嘲讽', '控制状态', '强制目标普通攻击自己。', ARRAY[]::TEXT[], NULL, false),
  ('伪报', '控制状态', '禁用指挥和被动战法，无视洞察。', ARRAY[]::TEXT[], NULL, false),
  ('挑拨', '控制状态', '强迫目标施放战法时选择自己。', ARRAY[]::TEXT[], NULL, false),
  ('破坏', '控制状态', '携带的装备失效。', ARRAY[]::TEXT[], NULL, false),
  ('捕获', '控制状态', '无法行动和造成伤害，禁用指挥/被动，进入禁疗，无法被友方选中，且无法被净化。', ARRAY[]::TEXT[], NULL, false),

  ('神机妙算', '特殊状态', '战法专属特殊状态，来源于诸葛亮战法。', ARRAY[]::TEXT[], '特殊状态不是封闭集合，后续按战法继续补充。', true),
  ('古之恶来', '特殊状态', '战法专属特殊状态，来源于典韦战法。', ARRAY[]::TEXT[], '特殊状态不是封闭集合，后续按战法继续补充。', true),
  ('威震', '特殊状态', '特殊不利状态，可叠加，达到条件后转为震慑，且不可净化。', ARRAY[]::TEXT[], '特殊状态不是封闭集合，后续按战法继续补充。', true),
  ('单骑救主', '特殊状态', '特殊状态，触发后强化指定战法效果，不可净化。', ARRAY[]::TEXT[], '特殊状态不是封闭集合，后续按战法继续补充。', true)
)
INSERT INTO statuses (name, category_id, description, aliases, notes, is_open_ended)
SELECT d.name, c.id, d.description, d.aliases, d.notes, d.is_open_ended
FROM data d
JOIN status_categories c ON c.name = d.category_name
ON CONFLICT (name) DO UPDATE SET
  category_id = EXCLUDED.category_id,
  description = EXCLUDED.description,
  aliases = EXCLUDED.aliases,
  notes = EXCLUDED.notes,
  is_open_ended = EXCLUDED.is_open_ended,
  updated_at = now();

COMMIT;

SELECT c.name || E'\t' || count(s.id)
FROM status_categories c
LEFT JOIN statuses s ON s.category_id = c.id
GROUP BY c.id, c.name, c.display_order
ORDER BY c.display_order;

BEGIN;

UPDATE skills SET effect_json = $$
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
        "value": {"base": 12, "max": 24},
        "value_type": "flat",
        "scales_with": "智力",
        "duration": 2,
        "stackable": false
      }
    },
    {
      "event": "round_start",
      "condition": "round >= 3",
      "target": "ally_group_2_3",
      "action": "heal",
      "heal": {
        "rate": {"base": 0.44, "max": 0.88},
        "scales_with": "智力"
      }
    }
  ]
}
$$::jsonb WHERE name = '守望相助';

UPDATE skills SET effect_json = $$
{
  "version": 1,
  "effects": [
    {
      "event": "on_normal_attack_taken",
      "target": "ally_group_2_3",
      "action": "apply_status",
      "chance": {"base": 0.25, "max": 0.50, "main_base": 0.30, "main_max": 0.60},
      "chance_scales_with": "统率",
      "status": {
        "name": "受到伤害提升",
        "category": "属性状态",
        "operation": "add",
        "value": {"base": 0.03, "max": 0.06},
        "value_type": "percent",
        "duration": -1,
        "stackable": true,
        "max_stacks": 3
      }
    },
    {
      "event": "on_first_round_trigger",
      "condition": "damage_source.force > damage_source.intel",
      "target": "damage_source",
      "action": "apply_status",
      "status": {"name": "缴械", "category": "控制状态", "duration": 2, "stackable": false}
    },
    {
      "event": "on_first_round_trigger",
      "condition": "damage_source.force <= damage_source.intel",
      "target": "damage_source",
      "action": "apply_status",
      "status": {"name": "计穷", "category": "控制状态", "duration": 2, "stackable": false}
    }
  ]
}
$$::jsonb WHERE name = '渊然难测';

UPDATE skills SET effect_json = $$
{
  "version": 1,
  "effects": [
    {
      "event": "round_start",
      "condition": "round % 2 == 1",
      "target": "all_ally",
      "action": "apply_status",
      "status": {
        "name": "统率",
        "category": "属性状态",
        "attr": "统率",
        "operation": "add",
        "value": {"base": 34, "max": 68},
        "value_type": "flat",
        "duration": 1,
        "stackable": false,
        "notes": "对黄巾武将生效时，受主将魅力影响"
      }
    },
    {
      "event": "round_start",
      "condition": "round % 2 == 1 AND target.charm < ally_main.charm",
      "target": "all_enemy",
      "action": "apply_status",
      "status": {"name": "禁疗", "category": "控制状态", "duration": 2, "stackable": false}
    },
    {
      "event": "round_start",
      "condition": "round % 2 == 0",
      "target": "all_ally",
      "action": "apply_status",
      "status": {
        "name": "受到主动突击伤害降低",
        "category": "属性状态",
        "operation": "add",
        "value": {"base": 0.12, "max": 0.24},
        "value_type": "percent",
        "duration": 2,
        "stackable": false,
        "notes": "对黄巾武将生效时，受统率影响"
      }
    }
  ]
}
$$::jsonb WHERE name = '聚石成金';

UPDATE skills SET effect_json = $$
{
  "version": 1,
  "effects": [
    {
      "event": "battle_start",
      "target": "all_ally_shield_advanced",
      "action": "apply_status",
      "status": {
        "name": "统率",
        "category": "属性状态",
        "attr": "统率",
        "operation": "add",
        "value": {"base": 18, "max": 36},
        "value_type": "flat",
        "duration": 8,
        "stackable": false
      }
    },
    {
      "event": "battle_start",
      "target": "all_ally_shield_advanced",
      "action": "apply_status",
      "status": {
        "name": "丹阳兵",
        "category": "特殊状态",
        "operation": "custom",
        "duration": 8,
        "stackable": true,
        "notes": "根据上一回合我军全体兵刃伤害获得一定比例丹阳兵，可叠加；受到谋略伤害时消耗丹阳兵抵挡部分谋略伤害，数量每回合衰减；陶谦统领时抵挡比例提升。"
      }
    }
  ]
}
$$::jsonb WHERE name = '丹阳兵';

COMMIT;

SELECT name, jsonb_array_length(effect_json->'effects') AS effect_count
FROM skills
WHERE name IN ('守望相助','渊然难测','聚石成金','丹阳兵')
ORDER BY name;
