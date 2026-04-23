# 后端 API

框架：FastAPI，Python，uvicorn 端口 7001

源码位置：`/home/ubuntu/sgz/app.py`，`battle_engine.py`，`damage_engine.py`

---

## 接口列表

### POST /api/simulate

模拟对战，支持单次和批量。

**请求体：**

```json
{
  "team_a": [ <GeneralInput>, ... ],
  "team_b": [ <GeneralInput>, ... ],
  "tech_a": <TechInput>,
  "tech_b": <TechInput>,
  "seed": null,
  "runs": 1
}
```

**GeneralInput：**

```json
{
  "name": "关羽",
  "force": 97,
  "intel": 75,
  "command": 96,
  "speed": 74,
  "troops": 10000,
  "troop_type": "cavalry",   // cavalry/bow/spear/shield/machine
  "troop_grade": "S",        // S/A/B/C
  "skills": [ <SkillInput>, ... ]
}
```

**SkillInput：**

```json
{
  "name": "武圣",
  "skill_type": "主动",        // 主动/突击/指挥/被动/兵种/阵法
  "activation_rate": 0.35,
  "damage_rate": 1.8,
  "damage_type": "兵刃",       // 兵刃/谋略
  "target_mode": "single_enemy", // single_enemy/all_enemy/self/all_ally/single_ally
  "heal_rate": 0.0,
  "heal_target": "self",
  "apply_status": null,
  "status_duration": 2,
  "status_chance": 1.0,
  "ignore_defense": false,
  "guaranteed_hit": false
}
```

**TechInput：**

```json
{
  "tech_attack": 10,    // 0-20
  "tech_intel": 10,     // 0-20
  "tech_defense": 10,   // 0-20
  "jiugong": 0,         // 九宫图 0-5
  "bagua": 0            // 八卦阵 0-5
}
```

**单次返回（runs=1）：**

```json
{
  "winner": "A",           // A/B/draw
  "engagements": 3,
  "team_a": [ { "name": "关羽", "troops_left": 5230, "loss_pct": 0.477, "alive": true } ],
  "team_b": [ ... ],
  "log": [
    { "engagement": 1, "round": 1, "actor": "关羽", "action": "武圣", "value": 1234, "target": "吕布" }
  ]
}
```

**批量返回（runs>1）：**

```json
{
  "runs": 100,
  "win_rate_a": 0.42,
  "win_rate_b": 0.55,
  "draw_rate": 0.03,
  "wins_a": 42,
  "wins_b": 55,
  "draws": 3,
  "avg_engagements": 4.21
}
```

### GET /api/health

```json
{ "status": "ok", "version": "0.1" }
```

---

## 战斗引擎关键逻辑

- 最多 **10 局**（engagements），每局最多 **8 回合**
- 胜负判定：主将兵力 ≤ 0 则该方失败
- 普攻伤害基于武力/统率，谋略伤害基于智力
- 科技加成：`tech_attack/intel/defense` 各加 5% 伤害/防御（每级 0.5%）
- 兵种适性 S/A/B/C 对应 1.1/1.0/0.9/0.8 倍系数
