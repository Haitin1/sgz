"""
三国志战略版战斗模拟器 - FastAPI 后端
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional
import os
import psycopg2
import psycopg2.extras

from battle_engine import (
    BattleEngine, GeneralConfig, SkillDef, TechConfig
)

app = FastAPI(title="三国志战略版战斗模拟器", version="0.1")

# ─────────────────────────────────────────────────────────────
# 请求 / 响应模型
# ─────────────────────────────────────────────────────────────

class SkillInput(BaseModel):
    name: str
    skill_type: str = "主动"          # 主动/突击/指挥/被动/兵种/阵法
    activation_rate: float = 0.35
    requires_prep: bool = False
    damage_rate: float = 0.0
    damage_type: str = "兵刃"
    target_mode: str = "single_enemy"
    heal_rate: float = 0.0
    heal_target: str = "self"
    stat_bonus: dict = Field(default_factory=dict)
    stat_pct_bonus: dict = Field(default_factory=dict)
    apply_status: Optional[str] = None
    status_duration: int = 2
    status_chance: float = 1.0
    ignore_defense: bool = False
    guaranteed_hit: bool = False


class GeneralInput(BaseModel):
    name: str
    force: int = 80
    intel: int = 80
    command: int = 80
    speed: int = 80
    troops: int = 10000
    troop_type: str = "cavalry"       # cavalry/bow/spear/shield/machine
    troop_grade: str = "A"            # S/A/B/C
    skills: list[SkillInput] = Field(default_factory=list)


class TechInput(BaseModel):
    tech_attack: int = 10
    tech_intel: int = 10
    tech_defense: int = 10
    jiugong: int = 0
    bagua: int = 0


class SimulateRequest(BaseModel):
    team_a: list[GeneralInput]
    team_b: list[GeneralInput]
    tech_a: TechInput = Field(default_factory=TechInput)
    tech_b: TechInput = Field(default_factory=TechInput)
    seed: Optional[int] = None
    runs: int = Field(default=1, ge=1, le=500)  # 模拟次数（多次取胜率）


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def convert_team(generals: list[GeneralInput]) -> list[GeneralConfig]:
    result = []
    for g in generals:
        skills = [
            SkillDef(
                name=s.name,
                skill_type=s.skill_type,
                activation_rate=s.activation_rate,
                requires_prep=s.requires_prep,
                damage_rate=s.damage_rate,
                damage_type=s.damage_type,
                target_mode=s.target_mode,
                heal_rate=s.heal_rate,
                heal_target=s.heal_target,
                stat_bonus=s.stat_bonus,
                stat_pct_bonus=s.stat_pct_bonus,
                apply_status=s.apply_status,
                status_duration=s.status_duration,
                status_chance=s.status_chance,
                ignore_defense=s.ignore_defense,
                guaranteed_hit=s.guaranteed_hit,
            )
            for s in g.skills
        ]
        result.append(GeneralConfig(
            name=g.name,
            force=g.force,
            intel=g.intel,
            command=g.command,
            speed=g.speed,
            troops=g.troops,
            troop_type=g.troop_type,
            troop_grade=g.troop_grade,
            skills=skills,
        ))
    return result


def convert_tech(t: TechInput) -> TechConfig:
    return TechConfig(
        tech_attack=t.tech_attack,
        tech_intel=t.tech_intel,
        tech_defense=t.tech_defense,
        jiugong=t.jiugong,
        bagua=t.bagua,
    )


# ─────────────────────────────────────────────────────────────
# API 端点
# ─────────────────────────────────────────────────────────────

@app.post("/api/simulate")
def simulate(req: SimulateRequest):
    if not req.team_a or not req.team_b:
        raise HTTPException(400, "双方队伍不能为空")
    if len(req.team_a) > 3 or len(req.team_b) > 3:
        raise HTTPException(400, "每队最多3名武将")

    team_a = convert_team(req.team_a)
    team_b = convert_team(req.team_b)
    tech_a = convert_tech(req.tech_a)
    tech_b = convert_tech(req.tech_b)

    if req.runs == 1:
        # 单次模拟，返回详细战报
        engine = BattleEngine(team_a, team_b, tech_a, tech_b, seed=req.seed)
        result = engine.run()
        return result
    else:
        # 多次模拟，返回胜率统计
        wins = {"A": 0, "B": 0, "draw": 0}
        total_engagements = 0
        for i in range(req.runs):
            seed = (req.seed + i) if req.seed is not None else None
            engine = BattleEngine(team_a, team_b, tech_a, tech_b, seed=seed)
            result = engine.run()
            wins[result["winner"]] += 1
            total_engagements += result["engagements"]

        return {
            "runs": req.runs,
            "win_rate_a": round(wins["A"] / req.runs, 3),
            "win_rate_b": round(wins["B"] / req.runs, 3),
            "draw_rate":  round(wins["draw"] / req.runs, 3),
            "wins_a": wins["A"],
            "wins_b": wins["B"],
            "draws":  wins["draw"],
            "avg_engagements": round(total_engagements / req.runs, 2),
        }


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1"}


# ─────────────────────────────────────────────────────────────
# 数据库工具
# ─────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="sgz", user="sgz", password="sgz2026"
    )


# ─────────────────────────────────────────────────────────────
# 军师技接口
# ─────────────────────────────────────────────────────────────

@app.get("/api/generals")
def list_generals():
    """返回所有武将基础信息（含成长值）"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, name, faction,
                   strength, intelligence, leadership, speed, politics, charisma,
                   str_growth, int_growth, lead_growth, spd_growth, pol_growth, cha_growth,
                   cavalry, bow, spear, shield, machine,
                   innate_skill, role
            FROM generals
            ORDER BY name
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


class GeneralUpdateInput(BaseModel):
    strength:     float
    intelligence: float
    leadership:   float
    speed:        float
    politics:     float = 0
    charisma:     float = 0
    str_growth:   float
    int_growth:   float
    lead_growth:  float
    spd_growth:   float
    pol_growth:   float = 0
    cha_growth:   float = 0


@app.put("/api/generals/{general_id}")
def update_general(general_id: int, body: GeneralUpdateInput):
    """更新武将基础属性和成长值"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE generals
            SET strength=%(strength)s, intelligence=%(intelligence)s,
                leadership=%(leadership)s, speed=%(speed)s,
                politics=%(politics)s, charisma=%(charisma)s,
                str_growth=%(str_growth)s, int_growth=%(int_growth)s,
                lead_growth=%(lead_growth)s, spd_growth=%(spd_growth)s,
                pol_growth=%(pol_growth)s, cha_growth=%(cha_growth)s
            WHERE id=%(id)s
        """, {**body.dict(), "id": general_id})
        if cur.rowcount == 0:
            raise HTTPException(404, "武将不存在")
        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


@app.get("/api/advisor_skills")
def list_advisor_skills():
    """返回所有已实装的军师技列表"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, general_name, skill_name, attribute,
                   troop_restrict, cost, target, is_passive,
                   daily_limit, description, effect_json
            FROM advisor_skills
            WHERE is_implemented = TRUE
            ORDER BY attribute NULLS LAST, id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


# ─────────────────────────────────────────────────────────────
# 前端静态文件
# ─────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>前端文件未找到，请检查 static/index.html</h1>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8765, reload=True)
