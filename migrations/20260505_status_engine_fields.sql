-- Add structured engine fields for status dictionary.

BEGIN;

ALTER TABLE statuses
  ADD COLUMN IF NOT EXISTS engine_key VARCHAR(64),
  ADD COLUMN IF NOT EXISTS stackable BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS max_stacks INTEGER,
  ADD COLUMN IF NOT EXISTS default_duration INTEGER,
  ADD COLUMN IF NOT EXISTS can_be_cleansed BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS can_be_immune BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS is_control BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS hooks TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  ADD COLUMN IF NOT EXISTS params_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS extra_rules JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE statuses
SET engine_key = CASE name
  WHEN '武力' THEN 'force'
  WHEN '智力' THEN 'intel'
  WHEN '统率' THEN 'command'
  WHEN '速度' THEN 'speed'
  WHEN '政治' THEN 'politics'
  WHEN '魅力' THEN 'charm'
  WHEN '会心几率' THEN 'crit_rate'
  WHEN '奇谋几率' THEN 'qimou_rate'
  WHEN '发动几率' THEN 'skill_trigger_rate'
  WHEN '战法造成伤害' THEN 'skill_damage_dealt'
  WHEN '受到战法伤害' THEN 'skill_damage_taken'

  WHEN '休整' THEN 'regroup'
  WHEN '急救' THEN 'first_aid'
  WHEN '叛逃' THEN 'mutiny'
  WHEN '溃逃' THEN 'rout'
  WHEN '沙暴' THEN 'sandstorm'
  WHEN '中毒' THEN 'poison'
  WHEN '水攻' THEN 'flood'
  WHEN '灼烧' THEN 'burn'

  WHEN '法术普攻' THEN 'magic_normal_attack'
  WHEN '严密' THEN 'strict_guard'
  WHEN '看破' THEN 'pierce_guard'
  WHEN '准备时间' THEN 'prepare_time'
  WHEN '警戒' THEN 'vigilance'
  WHEN '援护' THEN 'guard_cover'
  WHEN '铁索连环' THEN 'linked_targets'
  WHEN '穷追' THEN 'focus_fire'
  WHEN '攻心' THEN 'strategy_lifesteal'
  WHEN '倒戈' THEN 'weapon_lifesteal'
  WHEN '破阵' THEN 'ignore_defense'
  WHEN '必中' THEN 'sure_hit'
  WHEN '遇袭' THEN 'delayed_action'
  WHEN '先攻' THEN 'first_strike'
  WHEN '洞察' THEN 'insight'
  WHEN '免疫' THEN 'immunity'
  WHEN '分担' THEN 'damage_share_single'
  WHEN '分摊' THEN 'damage_share_split'
  WHEN '反击' THEN 'counterattack'
  WHEN '群攻' THEN 'aoe_normal_attack'
  WHEN '抵御' THEN 'shield'
  WHEN '规避' THEN 'evade'
  WHEN '连击' THEN 'combo'

  WHEN '捕获' THEN 'captured'
  WHEN '破坏' THEN 'equipment_broken'
  WHEN '挑拨' THEN 'forced_skill_target'
  WHEN '伪报' THEN 'disable_command_passive'
  WHEN '嘲讽' THEN 'taunt'
  WHEN '禁疗' THEN 'heal_block'
  WHEN '虚弱' THEN 'no_damage'
  WHEN '混乱' THEN 'confusion'
  WHEN '缴械' THEN 'disarm'
  WHEN '计穷' THEN 'silence_active'
  WHEN '震慑' THEN 'stun'

  WHEN '单骑救主' THEN 'single_rider_rescue'
  WHEN '威震' THEN 'mighty_shock'
  WHEN '古之恶来' THEN 'gu_zhi_e_lai'
  WHEN '神机妙算' THEN 'divine_calculation'
  ELSE CONCAT('status_', id)
END
WHERE engine_key IS NULL OR engine_key = '';

UPDATE statuses s
SET is_control = (c.name = '控制状态')
FROM status_categories c
WHERE s.category_id = c.id;

UPDATE statuses
SET stackable = TRUE
WHERE name IN ('灼烧','水攻','中毒','沙暴','溃逃','叛逃','急救','休整','分担','分摊','威震','抵御');

UPDATE statuses
SET max_stacks = CASE name
  WHEN '灼烧' THEN 5
  WHEN '水攻' THEN 5
  WHEN '中毒' THEN 5
  WHEN '沙暴' THEN 5
  WHEN '溃逃' THEN 5
  WHEN '叛逃' THEN 5
  WHEN '急救' THEN 2
  WHEN '休整' THEN 2
  WHEN '分担' THEN 3
  WHEN '分摊' THEN 3
  WHEN '威震' THEN 5
  WHEN '抵御' THEN 8
  ELSE max_stacks
END;

UPDATE statuses
SET default_duration = CASE
  WHEN name IN ('灼烧','水攻','中毒','沙暴','溃逃','叛逃','急救','休整') THEN 2
  WHEN name IN ('震慑','计穷','缴械','混乱','虚弱','禁疗','嘲讽','挑拨','伪报','破坏') THEN 1
  WHEN name = '捕获' THEN 2
  ELSE default_duration
END;

UPDATE statuses
SET can_be_cleansed = FALSE
WHERE name IN ('捕获','威震','单骑救主','古之恶来','神机妙算');

UPDATE statuses
SET can_be_immune = FALSE
WHERE name IN ('伪报','捕获');

UPDATE statuses
SET hooks = ARRAY['round_start']
WHERE name IN ('灼烧','水攻','中毒','沙暴','溃逃','叛逃','急救','休整');

UPDATE statuses
SET hooks = ARRAY['before_normal_attack']
WHERE name IN ('连击','群攻','穷追','法术普攻');

UPDATE statuses
SET hooks = ARRAY['on_damage_taken']
WHERE name IN ('反击');

UPDATE statuses
SET hooks = ARRAY['after_damage']
WHERE name IN ('倒戈','攻心');

UPDATE statuses
SET hooks = ARRAY['before_damage']
WHERE name IN ('规避','抵御','必中','破阵','警戒','看破');

UPDATE statuses
SET hooks = ARRAY['target_select']
WHERE name IN ('援护','嘲讽','混乱','挑拨');

UPDATE statuses
SET hooks = ARRAY['try_cast_skill']
WHERE name IN ('计穷','准备时间');

UPDATE statuses
SET hooks = ARRAY['skill_enable_check']
WHERE name IN ('伪报');

UPDATE statuses
SET hooks = ARRAY['on_heal']
WHERE name IN ('禁疗');

UPDATE statuses
SET hooks = ARRAY['turn_order']
WHERE name IN ('先攻','遇袭');

UPDATE statuses
SET hooks = ARRAY['apply_status']
WHERE name IN ('洞察','免疫','严密');

UPDATE statuses
SET hooks = ARRAY['before_action']
WHERE name IN ('震慑','捕获');

UPDATE statuses
SET hooks = ARRAY['custom']
WHERE name IN ('单骑救主','威震','古之恶来','神机妙算','铁索连环');

UPDATE statuses s
SET params_schema = CASE c.name
  WHEN '属性状态' THEN jsonb_build_object(
    'value', 'number_or_range',
    'value_type', jsonb_build_array('flat','percent'),
    'operation', jsonb_build_array('add','subtract','multiply'),
    'duration', 'int'
  )
  WHEN '持续性状态' THEN jsonb_build_object(
    'rate', 'number_or_range',
    'damage_type', jsonb_build_array('兵刃','谋略','无视防御','治疗'),
    'scales_with', jsonb_build_array('武力','智力'),
    'duration', 'int'
  )
  WHEN '功能性状态' THEN jsonb_build_object(
    'operation', 'engine_defined',
    'value', 'optional_number_or_range',
    'duration', 'int'
  )
  WHEN '控制状态' THEN jsonb_build_object(
    'duration', 'int',
    'can_be_immune', 'bool',
    'can_be_cleansed', 'bool'
  )
  WHEN '特殊状态' THEN jsonb_build_object(
    'custom', 'object',
    'duration', 'int',
    'notes', 'skill_specific'
  )
  ELSE '{}'::jsonb
END
FROM status_categories c
WHERE s.category_id = c.id;

UPDATE statuses
SET extra_rules = jsonb_build_object(
  'ignore_insight', true,
  'disable_skill_types', jsonb_build_array('指挥','被动')
)
WHERE name = '伪报';

UPDATE statuses
SET extra_rules = jsonb_build_object(
  'extend_target', '抵御',
  'allow_stack', true,
  'max_duration_rounds', 8
)
WHERE name = '严密';

UPDATE statuses
SET extra_rules = jsonb_build_object(
  'operation', 'ignore_damage_reduction'
)
WHERE name = '看破';

UPDATE statuses
SET extra_rules = jsonb_build_object(
  'normal_attack_damage_type', '谋略'
)
WHERE name = '法术普攻';

UPDATE statuses
SET extra_rules = jsonb_build_object(
  'cannot_be_dispelled', true,
  'cannot_be_targeted_by_ally', true,
  'disable_actions', jsonb_build_array('normal_attack','active_skill','heal')
)
WHERE name = '捕获';

ALTER TABLE statuses
  ALTER COLUMN engine_key SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'statuses_engine_key_key'
  ) THEN
    ALTER TABLE statuses
      ADD CONSTRAINT statuses_engine_key_key UNIQUE (engine_key);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_statuses_engine_key ON statuses (engine_key);
CREATE INDEX IF NOT EXISTS idx_statuses_is_control ON statuses (is_control);
CREATE INDEX IF NOT EXISTS idx_statuses_hooks ON statuses USING GIN (hooks);
CREATE INDEX IF NOT EXISTS idx_statuses_params_schema ON statuses USING GIN (params_schema);

UPDATE statuses SET updated_at = now();

COMMIT;

SELECT COUNT(*) AS total,
       COUNT(engine_key) AS keyed,
       COUNT(*) FILTER (WHERE stackable) AS stackable_cnt,
       COUNT(*) FILTER (WHERE is_control) AS control_cnt
FROM statuses;
