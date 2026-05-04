-- Initial after_normal_attack effect_json sample for assault tactics.

BEGIN;

UPDATE skills SET effect_json = $$
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
$$::jsonb WHERE name = '速乘其利';

COMMIT;

SELECT name, jsonb_array_length(effect_json->'effects') AS effect_count
FROM skills
WHERE name = '速乘其利';
