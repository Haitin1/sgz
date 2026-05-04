-- Initial skill_release effect_json sample for active tactics.

BEGIN;

UPDATE skills SET effect_json = $$
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
$$::jsonb WHERE name = '绝计折谋';

COMMIT;

SELECT name, jsonb_array_length(effect_json->'effects') AS effect_count
FROM skills
WHERE name = '绝计折谋';
