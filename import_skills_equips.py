#!/usr/bin/env python3
"""
三国志战略版战法 + 装备特技导入脚本
直接调用 Notion 公开 API，无需浏览器
需先建立 SSH 隧道: ssh -i key -L 15432:localhost:5432 -N -f ubuntu@132.145.24.63
"""

import re

import psycopg2
import requests

NOTION_HOST = "https://spacekid.notion.site"
SPACE_ID    = "3c3cffd9-e02f-4397-95b9-a0773524e8a6"
DB          = dict(host="127.0.0.1", port=15432, dbname="sgz", user="sgz", password="sgz2026")

# ── 战法 ──
SKILL_COLLECTION_ID = "d4c2ac16-4726-40d0-bac0-31921126d967"
SKILL_VIEW_ID       = "3227de1c-e389-42d8-bde9-34260519cade"
SKILL_FIELDS = {
    "title":  "name",
    "@LAx":   "category",
    "@SwI":   "trigger_prob",
    "a^Kk":   "description",
    "UljU":   "effect",
    "{zLr":   "quality",
    "WBYj":   "source",
    "[gY:":   "troop_type",
    "hRMB":   "target",
    "PrmV":   "conflict",
}

# ── 装备特技 ──
EQUIP_COLLECTION_ID = "967d72b9-c7a1-4430-84f4-3f5cd7ff1165"
EQUIP_VIEW_ID       = "9c7f4db1-2f7f-409f-a9ca-a9a292abcd70"
EQUIP_FIELDS = {
    "title":  "name",
    "PYfp":   "eq_type",
    "R^Zn":   "strength_level",
    "cO^o":   "source",
    "kxsQ":   "description",
    "p}AC":   "exclusive_for",
}


def get_prop(props, key, stype):
    if not props or key not in props:
        return None
    val = props[key]
    if not val or not val[0]:
        return None
    raw = val[0][0]
    if stype in ("title", "text"):
        return "".join(seg[0] for seg in val if seg)
    if stype == "number":
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
    return str(raw) if raw else None


def query_collection(collection_id, view_id):
    resp = requests.post(
        f"{NOTION_HOST}/api/v3/queryCollection",
        headers={"content-type": "application/json"},
        json={
            "collection":     {"id": collection_id, "spaceId": SPACE_ID},
            "collectionView": {"id": view_id,       "spaceId": SPACE_ID},
            "query": {"sort": [], "filter": {"filters": [], "operator": "and"}, "aggregations": []},
            "loader": {
                "type": "reducer",
                "reducers": {"collection_group_results": {"type": "results", "limit": 1000}},
                "userTimeZone": "Asia/Shanghai",
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def parse_rows(data, collection_id, field_map):
    record_map = data["recordMap"]
    block_ids  = data["result"]["reducerResults"]["collection_group_results"]["blockIds"]
    blocks     = record_map["block"]
    schema     = record_map["collection"][collection_id]["value"]["value"]["schema"]

    rows = []
    for bid in block_ids:
        b = blocks.get(bid, {}).get("value", {}).get("value", {})
        if not b or b.get("type") != "page":
            continue
        props = b.get("properties", {})
        row = {}
        for nkey, dbcol in field_map.items():
            stype = schema.get(nkey, {}).get("type", "text")
            row[dbcol] = get_prop(props, nkey, stype)
        rows.append(row)
    return rows


_LV_RANGE_RE = re.compile(r"(?P<low>\d+(?:\.\d+)?%?)\s*(?:→|->)\s*(?P<high>\d+(?:\.\d+)?%?)")


def normalize_skill_description_to_lv10(text):
    """把描述中的等级区间值统一替换为右值（10级值），如 21%→42% => 42%。"""
    if not text:
        return text
    return _LV_RANGE_RE.sub(lambda m: m.group("high"), text)


def insert_skills(conn, skills):
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE skills RESTART IDENTITY CASCADE")
    conn.commit()
    inserted = 0
    for s in skills:
        if not s.get("name"):
            continue
        s["description"] = normalize_skill_description_to_lv10(s.get("description"))
        try:
            cur.execute("SAVEPOINT sp1")
            cur.execute("""
                INSERT INTO skills (name, category, trigger_prob, description, effect, quality, source, troop_type, target, conflict)
                VALUES (%(name)s, %(category)s, %(trigger_prob)s, %(description)s, %(effect)s, %(quality)s, %(source)s, %(troop_type)s, %(target)s, %(conflict)s)
                ON CONFLICT (name) DO UPDATE SET
                    category=EXCLUDED.category, description=EXCLUDED.description,
                    effect=EXCLUDED.effect, quality=EXCLUDED.quality,
                    source=EXCLUDED.source, troop_type=EXCLUDED.troop_type,
                    target=EXCLUDED.target, conflict=EXCLUDED.conflict
            """, s)
            cur.execute("RELEASE SAVEPOINT sp1")
            inserted += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp1")
            print(f"    ⚠️  跳过战法 {s.get('name')}: {e}")
    conn.commit()
    cur.close()
    print(f"  ✓ 战法写入 {inserted} 条")


def insert_equips(conn, equips):
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE equipment_skills RESTART IDENTITY CASCADE")
    conn.commit()
    inserted = 0
    for e in equips:
        if not e.get("name"):
            continue
        try:
            cur.execute("SAVEPOINT sp1")
            cur.execute("""
                INSERT INTO equipment_skills (name, eq_type, strength_level, source, description, exclusive_for)
                VALUES (%(name)s, %(eq_type)s, %(strength_level)s, %(source)s, %(description)s, %(exclusive_for)s)
                ON CONFLICT (name) DO UPDATE SET
                    eq_type=EXCLUDED.eq_type, strength_level=EXCLUDED.strength_level,
                    source=EXCLUDED.source, description=EXCLUDED.description,
                    exclusive_for=EXCLUDED.exclusive_for
            """, e)
            cur.execute("RELEASE SAVEPOINT sp1")
            inserted += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp1")
            print(f"    ⚠️  跳过装备 {e.get('name')}: {e}")
    conn.commit()
    cur.close()
    print(f"  ✓ 装备特技写入 {inserted} 条")


def main():
    print("=== 战法 + 装备特技导入 ===\n")

    print("【战法】调用 Notion API...")
    skill_data = query_collection(SKILL_COLLECTION_ID, SKILL_VIEW_ID)
    skills = parse_rows(skill_data, SKILL_COLLECTION_ID, SKILL_FIELDS)
    print(f"  获取 {len(skills)} 个战法")
    print(f"  示例: {skills[0]['name']} | {skills[0]['category']} | {skills[0]['quality']}")

    print("\n【装备】调用 Notion API...")
    equip_data = query_collection(EQUIP_COLLECTION_ID, EQUIP_VIEW_ID)
    equips = parse_rows(equip_data, EQUIP_COLLECTION_ID, EQUIP_FIELDS)
    print(f"  获取 {len(equips)} 个装备特技")
    print(f"  示例: {equips[0]['name']} | {equips[0]['eq_type']} | {equips[0]['strength_level']}")

    print("\n连接数据库...")
    conn = psycopg2.connect(**DB)

    print("写入战法...")
    insert_skills(conn, skills)

    print("写入装备特技...")
    insert_equips(conn, equips)

    # 验证
    cur = conn.cursor()
    for tbl in ("skills", "equipment_skills"):
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {cur.fetchone()[0]} 条")
    cur.close()
    conn.close()

    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
