"""
三国志战略版战斗模拟器 - FastAPI 后端
"""

from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
import base64
import requests as _requests
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, EmailStr
from typing import Optional
import os
import psycopg2
import psycopg2.extras
import secrets
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import JWTError, jwt

from battle_engine import (
    BattleEngine, GeneralConfig, SkillDef, TechConfig
)

app = FastAPI(title="三国志战略版战斗模拟器", version="0.1")

# ─────────────────────────────────────────────────────────────
# 认证配置
# ─────────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
JWT_SECRET    = os.getenv("JWT_SECRET", "sgz-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

SMTP_HOST     = "smtp.ionos.co.uk"
SMTP_PORT     = 465
SMTP_USER     = "sgzadmin@sjwx.co.uk"
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SITE_URL      = os.getenv("SITE_URL", "http://132.145.24.63:8765")


def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)

def create_token(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> int:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    return int(payload["sub"])

def get_current_user_id(authorization: Optional[str] = Header(None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        return decode_token(authorization.split(" ", 1)[1])
    except JWTError:
        raise HTTPException(401, "Token 无效或已过期")

def send_reset_email(to_email: str, reset_link: str):
    msg = MIMEText(
        f"您好，\n\n请点击以下链接重置密码（24小时内有效）：\n\n{reset_link}\n\n如非本人操作，请忽略此邮件。",
        "plain", "utf-8"
    )
    msg["Subject"] = "三国志战略版 · 密码重置"
    msg["From"]    = SMTP_USER
    msg["To"]      = to_email
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as srv:
        srv.login(SMTP_USER, SMTP_PASSWORD)
        srv.send_message(msg)


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
# 用户认证
# ─────────────────────────────────────────────────────────────

class RegisterInput(BaseModel):
    username:      str
    email:         str
    password:      str
    server_name:   Optional[str] = None
    alliance_name: Optional[str] = None

class LoginInput(BaseModel):
    username: str
    password: str

class ProfileUpdateInput(BaseModel):
    server_name:   Optional[str] = None
    alliance_name: Optional[str] = None

class ForgotPasswordInput(BaseModel):
    email: str

class ResetPasswordInput(BaseModel):
    token:    str
    password: str


@app.post("/api/auth/register")
def register(body: RegisterInput):
    if len(body.username) < 2:
        raise HTTPException(400, "用户名至少2个字符")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少6位")
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, email, password_hash, server_name, alliance_name) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (body.username, body.email, hash_password(body.password),
             body.server_name, body.alliance_name)
        )
        user_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return {"token": create_token(user_id), "user_id": user_id, "username": body.username}
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "用户名或邮箱已被注册")
    except Exception as e:
        raise HTTPException(500, f"注册失败：{e}")


@app.post("/api/auth/login")
def login(body: LoginInput):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE username=%s", (body.username,))
        user = cur.fetchone(); cur.close(); conn.close()
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "用户名或密码错误")
        return {
            "token": create_token(user["id"]),
            "user_id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "server_name": user["server_name"],
            "alliance_name": user["alliance_name"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"登录失败：{e}")


@app.get("/api/auth/me")
def me(uid: int = Depends(get_current_user_id)):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, username, email, server_name, alliance_name, created_at FROM users WHERE id=%s",
            (uid,)
        )
        user = cur.fetchone(); cur.close(); conn.close()
        if not user:
            raise HTTPException(404, "用户不存在")
        return dict(user)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.put("/api/auth/profile")
def update_profile(body: ProfileUpdateInput, uid: int = Depends(get_current_user_id)):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE users SET server_name=%s, alliance_name=%s WHERE id=%s",
            (body.server_name, body.alliance_name, uid)
        )
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordInput):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE email=%s", (body.email,))
        row = cur.fetchone()
        if row:
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(hours=24)
            cur.execute(
                "UPDATE users SET reset_token=%s, reset_token_expires=%s WHERE id=%s",
                (token, expires, row[0])
            )
            conn.commit()
            link = f"{SITE_URL}/?reset_token={token}"
            try:
                send_reset_email(body.email, link)
            except Exception:
                pass  # 邮件失败不影响接口响应
        cur.close(); conn.close()
        return {"ok": True, "msg": "如果该邮箱已注册，重置邮件已发送"}
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordInput):
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少6位")
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT id, reset_token_expires FROM users WHERE reset_token=%s",
            (body.token,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "重置链接无效")
        if row[1] < datetime.now(timezone.utc):
            raise HTTPException(400, "重置链接已过期")
        cur.execute(
            "UPDATE users SET password_hash=%s, reset_token=NULL, reset_token_expires=NULL WHERE id=%s",
            (hash_password(body.password), row[0])
        )
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


# ─────────────────────────────────────────────────────────────
# 用户装备
# ─────────────────────────────────────────────────────────────

class EquipmentInput(BaseModel):
    name:           str
    eq_type:        str = "武器"
    owner_general:  Optional[str] = None
    force_bonus:    float = 0
    intel_bonus:    float = 0
    command_bonus:  float = 0
    speed_bonus:    float = 0
    politics_bonus: float = 0
    charisma_bonus: float = 0
    skill1:         Optional[str] = None
    skill2:         Optional[str] = None
    notes:          Optional[str] = None


@app.get("/api/user/equipment")
def list_equipment(uid: int = Depends(get_current_user_id)):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM user_equipment WHERE user_id=%s ORDER BY id", (uid,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.post("/api/user/equipment")
def add_equipment(body: EquipmentInput, uid: int = Depends(get_current_user_id)):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_equipment
              (user_id, name, eq_type, owner_general,
               force_bonus, intel_bonus, command_bonus, speed_bonus,
               politics_bonus, charisma_bonus, skill1, skill2, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (uid, body.name, body.eq_type, body.owner_general,
              body.force_bonus, body.intel_bonus, body.command_bonus, body.speed_bonus,
              body.politics_bonus, body.charisma_bonus, body.skill1, body.skill2, body.notes))
        new_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return {"ok": True, "id": new_id}
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.put("/api/user/equipment/{eq_id}")
def update_equipment(eq_id: int, body: EquipmentInput, uid: int = Depends(get_current_user_id)):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            UPDATE user_equipment SET
              name=%s, eq_type=%s, owner_general=%s,
              force_bonus=%s, intel_bonus=%s, command_bonus=%s, speed_bonus=%s,
              politics_bonus=%s, charisma_bonus=%s, skill1=%s, skill2=%s, notes=%s
            WHERE id=%s AND user_id=%s
        """, (body.name, body.eq_type, body.owner_general,
              body.force_bonus, body.intel_bonus, body.command_bonus, body.speed_bonus,
              body.politics_bonus, body.charisma_bonus, body.skill1, body.skill2, body.notes,
              eq_id, uid))
        if cur.rowcount == 0:
            raise HTTPException(404, "装备不存在")
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


class OwnerUpdate(BaseModel):
    owner_general: str = "无"

@app.patch("/api/user/equipment/{eq_id}/owner")
def update_equipment_owner(eq_id: int, body: OwnerUpdate, uid: int = Depends(get_current_user_id)):
    try:
        owner = body.owner_general
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE user_equipment SET owner_general=%s WHERE id=%s AND user_id=%s",
                    (owner, eq_id, uid))
        if cur.rowcount == 0:
            raise HTTPException(404, "装备不存在")
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.delete("/api/user/equipment/{eq_id}")
def delete_equipment(eq_id: int, uid: int = Depends(get_current_user_id)):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM user_equipment WHERE id=%s AND user_id=%s", (eq_id, uid))
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


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
                   innate_skill, role, command_value
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


@app.get("/api/skills")
def list_skills():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, name, category, trigger_prob, target, description, quality, effect
            FROM skills
            ORDER BY category, name
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]
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



@app.get("/api/military_books")
def list_military_books():
    """返回所有兵书数据"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, name, book_system, book_type, description
            FROM military_books
            ORDER BY book_system, book_type DESC, id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


@app.get("/api/equipment_skills")
def list_equipment_skills():
    """返回所有装备特技（name -> description 映射）"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT name, description FROM equipment_skills")
        rows = cur.fetchall(); cur.close(); conn.close()
        return {r["name"]: r["description"] for r in rows}
    except Exception as e:
        raise HTTPException(500, f"错误：{e}")


@app.get("/api/affinities")
def list_affinities():
    """返回所有缘分数据"""
    import json as _json
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, name, min_members, members, effect
            FROM affinities
            ORDER BY id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            # members 是 text 列存的 JSON 字符串，手动解析成数组
            if isinstance(d.get('members'), str):
                try:
                    d['members'] = _json.loads(d['members'])
                except Exception:
                    d['members'] = []
            result.append(d)
        return result
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


# ─────────────────────────────────────────────────────────────
# 阵容存储
# ─────────────────────────────────────────────────────────────

import json as _json

class LineupSaveInput(BaseModel):
    name: str = ""
    data: dict = Field(default_factory=dict)


@app.get("/api/lineups/{user_id}")
def get_lineups(user_id: str):
    """返回该用户的所有阵容，格式 {a: [5槽], b: [5槽]}"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT team, slot, name, data FROM lineups WHERE user_id=%s",
            (user_id,)
        )
        rows = cur.fetchall()
        cur.close(); conn.close()
        result = {"a": [None]*5, "b": [None]*5}
        for r in rows:
            s = r["slot"]
            if s < 5:
                entry = {"name": r["name"], "data": r["data"]}
                result[r["team"]][s] = entry
        return result
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


@app.put("/api/lineups/{user_id}/{team}/{slot}")
def save_lineup(user_id: str, team: str, slot: int, body: LineupSaveInput):
    """保存/覆盖某槽阵容"""
    if team not in ("a", "b") or slot not in range(5):
        raise HTTPException(400, "参数错误")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO lineups (user_id, team, slot, name, data, saved_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, team, slot)
            DO UPDATE SET name=EXCLUDED.name, data=EXCLUDED.data, saved_at=NOW()
        """, (user_id, team, slot, body.name, _json.dumps(body.data)))
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


@app.delete("/api/lineups/{user_id}/{team}/{slot}")
def delete_lineup(user_id: str, team: str, slot: int):
    """删除某槽阵容"""
    if team not in ("a", "b") or slot not in range(5):
        raise HTTPException(400, "参数错误")
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM lineups WHERE user_id=%s AND team=%s AND slot=%s",
            (user_id, team, slot)
        )
        conn.commit(); cur.close(); conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


# ─────────────────────────────────────────────────────────────
# 赛季剧本 & 推荐阵容
# ─────────────────────────────────────────────────────────────

@app.get("/api/scenarios")
def get_scenarios():
    """返回所有赛季剧本，按 display_order 排序"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, is_current FROM scenarios ORDER BY display_order DESC")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


@app.get("/api/recommended_lineups")
def get_recommended_lineups(scenario_id: int):
    """返回指定赛季的推荐阵容列表"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, name, faction, troop_type, generals, notes FROM recommended_lineups WHERE scenario_id=%s ORDER BY id",
            (scenario_id,)
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception as e:
        raise HTTPException(500, f"数据库错误：{e}")


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# OCR 识别
# ─────────────────────────────────────────────────────────────

OCR_TOKEN = os.environ.get("BAIDU_OCR_TOKEN", "")
OCR_SYNC_URL = "https://paddleocr.aistudio-app.com/layout-parsing"
OCR_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
OCR_MODEL = "PaddleOCR-VL-1.5"
_OCR_DEBUG_FILE = "/tmp/sgz_ocr_debug.txt"

import re as _re
import asyncio as _asyncio
import base64 as _base64
import json as _json_ocr
import time as _time


def _sse(data: dict) -> str:
    return f"data: {_json_ocr.dumps(data, ensure_ascii=False)}\n\n"


async def _ocr_tesseract(image_bytes: bytes) -> list[str]:
    """本地 Tesseract OCR 兜底"""
    import pytesseract
    from PIL import Image
    import io
    loop = _asyncio.get_event_loop()
    def _do_ocr():
        img = Image.open(io.BytesIO(image_bytes))
        # 用中文+英文识别
        text = pytesseract.image_to_string(img, lang='chi_sim+eng', config='--psm 6')
        return text
    text = await loop.run_in_executor(None, _do_ocr)
    if text.strip():
        return [text]
    return []


async def _submit_vl_job(image_bytes: bytes) -> str:
    headers = {"Authorization": f"bearer {OCR_TOKEN}"}
    loop = _asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: _requests.post(
        OCR_JOB_URL, headers=headers,
        data={"model": OCR_MODEL, "optionalPayload": _json_ocr.dumps({
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useLayoutDetection": False,
            "useChartRecognition": False,
            "useOcrForImageBlock": True,
        })},
        files={"file": ("image.jpg", image_bytes, "image/jpeg")},
        timeout=20,
    ))
    if resp.status_code != 200:
        raise RuntimeError(f"提交失败(HTTP {resp.status_code}): {resp.text[:300]}")
    rj = resp.json()
    if rj.get("code", 0) != 0:
        raise RuntimeError(f"OCR API错误(code={rj['code']}): {rj.get('msg', '')} | {resp.text[:200]}")
    return rj["data"]["jobId"]


async def _poll_vl_job(job_id: str) -> dict:
    headers = {"Authorization": f"bearer {OCR_TOKEN}"}
    loop = _asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        None, lambda: _requests.get(f"{OCR_JOB_URL}/{job_id}", headers=headers, timeout=10)
    )
    return resp.json()["data"]


async def _download_vl_result(jsonl_url: str) -> list[str]:
    """下载 JSONL 结果，返回所有 markdown/HTML 文本行"""
    loop = _asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: _requests.get(jsonl_url, timeout=15))
    # 先写调试日志（原始数据）
    try:
        with open(_OCR_DEBUG_FILE, "w") as f:
            f.write(f"JSONL URL: {jsonl_url}\n")
            f.write(f"HTTP status: {raw.status_code}\n")
            f.write(f"Raw response (first 5000 chars):\n{raw.text[:5000]}\n\n")
    except Exception:
        pass
    lines = []
    for raw_line in raw.text.strip().split("\n"):
        if not raw_line.strip():
            continue
        try:
            obj = _json_ocr.loads(raw_line)
        except Exception:
            continue
        res_obj = obj.get("result", obj)

        for res in res_obj.get("layoutParsingResults", []):
            md = res.get("markdown", {}).get("text", "")
            if md.strip():
                lines.append(md)
    # 追加解析结果
    try:
        with open(_OCR_DEBUG_FILE, "a") as f:
            f.write(f"Parsed lines: {len(lines)}\n")
            for i, l in enumerate(lines):
                f.write(f"--- line[{i}] ---\n{l[:1000]}\n")
    except Exception:
        pass
    return lines


_STAT_MAP = {
    "武力": "force_bonus", "智力": "intel_bonus", "统率": "command_bonus",
    "速度": "speed_bonus", "政治": "politics_bonus", "魅力": "charisma_bonus",
}
_STAT_RE = _re.compile(r"(武力|智力|统率|速度|政治|魅力)[+＋](\d+\.?\d*)")
_TYPE_KW = {"武器", "防具", "坐骑", "宝物"}
_SKIP_NAME = _re.compile(r"^[-—\s\|装备名称类型持有者武将属性技能特技]+$")


def _parse_stats(text: str) -> dict:
    stats = {}
    for m in _STAT_RE.finditer(text):
        field = _STAT_MAP.get(m.group(1))
        if field:
            stats[field] = float(m.group(2))
    return stats


def _fuzzy_match(word: str, candidates: list[str], threshold: int = 70) -> str | None:
    import difflib
    word = word.strip()
    if not word:
        return None
    for c in candidates:
        if word == c or word in c or c in word:
            return c
    best, best_score = None, 0
    ws = set(word)
    for c in candidates:
        cs = set(c)
        if not ws or not cs:
            continue
        # 字符集交叉比
        char_score = len(ws & cs) / max(len(ws), len(cs)) * 100
        # 序列相似比（对 OCR 同位置误读更友好，如 姬/姫）
        seq_score  = difflib.SequenceMatcher(None, word, c).ratio() * 100
        score = max(char_score, seq_score)
        if score >= threshold and score > best_score:
            best, best_score = c, score
    return best


_TR_PAT = _re.compile(r'<tr[^>]*>(.*?)</tr>', _re.DOTALL | _re.IGNORECASE)
_TD_PAT = _re.compile(r'<td[^>]*>(.*?)</td>', _re.DOTALL | _re.IGNORECASE)


def _parse_equip_items(lines: list[str], eq_skills: dict, eq_names: dict) -> list[dict]:
    """从 OCR 输出（HTML table 或 markdown）中解析装备列表"""
    full_text = "\n".join(lines)
    skill_names = list(eq_skills.keys())
    equip_names = list(eq_names.keys())
    items: list[dict] = []
    seen: set[str] = set()  # 去重: "name|type|stats_key"

    def _dedup_key(name, eq_type, stats):
        sk = ",".join(f"{k}:{v}" for k, v in sorted(stats.items()))
        return f"{name}|{eq_type}|{sk}"

    def _add_item(name, eq_type, stats, skills):
        if not name or not stats:
            return
        # 模糊匹配装备名
        matched = _fuzzy_match(name, equip_names)
        name = matched if matched else name
        key = _dedup_key(name, eq_type, stats)
        if key in seen:
            return
        seen.add(key)
        items.append({
            "equip_name": name,
            "equip_type": eq_type or "武器",
            "skills": skills,
            "stats": stats,
        })

    # ── 优先：HTML <table> 解析 ──
    if "<tr" in full_text.lower():
        last_type = None
        for tr_m in _TR_PAT.finditer(full_text):
            tds = [m.group(1).strip() for m in _TD_PAT.finditer(tr_m.group(1))]
            if len(tds) < 2:
                continue

            # 跳过表头行和空行
            if any(h in "".join(tds) for h in ("装备名称", "类型", "持有")):
                continue

            name = tds[0]
            if not name or _re.match(r'^[-—\s]+$', name):
                continue

            # 灵活解析: 支持 2~5 列的各种格式
            eq_type = ""
            stats_text = ""
            skill_text = ""

            if len(tds) >= 4:
                # 标准格式: 名称 | 类型 | 持有者 | 属性 | 技能
                eq_type = tds[1] if tds[1] in _TYPE_KW else ""
                stats_text = tds[3]
                skill_text = tds[4].strip() if len(tds) > 4 else ""
            elif len(tds) == 3:
                # OCR 常见: 名称 | 类型(可能空) | 属性
                if tds[1] in _TYPE_KW:
                    eq_type = tds[1]
                    last_type = eq_type
                stats_text = tds[2]
            elif len(tds) == 2:
                # 最简: 名称 | 属性
                stats_text = tds[1]

            # 用上一次的类型填充空缺
            if not eq_type and last_type:
                eq_type = last_type

            # 如果类型列有值，更新 last_type
            if not eq_type:
                # 尝试从 eq_names 已知数据推断
                eq_type = eq_names.get(name, "")

            stats = _parse_stats(stats_text)
            if not stats:
                continue

            skills = []
            if skill_text:
                ms = _fuzzy_match(skill_text, skill_names)
                skill_name = ms if ms else skill_text
                skills.append({"name": skill_name, "desc": eq_skills.get(skill_name, "")})

            _add_item(name, eq_type, stats, skills)

        if items:
            return items

    # ── 优先：行分隔OCR格式（每\n\n一件装备，字段用——分隔）──
    # format: "名称 类型—— 属性1 属性2 技能"
    if '—' in full_text or '\n\n' in full_text:
        candidate_lines = [l.strip() for l in _re.split(r'\n{2,}', full_text) if l.strip()]
        for cline in candidate_lines:
            # 把 —— 替换为空格，方便分词
            cline_clean = _re.sub(r'[—－]+', ' ', cline)
            parts = cline_clean.split()
            if not parts:
                continue
            # 找类型关键词位置
            type_idx = next((i for i, p in enumerate(parts) if p in _TYPE_KW), None)
            if type_idx is None:
                continue
            name_raw = ' '.join(parts[:type_idx])
            eq_type = parts[type_idx]
            rest = parts[type_idx + 1:]
            stats: dict = {}
            skills: list = []
            for r in rest:
                s = _parse_stats(r)
                if s:
                    stats.update(s)
                else:
                    ms = _fuzzy_match(r, skill_names)
                    if ms:
                        skills.append({"name": ms, "desc": eq_skills.get(ms, "")})
            if not stats:
                continue
            matched = _fuzzy_match(name_raw, equip_names)
            name = matched if matched else name_raw
            _add_item(name, eq_type, stats, skills)
        if items:
            return items

    # ── 降级：markdown | 表格解析 ──
    for line in lines:
        if "|" not in line:
            continue
        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < 2:
            continue
        name = cols[0]
        if not name or _re.match(r'^[-—\s]+$', name):
            continue
        if any(h in name for h in ("装备名称", "类型")):
            continue
        eq_type = cols[1] if len(cols) > 1 and cols[1] in _TYPE_KW else eq_names.get(name, "")
        stats_text = cols[3] if len(cols) > 3 else (cols[2] if len(cols) > 2 else "")
        stats = _parse_stats(stats_text)
        if not stats:
            continue
        skill_text = cols[4].strip() if len(cols) > 4 else ""
        skills = []
        if skill_text:
            ms = _fuzzy_match(skill_text, skill_names)
            skill_name = ms if ms else skill_text
            skills.append({"name": skill_name, "desc": eq_skills.get(skill_name, "")})
        _add_item(name, eq_type, stats, skills)
    if items:
        return items

    # ── 降级：逐 token 解析 ──
    cur: dict | None = None

    def flush():
        if cur and cur.get("equip_name") and cur.get("stats"):
            _add_item(cur["equip_name"], cur.get("equip_type", ""), cur["stats"], cur.get("skills", []))

    for line in lines:
        cleaned = _re.sub(r"[#*`<>|\\]+", " ", line)
        for token in cleaned.split():
            token = token.strip()
            if not token:
                continue
            matched_eq = _fuzzy_match(token, equip_names)
            if matched_eq:
                flush()
                cur = {"equip_name": matched_eq,
                       "equip_type": eq_names.get(matched_eq),
                       "skills": [], "stats": {}}
                continue
            if cur is None:
                cur = {"equip_name": None, "equip_type": None, "skills": [], "stats": {}}
            if token in _TYPE_KW:
                cur["equip_type"] = token
                continue
            ms = _fuzzy_match(token, skill_names)
            if ms and ms not in [s["name"] for s in cur["skills"]]:
                cur["skills"].append({"name": ms, "desc": eq_skills[ms]})
                continue
            cur["stats"].update(_parse_stats(token))

    flush()

    # ── 最终降级: 对整段纯文本做行级解析 ──
    if not items:
        full = "\n".join(lines)
        # 尝试从纯文本中提取 "名称 类型 属性 技能" 模式
        # 匹配: 武器/防具/坐骑/宝物 前后的装备名 + 属性
        text_lines = _re.split(r'[\n\r]+', full)
        for tl in text_lines:
            tl = tl.strip()
            if not tl:
                continue
            # 跳过表头
            if _re.match(r'^[-—\s|装备名称类型持有武将属性技能特技]+$', tl):
                continue
            # 找属性
            stats = _parse_stats(tl)
            if not stats:
                continue
            # 找类型
            eq_type = None
            for kw in _TYPE_KW:
                if kw in tl:
                    eq_type = kw
                    break
            if not eq_type:
                continue
            # 装备名: 类型前面的文字
            type_pos = tl.index(eq_type)
            name_part = tl[:type_pos].strip()
            # 清理多余符号
            name_part = _re.sub(r'[|,\-\s]+$', '', name_part).strip()
            if not name_part:
                continue
            # 模糊匹配已知名字
            matched = _fuzzy_match(name_part, equip_names)
            eq_name = matched if matched else name_part
            # 找技能
            skills = []
            after_type = tl[type_pos + len(eq_type):]
            for sn in skill_names:
                if sn in after_type:
                    skills.append({"name": sn, "desc": eq_skills.get(sn, "")})
            items.append({
                "equip_name": eq_name,
                "equip_type": eq_type,
                "skills": skills,
                "stats": stats,
            })

    return items


@app.post("/api/ocr/equipment")
async def ocr_equipment(file: UploadFile = File(...)):
    image_bytes = await file.read()

    # 预加载 DB 数据
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT name, description FROM equipment_skills")
    eq_skills = {r["name"]: r["description"] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT name, eq_type FROM user_equipment LIMIT 1000")
    eq_names = {r["name"]: r["eq_type"] for r in cur.fetchall()}
    cur.close(); conn.close()

    async def stream():
        try:
            # 异步 API (jobs)
            yield _sse({"progress": 5, "msg": "提交识别任务…"})
            job_id = await _submit_vl_job(image_bytes)
            yield _sse({"progress": 12, "msg": f"任务已提交: {job_id}"})

            deadline = _time.time() + 90
            jsonl_url = None
            last_state = ""
            while _time.time() < deadline:
                await _asyncio.sleep(3)
                data = await _poll_vl_job(job_id)
                state = data["state"]
                last_state = state

                if state == "pending":
                    elapsed = _time.time() - (deadline - 90)
                    p = min(30, 12 + elapsed * 1.5)
                    yield _sse({"progress": round(p, 1), "msg": "队列等待中…"})

                elif state == "running":
                    try:
                        total = data["extractProgress"]["totalPages"]
                        done_pages = data["extractProgress"]["extractedPages"]
                        p = 30 + (done_pages / max(total, 1)) * 55
                    except Exception:
                        elapsed = _time.time() - (deadline - 90)
                        p = min(80, 30 + elapsed * 1.5)
                    yield _sse({"progress": round(p, 1), "msg": "识别中…"})

                elif state == "done":
                    jsonl_url = data["resultUrl"]["jsonUrl"]
                    yield _sse({"progress": 90, "msg": "下载结果…"})
                    break

                elif state == "failed":
                    err_msg = data.get("errorMsg", "识别失败")
                    yield _sse({"error": f"OCR任务失败: {err_msg}"})
                    return

            if not jsonl_url:
                yield _sse({"error": f"识别超时（90秒），最后状态: {last_state}"})
                return

            lines = await _download_vl_result(jsonl_url)
            items = _parse_equip_items(lines, eq_skills, eq_names)
            for item in items:
                item["_checked"] = True

            yield _sse({"progress": 100, "msg": "完成", "result": {"items": items, "raw": lines}})

        except Exception as e:
            yield _sse({"error": str(e)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/ocr/debug")
async def ocr_debug():
    """查看 OCR 调试日志 + token 状态"""
    import os
    has_token = bool(OCR_TOKEN.strip())
    token_info = f"{OCR_TOKEN[:8]}...(len={len(OCR_TOKEN)})" if has_token else "(空! 请设置 BAIDU_OCR_TOKEN 环境变量)"
    debug_log = ""
    if os.path.exists(_OCR_DEBUG_FILE):
        with open(_OCR_DEBUG_FILE, "r") as f:
            debug_log = f.read()
    return JSONResponse({
        "token": token_info,
        "has_token": has_token,
        "sync_url": OCR_SYNC_URL,
        "async_url": OCR_JOB_URL,
        "model": OCR_MODEL,
        "log": debug_log or "(暂无日志，请先执行一次OCR识别)"
    })


def _extract_text_tokens(html: str) -> list[str]:
    """从 HTML/markdown 中提取所有可见文字 token"""
    # 取出所有 td/th 内容优先，再 fallback 到去标签纯文本
    tokens = []
    for m in _TD_PAT.finditer(html):
        txt = m.group(1).strip()
        if txt:
            tokens.extend(txt.split())
    if not tokens:
        clean = _re.sub(r"<[^>]+>", " ", html)
        clean = _re.sub(r"[#*`|\\]", " ", clean)
        tokens = clean.split()
    return tokens


_LEVEL_RE = _re.compile(r'^(\d{1,2})\s*(.{2,6})$')   # "43孙尚香" / "43 孙尚香"

def _parse_lineup_from_html(
    full_text: str,
    generals_db: list[dict],
    skills_db: list[dict],
    books_db: list[dict] | None = None,
) -> dict:
    """从 VL-1.5 返回的文本中解析阵容（武将 + 技能 + 兵书 + 等级）

    OCR 读取多列截图时有一个规律：
    - 某将的「主兵书」往往出现在「下一位武将名」之后、「下一位武将技能」之前
    - 某将的「副兵书」往往出现在「下一位武将名」之前（当前将领技能之后）
    因此用状态机：刚识别武将名后进入 after_name 状态；
    该状态下若遇到兵书 → 归给上一位将领；遇到技能 → 切为 in_skills 状态。
    """
    gen_names     = [g["name"] for g in generals_db]
    skill_names   = [s["name"] for s in skills_db]
    book_names    = [b["name"] for b in (books_db or [])]
    # 字数规则：4字=主兵书，2字=副兵书（游戏固定规则，优先于 DB 分类）
    book_type_map = {b["name"]: ("主兵书" if len(b["name"]) == 4 else "副兵书")
                     for b in (books_db or [])}

    tokens = _extract_text_tokens(full_text)

    generals_out: list[dict] = []
    cur_gen:  dict | None = None
    prev_gen: dict | None = None
    pending_level: int | None = None
    # after_name = True  → 刚切换到新将领，尚未看到其任何技能
    after_name = False

    for tok in tokens:
        tok = tok.strip("，。、·：:「」【】()（）\"'")
        if not tok:
            continue

        # ── 纯数字 token ──
        if tok.isdigit():
            n = int(tok)
            if 1 <= n <= 60:
                pending_level = n
            continue

        if len(tok) < 2:
            continue

        # ── "43孙尚香" 拼合 token ──
        m_lv = _LEVEL_RE.match(tok)
        lv_prefix = None
        if m_lv:
            maybe_lv  = int(m_lv.group(1))
            tok_clean = m_lv.group(2)
            if 1 <= maybe_lv <= 60:
                lv_prefix = maybe_lv
                tok = tok_clean.strip()

        # ── 武将名匹配 ──
        gm = _fuzzy_match(tok, gen_names, threshold=75)
        if gm and gm not in [g["name"] for g in generals_out]:
            level = lv_prefix or pending_level or 43
            prev_gen  = cur_gen
            cur_gen   = {"name": gm, "level": level, "skills": [], "books": []}
            after_name = True
            generals_out.append(cur_gen)
            if len(generals_out) > 3:
                generals_out = generals_out[:3]
            pending_level = None
            continue

        pending_level = None
        if cur_gen is None:
            continue

        # ── 兵书匹配（直接匹配） ──
        def _add_book(tgt, bk_name, bk_type):
            if tgt and bk_name not in [x["name"] for x in tgt["books"]]:
                tgt["books"].append({"name": bk_name, "book_type": bk_type})

        bk = _fuzzy_match(tok, book_names, threshold=72) if book_names else None
        if bk:
            # after_name 阶段：主兵书因多列布局被 OCR 扫到"下一将名"之后，归给上一将
            target = prev_gen if (after_name and prev_gen is not None) else cur_gen
            _add_book(target, bk, book_type_map.get(bk, ""))
            continue

        # ── 兵书分割匹配：OCR 可能把相邻两本书合并（如"守势防备"="守势"+"防备"） ──
        if book_names and len(tok) in (4, 6) and all('一' <= c <= '鿿' for c in tok):
            half = len(tok) // 2
            target = prev_gen if (after_name and prev_gen is not None) else cur_gen
            matched_split = False
            for part in (tok[:half], tok[half:]):
                pb = _fuzzy_match(part, book_names, threshold=88)  # 对半截片段要求更严格
                if pb:
                    _add_book(target, pb, book_type_map.get(pb, ""))
                    matched_split = True
            if matched_split:
                continue

        # ── 技能匹配 ──
        sk = _fuzzy_match(tok, skill_names, threshold=65)
        if sk and sk not in [s["name"] for s in cur_gen["skills"]]:
            if len(cur_gen["skills"]) < 3:
                cur_gen["skills"].append({"name": sk})
            after_name = False   # 看到第一个技能，结束 after_name 阶段
            continue

        # ── 技能区域的未匹配占位：
        #    after_name=False（已进入技能区）且技能未满3个时，
        #    4字汉字 token 大概率是误读的技能名，原文存入以便用户识别和修正。
        #    2字及其他长度 token 在此位置多为噪声，忽略。
        is_skill_zone = (not after_name) and (len(cur_gen["skills"]) < 3)
        is_4char_cjk  = len(tok) == 4 and all('一' <= c <= '鿿' for c in tok)
        if is_skill_zone and is_4char_cjk:
            cur_gen["skills"].append({"name": tok, "unmatched": True})
            # 不切换 after_name，继续识别可能紧跟的真实技能

    return {"generals": generals_out}


@app.post("/api/ocr/lineup")
async def ocr_lineup(file: UploadFile = File(...)):
    image_bytes = await file.read()
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT name FROM generals")
    generals_db = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT name FROM skills")
    skills_db = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT name, book_type FROM military_books")
    books_db = [dict(r) for r in cur.fetchall()]
    # 武将兵种适性（用于推断最佳兵种）
    cur.execute("SELECT name, cavalry, bow, spear, shield, machine FROM generals")
    troop_rows = {r["name"]: dict(r) for r in cur.fetchall()}
    cur.close(); conn.close()

    async def stream():
        try:
            yield _sse({"progress": 5, "msg": "提交识别任务…"})
            job_id = await _submit_vl_job(image_bytes)
            yield _sse({"progress": 12, "msg": "任务已提交，等待队列…"})
            deadline = _time.time() + 90
            jsonl_url = None
            while _time.time() < deadline:
                await _asyncio.sleep(3)
                data = await _poll_vl_job(job_id)
                state = data["state"]
                if state == "pending":
                    p = min(30, 12 + (_time.time() - (deadline - 90)) * 1.5)
                    yield _sse({"progress": round(p, 1), "msg": "队列等待中…"})
                elif state == "running":
                    try:
                        total = data["extractProgress"]["totalPages"]
                        done_pages = data["extractProgress"]["extractedPages"]
                        p = 30 + (done_pages / max(total, 1)) * 55
                    except Exception:
                        p = min(80, 30 + (_time.time() - (deadline - 90)) * 1.5)
                    yield _sse({"progress": round(p, 1), "msg": "识别中…"})
                elif state == "done":
                    jsonl_url = data["resultUrl"]["jsonUrl"]
                    yield _sse({"progress": 90, "msg": "下载结果…"})
                    break
                elif state == "failed":
                    yield _sse({"error": data.get("errorMsg", "识别失败")}); return
            if not jsonl_url:
                yield _sse({"error": "识别超时（90秒）"}); return
            lines = await _download_vl_result(jsonl_url)
            joined = "\n".join(lines)
            result = _parse_lineup_from_html(joined, generals_db, skills_db, books_db)
            # 补充兵种适性信息（根据 DB 推断最佳兵种）
            GRADE_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1, "": 0}
            TROOP_KEYS  = [("cavalry","骑兵"), ("bow","弓兵"), ("spear","枪兵"), ("shield","盾兵"), ("machine","器械")]
            for g in result["generals"]:
                tr = troop_rows.get(g["name"], {})
                best_type, best_grade = "cavalry", "C"
                for key, _ in TROOP_KEYS:
                    grade = tr.get(key, "C") or "C"
                    if GRADE_ORDER.get(grade, 0) > GRADE_ORDER.get(best_grade, 0):
                        best_type, best_grade = key, grade
                g["troop_type"]  = best_type
                g["troop_grade"] = best_grade
            result["raw"] = joined[:5000]  # 调试用
            yield _sse({"progress": 100, "msg": "完成", "result": result})
        except Exception as e:
            yield _sse({"error": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
