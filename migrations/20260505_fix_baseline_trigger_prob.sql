-- Fix trigger probability normalization for baseline structured skills.

BEGIN;

WITH baseline AS (
  SELECT s.*
  FROM skills s
  WHERE s.effect_json->>'parser' = 'sql_backfill_20260505'
),
derived AS (
  SELECT
    s.id,
    CASE
      WHEN s.category = '主动' THEN 'skill_release'
      WHEN s.category = '突击' THEN 'after_normal_attack'
      WHEN s.category IN ('指挥', '被动', '兵种', '阵法') THEN 'battle_start'
      ELSE 'battle_start'
    END AS suggested_event,
    CASE
      WHEN COALESCE(s.target, '') ~ '自己' THEN 'self'
      WHEN COALESCE(s.target, '') ~ '敌军单体' THEN 'single_enemy'
      WHEN COALESCE(s.target, '') ~ '我军单体|友军单体' THEN 'single_ally'
      WHEN COALESCE(s.target, '') ~ '敌军主将' THEN 'enemy_main'
      WHEN COALESCE(s.target, '') ~ '我军主将|友军主将' THEN 'ally_main'
      WHEN COALESCE(s.target, '') ~ '敌军群体\(1-2人\)|敌军群体（1-2人）' THEN 'enemy_group_1_2'
      WHEN COALESCE(s.target, '') ~ '敌军群体\(2-3人\)|敌军群体（2-3人）|敌军群体 2-3 人' THEN 'enemy_group_2_3'
      WHEN COALESCE(s.target, '') ~ '我军群体\(2-3人\)|我军群体（2-3人）|我军群体 2-3 人' THEN 'ally_group_2_3'
      WHEN COALESCE(s.target, '') ~ '敌军群体' THEN 'all_enemy'
      WHEN COALESCE(s.target, '') ~ '我军群体|友军群体|我军全体' THEN 'all_ally'
      WHEN COALESCE(s.target, '') ~ '双方全体' THEN 'all_both'
      ELSE 'self'
    END AS suggested_target,
    COALESCE(
      (
        SELECT jsonb_agg(DISTINCT st.name)
        FROM statuses st
        WHERE COALESCE(s.description, '') LIKE '%' || st.name || '%'
      ),
      '[]'::jsonb
    ) AS status_mentions,
    COALESCE(
      (
        SELECT jsonb_agg(
          jsonb_build_object(
            'base_pct', (m[1])::numeric,
            'max_pct', (m[2])::numeric,
            'base_rate', ROUND(((m[1])::numeric / 100.0)::numeric, 4),
            'max_rate', ROUND(((m[2])::numeric / 100.0)::numeric, 4)
          )
        )
        FROM regexp_matches(
          COALESCE(s.description, ''),
          '([0-9]+(?:\\.[0-9]+)?)%\\s*→\\s*([0-9]+(?:\\.[0-9]+)?)%',
          'g'
        ) AS m
      ),
      '[]'::jsonb
    ) AS percent_pairs,
    to_jsonb(
      array_remove(
        ARRAY[
          CASE WHEN COALESCE(s.description, '') ~ '(造成|受到)[^。；，]*伤害|伤害率' THEN 'deal_damage' END,
          CASE WHEN COALESCE(s.description, '') ~ '恢复兵力|治疗率|恢复我军|治疗' THEN 'heal' END,
          CASE WHEN COALESCE(s.description, '') ~ '状态|提高|降低|获得|进入|施加|偷取|免疫|禁疗|缴械|计穷|震慑|混乱' THEN 'apply_status' END
        ],
        NULL
      )
    ) AS suggested_actions
  FROM baseline s
)
UPDATE skills s
SET effect_json = jsonb_strip_nulls(
  jsonb_build_object(
    'version', 1,
    'structured_level', 'baseline_v1',
    'parser', 'sql_backfill_20260505',
    'meta', jsonb_strip_nulls(
      jsonb_build_object(
        'skill_id', s.id,
        'name', s.name,
        'category', s.category,
        'quality', s.quality,
        'source', s.source,
        'effect_tag', s.effect,
        'target_text', s.target,
        'conflict', s.conflict,
        'is_inherited', s.is_inherited,
        'is_event', s.is_event,
        'is_builtin', s.is_builtin,
        'trigger_prob_raw', s.trigger_prob,
        'trigger_prob_pct', CASE
          WHEN s.trigger_prob IS NULL THEN NULL
          WHEN s.trigger_prob > 1 THEN ROUND(s.trigger_prob::numeric, 2)
          ELSE ROUND((s.trigger_prob * 100.0)::numeric, 2)
        END,
        'trigger_prob_rate', CASE
          WHEN s.trigger_prob IS NULL THEN NULL
          WHEN s.trigger_prob > 1 THEN ROUND((s.trigger_prob / 100.0)::numeric, 4)
          ELSE ROUND(s.trigger_prob::numeric, 4)
        END
      )
    ),
    'raw', jsonb_build_object(
      'description', COALESCE(s.description, ''),
      'duration', COALESCE(s.duration, '')
    ),
    'hints', jsonb_build_object(
      'suggested_event', d.suggested_event,
      'suggested_target', d.suggested_target,
      'suggested_actions', COALESCE(d.suggested_actions, '[]'::jsonb),
      'status_mentions', d.status_mentions,
      'percent_pairs', d.percent_pairs
    ),
    'effects', jsonb_build_array(
      jsonb_strip_nulls(
        jsonb_build_object(
          'event', d.suggested_event,
          'target', d.suggested_target,
          'action', 'todo_parse',
          'chance', CASE
            WHEN s.trigger_prob IS NULL THEN NULL
            ELSE jsonb_build_object(
              'base', CASE
                WHEN s.trigger_prob > 1 THEN ROUND((s.trigger_prob / 100.0)::numeric, 4)
                ELSE ROUND(s.trigger_prob::numeric, 4)
              END,
              'max', CASE
                WHEN s.trigger_prob > 1 THEN ROUND((s.trigger_prob / 100.0)::numeric, 4)
                ELSE ROUND(s.trigger_prob::numeric, 4)
              END
            )
          END,
          'notes', '自动结构化占位：已提取原文与线索，待精细拆解'
        )
      )
    )
  )
)
FROM derived d
WHERE s.id = d.id
  AND s.effect_json->>'parser' = 'sql_backfill_20260505';

COMMIT;

SELECT COUNT(*) AS fixed_rows
FROM skills
WHERE effect_json->>'parser' = 'sql_backfill_20260505';
