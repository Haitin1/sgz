"""
三国志战略版战斗模拟器 - FastAPI 后端
"""

from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File
import base64
import requests as _requests
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
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
            entry = {"name": r["name"], "data": r["data"]}
            result[r["team"]][r["slot"]] = entry
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
# ─────────────────────────────────────────────────────────────
# OCR 识别
# ─────────────────────────────────────────────────────────────

OCR_TOKEN = os.environ.get("BAIDU_OCR_TOKEN", "")
OCR_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
OCR_MODEL = "PaddleOCR-VL-1.5"

import re as _re
import asyncio as _asyncio
import json as _json_ocr
import time as _time


async def _call_paddle_vl(image_bytes: bytes) -> list[str]:
    """用 PaddleOCR VL-1.5 异步模型识别，返回纯文字行列表"""
    headers = {"Authorization": f"bearer {OCR_TOKEN}"}
    loop = _asyncio.get_event_loop()

    # 提交任务
    def _submit():
        return _requests.post(
            OCR_JOB_URL,
            headers=headers,
            data={"model": OCR_MODEL, "optionalPayload": _json_ocr.dumps({
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": True,
            })},
            files={"file": ("image.jpg", image_bytes, "image/jpeg")},
            timeout=20,
        )
    job_resp = await loop.run_in_executor(None, _submit)
    if job_resp.status_code != 200:
        raise HTTPException(500, f"提交OCR失败: {job_resp.text[:300]}")
    job_id = job_resp.json()["data"]["jobId"]

    # 轮询结果（最多 90 秒）
    jsonl_url = None
    deadline = _time.time() + 90
    while _time.time() < deadline:
        await _asyncio.sleep(3)
        poll = await loop.run_in_executor(
            None, lambda: _requests.get(f"{OCR_JOB_URL}/{job_id}", headers=headers, timeout=10)
        )
        state = poll.json()["data"]["state"]
        if state == "done":
            jsonl_url = poll.json()["data"]["resultUrl"]["jsonUrl"]
            break
        elif state == "failed":
            raise HTTPException(500, f"OCR任务失败: {poll.json()['data'].get('errorMsg', '')}")

    if not jsonl_url:
        raise HTTPException(500, "OCR识别超时（90秒）")

    # 下载结果，提取文字行
    raw = await loop.run_in_executor(None, lambda: _requests.get(jsonl_url, timeout=15))
    lines = []
    for raw_line in raw.text.strip().split("\n"):
        if not raw_line.strip():
            continue
        try:
            res_obj = _json_ocr.loads(raw_line)["result"]
        except Exception:
            continue
        for res in res_obj.get("layoutParsingResults", []):
            md = res.get("markdown", {}).get("text", "")
            for md_line in md.splitlines():
                if "<img" in md_line or "src=" in md_line:
                    continue
                cleaned = _re.sub(r"[#*`>\-|\\]+", " ", md_line).strip()
                cleaned = _re.sub(r"\s{2,}", " ", cleaned)
                if cleaned and not _re.match(r"^[\s\W]+$", cleaned):
                    lines.append(cleaned)
    return lines


def _fuzzy_match(word: str, candidates: list[str], threshold: int = 70) -> str | None:
    word = word.strip()
    for c in candidates:
        if word == c or word in c or c in word:
            return c
    ws = set(word)
    best, best_score = None, 0
    for c in candidates:
        cs = set(c)
        if not ws or not cs:
            continue
        score = len(ws & cs) / max(len(ws), len(cs)) * 100
        if score >= threshold and score > best_score:
            best, best_score = c, score
    return best


def _parse_equip_items(lines: list[str], eq_skills: dict, eq_names: dict) -> list[dict]:
    """从 OCR 文字行中解析出多个装备，每行可能含多个 token"""
    skill_names = list(eq_skills.keys())
    equip_names = list(eq_names.keys())
    type_kw = {"武器", "防具", "坐骑", "宝物"}
    stat_map = {"武": "force_bonus", "智": "intel_bonus", "统": "command_bonus",
                "速": "speed_bonus", "政": "politics_bonus", "魅": "charisma_bonus"}

    items: list[dict] = []
    cur: dict | None = None

    def flush():
        if cur and cur.get("equip_name"):
            items.append(cur)

    for line in lines:
        for token in line.split():
            token = token.strip()
            if not token:
                continue
            # 装备名命中 → 开新条目
            matched_eq = _fuzzy_match(token, equip_names)
            if matched_eq:
                flush()
                cur = {"equip_name": matched_eq,
                       "equip_type": eq_names.get(matched_eq),
                       "skills": [], "stats": {}}
                continue
            if cur is None:
                cur = {"equip_name": None, "equip_type": None, "skills": [], "stats": {}}
            # 类型
            if token in type_kw:
                cur["equip_type"] = token
                continue
            # 特技
            ms = _fuzzy_match(token, skill_names)
            if ms:
                if ms not in [s["name"] for s in cur["skills"]]:
                    cur["skills"].append({"name": ms, "desc": eq_skills[ms]})
                continue
            # 属性数值，如 统率+8.61 武力+8.55
            for m in _re.finditer(r"([武智统速政魅])力?[+＋](\d+\.?\d*)", token):
                field = stat_map.get(m.group(1))
                if field:
                    cur["stats"][field] = float(m.group(2))

    flush()
    return items


@app.post("/api/ocr/equipment")
async def ocr_equipment(file: UploadFile = File(...)):
    image_bytes = await file.read()
    lines = await _call_paddle_vl(image_bytes)

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT name, description FROM equipment_skills")
    eq_skills = {r["name"]: r["description"] for r in cur.fetchall()}
    cur.execute("SELECT name, eq_type FROM user_equipment WHERE user_id='u2' LIMIT 500")
    eq_names = {r["name"]: r["eq_type"] for r in cur.fetchall()}
    cur.close(); conn.close()

    items = _parse_equip_items(lines, eq_skills, eq_names)
    return {"items": items, "raw": lines}


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
