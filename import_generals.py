#!/usr/bin/env python3
"""
三国志战略版武将数据导入脚本
直接调用 Notion 公开 API，无需浏览器
用法: python3 import_generals.py
需先建立 SSH 隧道: ssh -i ~/.ssh/key -L 15432:localhost:5432 -N -f ubuntu@132.145.24.63
"""

import json, requests, psycopg2

# ── Notion API 参数 ──
NOTION_HOST  = "https://spacekid.notion.site"
COLLECTION_ID = "533bff5c-38cf-435c-82f6-a62b1e133705"
VIEW_ID       = "7f0e2a00-f459-4aac-8f5c-3ac12ad38fcf"
SPACE_ID      = "3c3cffd9-e02f-4397-95b9-a0773524e8a6"

# ── 数据库连接（SSH 隧道） ──
DB = dict(host="127.0.0.1", port=15432, dbname="sgz", user="sgz", password="sgz2026")

# ── Notion key -> DB column 映射 ──
FIELD_MAP = {
    "title":  "name",
    "Mi`L":   "faction",
    '{7o"':   "command_value",
    "O|&L":   "cavalry",
    "UkLy":   "shield",
    "#?Lj":   "bow",
    ":#Qo":   "spear",
    "u?sB":   "machine",
    "|JWQ":   "navy",
    "%'<c":   "strength",
    "Rh+5":   "intelligence",
    "j1a4":   "leadership",
    "#zKK":   "speed",
    "v'~d":   "politics",
    "ZrFy":   "charisma",
    "Z&b+":   "str_growth",
    "Oq0e":   "int_growth",
    "jlx{":   "lead_growth",
    "ea61":   "spd_growth",
    "}]1S":   "pol_growth",
    "EpF\\":  "cha_growth",
    ">tNv":   "role",
    "Z`y]":   "is_collector",
    "WI^j":   "is_dynamic",
    "DXzI":   "is_strategist",
    "T;mk":   "strategist_skill",
    "?~F}":   "strategy_points",
    "qaz<":   "exclusive_title",
}

TROOP_KEYS = {"cavalry","shield","bow","spear","machine","navy"}
BOOL_KEYS  = {"is_collector","is_dynamic","is_strategist"}


def get_prop(props, key, schema_type):
    if not props or key not in props:
        return None
    val = props[key]
    if not val:
        return None
    raw = val[0][0] if val and val[0] else None
    if raw is None:
        return None
    if schema_type in ("title", "text"):
        return "".join(seg[0] for seg in val if seg)
    if schema_type == "number":
        try:
            return float(raw) if "." in str(raw) else int(raw)
        except (ValueError, TypeError):
            return None
    return str(raw)


def fetch_generals():
    print("  调用 Notion API...")
    resp = requests.post(
        f"{NOTION_HOST}/api/v3/queryCollection",
        headers={"content-type": "application/json"},
        json={
            "collection":     {"id": COLLECTION_ID, "spaceId": SPACE_ID},
            "collectionView": {"id": VIEW_ID,        "spaceId": SPACE_ID},
            "query": {"sort": [], "filter": {"filters": [], "operator": "and"}, "aggregations": []},
            "loader": {
                "type": "reducer",
                "reducers": {"collection_group_results": {"type": "results", "limit": 500}},
                "userTimeZone": "Asia/Shanghai",
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    record_map  = data["recordMap"]
    block_ids   = data["result"]["reducerResults"]["collection_group_results"]["blockIds"]
    blocks      = record_map["block"]
    schema      = record_map["collection"][COLLECTION_ID]["value"]["value"]["schema"]

    print(f"  共 {len(block_ids)} 个武将")

    generals = []
    for bid in block_ids:
        b = blocks.get(bid, {}).get("value", {}).get("value", {})
        if not b or b.get("type") != "page":
            continue
        props = b.get("properties", {})
        row = {}
        for nkey, dbcol in FIELD_MAP.items():
            stype = schema.get(nkey, {}).get("type", "text")
            row[dbcol] = get_prop(props, nkey, stype)

        # 兵种取末尾字母
        for k in TROOP_KEYS:
            if row.get(k):
                row[k] = row[k].split()[-1]
        # 统御值 string→int
        if row.get("command_value"):
            try:
                row["command_value"] = int(row["command_value"])
            except (ValueError, TypeError):
                row["command_value"] = None
        # 布尔
        for k in BOOL_KEYS:
            row[k] = row.get(k) == "已开放"

        generals.append(row)

    return generals


def insert_generals(conn, generals):
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE generals RESTART IDENTITY CASCADE")
    conn.commit()
    inserted = 0
    for g in generals:
        if not g.get("name"):
            continue
        try:
            cur.execute("SAVEPOINT sp1")
            cur.execute("""
                INSERT INTO generals (
                    name, faction, command_value,
                    cavalry, shield, bow, spear, machine, navy,
                    strength, intelligence, leadership, speed, politics, charisma,
                    str_growth, int_growth, lead_growth, spd_growth, pol_growth, cha_growth,
                    role, is_collector, is_dynamic, is_strategist,
                    strategist_skill, strategy_points, exclusive_title
                ) VALUES (
                    %(name)s, %(faction)s, %(command_value)s,
                    %(cavalry)s, %(shield)s, %(bow)s, %(spear)s, %(machine)s, %(navy)s,
                    %(strength)s, %(intelligence)s, %(leadership)s, %(speed)s, %(politics)s, %(charisma)s,
                    %(str_growth)s, %(int_growth)s, %(lead_growth)s, %(spd_growth)s, %(pol_growth)s, %(cha_growth)s,
                    %(role)s, %(is_collector)s, %(is_dynamic)s, %(is_strategist)s,
                    %(strategist_skill)s, %(strategy_points)s, %(exclusive_title)s
                )
            """, g)
            cur.execute("RELEASE SAVEPOINT sp1")
            inserted += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp1")
            print(f"    ⚠️  跳过 {g.get('name')}: {e}")
            # 打印出问题的字段值
            for k in ("str_growth","int_growth","lead_growth","spd_growth","pol_growth","cha_growth"):
                if g.get(k) is not None:
                    print(f"         {k}={g[k]}")
    conn.commit()
    cur.close()
    print(f"  ✓ 写入 {inserted} 条")


def main():
    print("=== 武将数据导入 ===\n")

    generals = fetch_generals()

    # 打印前3条预览
    print("\n前3条预览:")
    for g in generals[:3]:
        print(f"  {g['name']} | {g['faction']} | 统御{g['command_value']} | 骑{g['cavalry']} 盾{g['shield']} 弓{g['bow']} | 武{g['strength']} 智{g['intelligence']}")

    print("\n连接数据库 (SSH隧道 15432)...")
    conn = psycopg2.connect(**DB)
    insert_generals(conn, generals)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM generals")
    print(f"\n数据库 generals 表: {cur.fetchone()[0]} 条")
    cur.close()
    conn.close()

    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
