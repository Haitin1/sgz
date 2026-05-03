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

import re as _re
import asyncio as _asyncio
import json as _json_ocr
import time as _time

_OCR_DEBUG_FILE = "/tmp/sgz_ocr_debug.txt"

# 本地 OCR 实例（延迟初始化，首次调用时加载模型）
_rapid_ocr_instance = None


def _sse(data: dict) -> str:
    return f"data: {_json_ocr.dumps(data, ensure_ascii=False)}\n\n"


async def _run_local_ocr(image_bytes: bytes) -> list[str]:
    """使用本地 RapidOCR (PP-OCRv4 ONNX) 识别图片，返回按阅读顺序排列的文本行"""
    loop = _asyncio.get_event_loop()

    def _do():
        global _rapid_ocr_instance
        if _rapid_ocr_instance is None:
            from rapidocr_onnxruntime import RapidOCR
            _rapid_ocr_instance = RapidOCR()

        result, _ = _rapid_ocr_instance(image_bytes)
        if not result:
            return []

        # result: [[bbox_points, text, score], ...]
        # bbox_points: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        items = []
        for r in result:
            bbox, text = r[0], r[1]
            score = r[2] if len(r) > 2 else 1.0
            if score < 0.4:
                continue
            y_center = sum(pt[1] for pt in bbox) / 4
            x_left   = bbox[0][0]
            height   = abs(bbox[2][1] - bbox[0][1])
            items.append((y_center, x_left, text, height))

        if not items:
            return []

        # 按 Y 排序后分行（Y 差 < 平均行高×0.6 则视为同一行）
        items.sort(key=lambda x: x[0])
        avg_h = sum(x[3] for x in items) / len(items)
        threshold = max(10, avg_h * 0.6)

        rows, cur_row = [], [items[0]]
        for item in items[1:]:
            if item[0] - cur_row[-1][0] <= threshold:
                cur_row.append(item)
            else:
                rows.append(cur_row)
                cur_row = [item]
        rows.append(cur_row)

        # 同行按 X 排序，合并成一个字符串
        lines = []
        for row in rows:
            row.sort(key=lambda x: x[1])
            lines.append(' '.join(x[2] for x in row))

        # 写调试日志
        try:
            with open(_OCR_DEBUG_FILE, "w") as f:
                f.write(f"RapidOCR lines ({len(lines)}):\n")
                for i, l in enumerate(lines):
                    f.write(f"[{i}] {l}\n")
        except Exception:
            pass

        return lines

    return await loop.run_in_executor(None, _do)


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
        # 位置对齐加分：同位置字符相同（区分"疑神"→"凝神"vs"神医"）
        pos_matches = sum(1 for i in range(min(len(word), len(c))) if word[i] == c[i])
        pos_bonus = pos_matches / max(len(word), len(c)) * 30
        score = max(char_score, seq_score) + pos_bonus
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

    # ── 通用 UI 噪声集（多个解析器共用）──
    _NOISE = {'品质','类型','持有','详情','排序','总览','装备','材料',
              '道具','营造','货布商店','装备名称','批量出售','珍品','返回',
              '库藏','铜币','玉璧','金铁','反','武将','属性','技能','特技',
              '排序','筛选','确认','取消','关闭','背包'}

    # ── 优先：RapidOCR 本地格式（每行一条装备，字段空格分隔）──
    # format: "名称 类型 属性属性 技能"  例: "七星宝刀 武器 武力+8.14统率+4.65 龙骧"
    has_type_in_lines = any(
        any(p in _TYPE_KW for p in l.split())
        for l in lines
        if not all(p in _NOISE or _re.search(r'[\d/\\%]', p) for p in l.split())
    )
    if has_type_in_lines and '\n\n' not in full_text and '<tr' not in full_text.lower():
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            type_idx = next((i for i, p in enumerate(parts) if p in _TYPE_KW), None)
            if type_idx is None:
                continue
            name_raw = ' '.join(parts[:type_idx]).strip()
            if not name_raw or name_raw in _NOISE:
                continue
            eq_type = parts[type_idx]
            rest = parts[type_idx + 1:]
            stats: dict = {}
            skills: list = []
            for r in rest:
                if r in _NOISE or (not _re.match(r'^(武力|智力|统率|速度|政治|魅力)', r) and _re.search(r'[\d/\\%]', r)):
                    continue
                s = _parse_stats(r)
                if s:
                    stats.update(s)
                else:
                    ms = _fuzzy_match(r, skill_names, threshold=50)
                    if ms:
                        skills.append({"name": ms, "desc": eq_skills.get(ms, "")})
                    elif len(r) >= 2 and not _re.search(r'[\d/\\%]', r):
                        skills.append({"name": r, "desc": ""})
            if not stats:
                continue
            matched = _fuzzy_match(name_raw, equip_names)
            name = matched if matched else name_raw
            _add_item(name, eq_type, stats, skills)
        if items:
            return items

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

    # ── 优先：逐token格式（spotting模式，每字段单独一行，\n\n分隔）──
    # format: 名称\n\n类型\n\n--\n\n属性\n\n技能\n\n名称\n\n...
    if '\n\n' in full_text:
        _UI_NOISE = {'品质','类型','持有','详情','排序','总览','装备','材料',
                     '道具','营造','货布商店','装备名称','批量出售','珍品','返回'}
        tokens = [t.strip() for t in _re.split(r'\n+', full_text) if t.strip()]
        for ti, tok in enumerate(tokens):
            if tok not in _TYPE_KW:
                continue
            # 类型前一个token是装备名
            name_raw = tokens[ti - 1] if ti > 0 else ''
            if not name_raw or name_raw in _UI_NOISE or _parse_stats(name_raw):
                continue
            # 往后找属性和技能（跳过--，遇到下一个类型关键词停止）
            stats: dict = {}
            skills: list = []
            j = ti + 1
            while j < len(tokens) and j <= ti + 4:
                t = tokens[j]
                if t in _TYPE_KW:
                    break
                # 下一个token是类型词，说明当前token是下一件装备的名称，停止
                if j + 1 < len(tokens) and tokens[j + 1] in _TYPE_KW:
                    break
                if _re.match(r'^[-－—–\s]+$', t):  # 跳过 -- 分隔符
                    j += 1; continue
                s = _parse_stats(t)
                if s:
                    stats.update(s)
                else:
                    ms = _fuzzy_match(t, skill_names, threshold=50)
                    if ms:
                        skills.append({"name": ms, "desc": eq_skills.get(ms, "")})
                    elif len(t) >= 2 and t not in _UI_NOISE and not _re.search(r'[\d/\\%]', t):
                        skills.append({"name": t, "desc": ""})
                j += 1
            if not stats:
                continue
            matched = _fuzzy_match(name_raw, equip_names)
            name = matched if matched else name_raw
            _add_item(name, tok, stats, skills)
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
                    # 阈值50：凝/疑 等形近字OCR误读也能匹配
                    ms = _fuzzy_match(r, skill_names, threshold=50)
                    if ms:
                        skills.append({"name": ms, "desc": eq_skills.get(ms, "")})
                    elif r and len(r) >= 2:
                        # 匹配失败：保留原始OCR文字，让用户在输入框手动修正
                        skills.append({"name": r, "desc": ""})
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
            ms = _fuzzy_match(skill_text, skill_names, threshold=50)
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
            ms = _fuzzy_match(token, skill_names, threshold=50)
            if ms and ms not in [s["name"] for s in cur["skills"]]:
                cur["skills"].append({"name": ms, "desc": eq_skills[ms]})
                continue
            token_stats = _parse_stats(token)
            if token_stats:
                cur["stats"].update(token_stats)
            elif len(token) >= 2 and token not in _TYPE_KW and token not in _NOISE \
                    and not _re.search(r'[\d/\\%]', token):
                # 非属性非类型非噪声，保留为未匹配技能
                cur["skills"].append({"name": token, "desc": ""})

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
            yield _sse({"progress": 10, "msg": "识别中…"})
            lines = await _run_local_ocr(image_bytes)
            if not lines:
                yield _sse({"error": "未识别到任何文字，请检查图片质量"})
                return
            yield _sse({"progress": 80, "msg": f"解析中（{len(lines)} 行）…"})
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
    """查看 OCR 调试日志"""
    debug_log = ""
    if os.path.exists(_OCR_DEBUG_FILE):
        with open(_OCR_DEBUG_FILE, "r") as f:
            debug_log = f.read()
    return JSONResponse({
        "engine": "RapidOCR (local, PP-OCRv4 ONNX)",
        "model_loaded": _rapid_ocr_instance is not None,
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

# 兵书类别关键词（OCR 会扫到，但不是兵书名）
_BOOK_CAT_KW    = {'作战','始计','辅助','防守','奇袭','牵制','强攻','治军','扰敌'}
_TROOP_LABEL_MAP = {'骑兵':'cavalry','弓兵':'bow','枪兵':'spear','盾兵':'shield','器械':'machine'}
_TROOP_LINE_RE   = _re.compile(r'(骑兵|弓兵|枪兵|盾兵|器械)([SABC5]?)')
# 格式2中出现的状态/体力噪音行关键词
_STATUS_NOISE_RE = _re.compile(
    r'^(?:体力|策力|部队中|重伤|御\d*|主将|副将|觉醒|觉星|SP\s?觉醒|蜀|魏|吴|汉|群)'
    r'|^\d+[：:]\d+'   # 重伤倒计时 "06：40"
    r'|\d+/\d+'         # 兵力/体力分数
)


def _parse_lineup_columns(
    lines: list[str],
    generals_db: list[dict],
    skills_db:   list[dict],
    books_db:    list[dict] | None = None,
) -> list[dict] | None:
    """
    针对 RapidOCR 横向扫描输出的纵列格式解析阵容。
    游戏阵容截图格式：武将名同行（N列）→ 每行 N 个战法（列对应武将）→ 兵书行。
    返回武将列表，或 None（格式不匹配时回退旧解析器）。
    """
    gen_names  = [g["name"] for g in generals_db]
    skill_names= [s["name"] for s in skills_db]
    book_names = [b["name"] for b in (books_db or [])]
    book_type_map = {b["name"]: b.get("book_type", "副兵书") for b in (books_db or [])}

    # ── Step 1: 找武将名行（格式 "47庞统 47诸葛亮 47法正"）──
    generals: list[dict] = []
    gen_line_idx = -1
    for li, line in enumerate(lines):
        found = []
        for tok in line.split():
            m = _LEVEL_RE.match(tok)
            if m:
                lv, name_cand = int(m.group(1)), m.group(2).strip()
                if 1 <= lv <= 60:
                    gm = _fuzzy_match(name_cand, gen_names, threshold=70)
                    if gm:
                        found.append((gm, lv))
        if found:
            generals = [{"name": n, "level": lv, "skills": [], "books": []}
                        for n, lv in found]
            gen_line_idx = li
            break

    if not generals:
        return None

    n = len(generals)  # 列数

    # ── Step 2: 逐行解析武将名行之后的内容 ──
    for line in lines[gen_line_idx + 1:]:
        tokens = line.split()
        if not tokens:
            continue

        # —— 跳过噪音行（体力/策力/部队状态/阵营/觉醒标签等）——
        if all(_STATUS_NOISE_RE.search(t) for t in tokens):
            continue
        # 跳过兵书类别行（"作战 作战 始计" 等）
        if all(t in _BOOK_CAT_KW for t in tokens):
            continue

        # —— 格式2: 兵种行（"弓兵S 3371/9700 弓兵S 2931/9800 ..."）——
        # 特征：每列 token 以兵种开头（骑/弓/枪/盾/器）
        troop_matches = _TROOP_LINE_RE.findall(line)
        if len(troop_matches) >= max(1, n - 1):  # 至少 n-1 列能识别兵种（OCR可能漏一列）
            for col, (label, grade) in enumerate(troop_matches):
                if col >= n:
                    break
                grade = grade.replace('5', 'S').upper() or 'S'   # OCR 常把 S 识别为 5
                generals[col]['troop_type']  = _TROOP_LABEL_MAP.get(label, 'cavalry')
                generals[col]['troop_grade'] = grade if grade in ('S','A','B','C') else 'S'
            continue

        # —— 战法行 ——
        # 去掉 "S"/"A"/"B" 等单字母品质标记
        non_grade = [t for t in tokens if not _re.fullmatch(r'[SABC]', t)]
        if non_grade:
            # 每个 token 尝试模糊匹配战法
            matches = [(t, _fuzzy_match(t, skill_names, threshold=58)) for t in non_grade]
            valid = [(t, sm) for t, sm in matches if sm]
            # 如果有效匹配数 ≥ 1 且 token 数量 == 列数 → 战法行
            if valid and len(non_grade) == n:
                for col, (raw, sm) in enumerate(matches):
                    if col < n and len(generals[col]["skills"]) < 3:
                        generals[col]["skills"].append({"name": sm or raw})
                continue
            # token 数量不等于列数，但全部都是战法也接受
            if valid and len(valid) == len(non_grade) and len(non_grade) <= n:
                for col, (raw, sm) in enumerate(matches):
                    if col < n and len(generals[col]["skills"]) < 3:
                        generals[col]["skills"].append({"name": sm or raw})
                continue

        # —— 兵书行 ——
        # 游戏规则：主兵书4字，副兵书2字；OCR 常将两本副兵书拼在一起输出（无空格）
        all_books: list[str] = []
        main_book_set  = {b["name"] for b in (books_db or []) if len(b["name"]) == 4}
        sub_book_names = [b["name"] for b in (books_db or []) if len(b["name"]) == 2]
        for tok in tokens:
            if not all('一' <= c <= '鿿' for c in tok):
                continue
            tlen = len(tok)
            if tlen == 4:
                # 优先精确匹配主兵书（4字），避免 substring 误匹配拼合副兵书
                if tok in main_book_set:
                    all_books.append(tok)
                else:
                    # OCR 误读的主兵书：先尝试模糊匹配主兵书列表
                    bm = _fuzzy_match(tok, list(main_book_set), threshold=75)
                    if bm:
                        all_books.append(bm)
                    else:
                        # 不是主兵书 → 拆为两本副兵书（2+2）
                        for part in (tok[:2], tok[2:]):
                            pb = _fuzzy_match(part, sub_book_names, threshold=80)
                            if pb:
                                all_books.append(pb)
            elif tlen == 2:
                pb = _fuzzy_match(tok, book_names, threshold=80)
                if pb:
                    all_books.append(pb)
            elif tlen == 6:
                # 先试 4字主兵书 + 2字副兵书
                if tok[:4] in main_book_set:
                    all_books.append(tok[:4])
                    pb = _fuzzy_match(tok[4:], sub_book_names, threshold=80)
                    if pb: all_books.append(pb)
                else:
                    # 三本2字副兵书拼合
                    for i in range(0, 6, 2):
                        pb = _fuzzy_match(tok[i:i+2], sub_book_names, threshold=80)
                        if pb: all_books.append(pb)
            else:
                bm = _fuzzy_match(tok, book_names, threshold=70)
                if bm:
                    all_books.append(bm)

        if all_books:
            bpg, extra = divmod(len(all_books), n)
            idx = 0
            for col in range(n):
                count = bpg + (1 if col < extra else 0)
                for _ in range(count):
                    if idx < len(all_books):
                        bk = all_books[idx]
                        generals[col]["books"].append(
                            {"name": bk, "book_type": book_type_map.get(bk, "副兵书")}
                        )
                        idx += 1

    return generals if generals else None


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
            yield _sse({"progress": 10, "msg": "识别中…"})
            lines = await _run_local_ocr(image_bytes)
            if not lines:
                yield _sse({"error": "未识别到任何文字，请检查图片质量"})
                return
            yield _sse({"progress": 70, "msg": f"解析中…"})
            # 优先用纵列解析器（适合 RapidOCR 横向扫描输出）
            col_generals = _parse_lineup_columns(lines, generals_db, skills_db, books_db)
            if col_generals is not None:
                result = {"generals": col_generals}
            else:
                joined = "\n".join(lines)
                result = _parse_lineup_from_html(joined, generals_db, skills_db, books_db)
            # 补充兵种适性信息（根据 DB 推断最佳兵种）
            GRADE_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1, "": 0}
            TROOP_KEYS  = [("cavalry","骑兵"), ("bow","弓兵"), ("spear","枪兵"), ("shield","盾兵"), ("machine","器械")]
            s_count: dict[str, int] = {}
            for g in result["generals"]:
                if g.get("troop_type"):
                    # 格式2：解析器已从截图直接读出兵种，直接统计 S 票
                    if g.get("troop_grade") == "S":
                        s_count[g["troop_type"]] = s_count.get(g["troop_type"], 0) + 1
                else:
                    # 格式1：截图无兵种信息，从数据库推算最优兵种
                    tr = troop_rows.get(g["name"], {})
                    best_type, best_grade = "cavalry", "C"
                    for key, _ in TROOP_KEYS:
                        grade = tr.get(key, "C") or "C"
                        if GRADE_ORDER.get(grade, 0) > GRADE_ORDER.get(best_grade, 0):
                            best_type, best_grade = key, grade
                        if grade == "S":
                            s_count[key] = s_count.get(key, 0) + 1
                    g["troop_type"]  = best_type
                    g["troop_grade"] = best_grade
            # 队伍兵种：S 最多的兵种（优先按 S 票数，票数相同取 TROOP_KEYS 顺序靠前）
            if s_count:
                team_troop_key = max(TROOP_KEYS, key=lambda kv: s_count.get(kv[0], 0))[0]
            else:
                team_troop_key = (result["generals"][0].get("troop_type") or "cavalry")
            result["team_troop"] = team_troop_key
            result["raw"] = lines[:100]  # 调试用
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
