#!/usr/bin/env python3
"""
三国志战略版缘分数据导入
成员数据来源：spacekid.notion.site/sgz-gs 武将表缘分字段（2024）
效果数据来源：m.7724.com/sgzzlbyx/news/19610.html（2021）及 9game.cn（2020）
"""

import psycopg2

DB = dict(host="127.0.0.1", port=15432, dbname="sgz", user="sgz", password="sgz2026")

# 格式：(缘分名称, 最少激活人数, 成员列表, 效果描述)
# 成员数据：从 Notion spacekid 武将表缘分字段提取（2024年数据，140名武将）
# 效果描述：已知的标注来源，未收录的留空待补充
AFFINITIES = [
    # ── 蜀国 ──────────────────────────────────────────────────────────────
    ("桃园结义",    3, ["关羽", "SP 关羽", "刘备", "张飞"],
     "战斗第6回合，我军全体获得两次防御"),

    ("五虎上将",    3, ["关羽", "SP 关羽", "张飞", "马超", "SP 马超", "赵云", "黄忠", "SP 黄忠"],
     "全体武力、统率各提升10点，主将会心提升"),

    ("西蜀之智",    3, ["诸葛亮", "SP 诸葛亮", "庞统", "法正", "SP 法正", "徐庶"],
     "全体智力提升10点，单体前2回合获得两次防御效果"),

    ("天水之战",    2, ["诸葛亮", "SP 诸葛亮", "姜维"],
     "全体武力值提升16点"),

    ("才堪相配",    2, ["诸葛亮", "SP 诸葛亮", "黄月英", "SP 黄月英"],
     ""),

    ("西凉霸雄",    3, ["马超", "SP 马超", "马岱", "马云禄", "庞德", "SP 庞德", "马腾"],
     ""),

    ("鸾凤栖蜀",    2, ["刘备", "孙尚香"],
     ""),

    ("后起之秀",    3, ["姜维", "邓艾", "钟会"],
     ""),

    ("绰有父风",    2, ["关兴", "张苞"],
     ""),

    ("南蛮之乱",    3, ["孟获", "祝融夫人", "朵思大王", "兀突骨", "木鹿大王"],
     ""),

    ("南疆骁侣",    2, ["孟获", "祝融夫人"],
     ""),

    # ── 魏国 ──────────────────────────────────────────────────────────────
    ("三足鼎立",    3, ["曹操", "刘备", "孙权"],
     ""),

    ("虎卫神威",    3, ["曹操", "许褚", "SP 许褚", "典韦", "SP 典韦"],
     "对阵骑兵或枪兵时，全体所受兵刃伤害降低12%"),

    ("五子良将",    3, ["张辽", "乐进", "SP 乐进", "于禁", "张郃", "徐晃"],
     "全体速度提升14点，全体会心4%"),

    ("五谋臣",      3, ["荀彧", "SP 荀彧", "贾诩", "荀攸", "程昱", "郭嘉", "SP 郭嘉"],
     "全体奇谋提升4.5%，全体前2回合获得先手效果"),

    ("曹魏族将",    3, ["曹仁", "夏侯惇", "夏侯渊"],
     "前2回合，其中2人获得破阵效果（无视防御）"),

    ("魏之泽",      3, ["曹仁", "曹洪", "曹真", "SP 曹真", "郭淮", "李典"],
     "全体武力值提升12点"),

    ("太师动乱",    3, ["吕布", "董卓", "SP 董卓", "李儒", "华雄", "贾诩"],
     "主将武力提升25点，其余武将统率提升7%"),

    ("飞将所望",    3, ["吕布", "吕玲绮", "高顺", "陈宫", "张辽"],
     ""),

    ("国之栋才",    3, ["司马懿", "诸葛亮", "SP 诸葛亮", "周瑜", "SP 周瑜"],
     ""),

    ("幽鸷合策",    2, ["司马懿", "张春华"],
     ""),

    ("洛水情殇",    2, ["甄姬", "曹丕"],
     ""),

    ("河间四将",    3, ["颜良", "文丑", "张郃"],
     ""),

    ("乱世妖星",    3, ["张角", "于吉", "左慈"],
     ""),

    ("美人连环计",  3, ["吕布", "董卓", "SP 董卓", "貂蝉", "SP 貂蝉"],
     ""),

    ("乱世才女",    3, ["甄姬", "王异", "王元姬", "黄月英", "SP 黄月英",
                       "张春华", "貂蝉", "SP 貂蝉", "蔡文姬"],
     ""),

    ("汉末职忠",    3, ["皇甫嵩", "SP 皇甫嵩", "朱儁", "SP 朱儁", "卢植", "SP 卢植"],
     "全体统率提升12点"),

    ("黄巾之乱",    3, ["张角", "张宝", "SP 张宝", "张梁", "SP 张梁"],
     "全体武力、智力各提升9点"),

    # ── 吴国 ──────────────────────────────────────────────────────────────
    ("东吴传子",    3, ["孙坚", "SP 孙坚", "孙策", "孙权", "孙尚香"],
     "全体统率、速度提升10点，第1回合受到的伤害降低50%"),

    ("东吴大都督",  3, ["周瑜", "SP 周瑜", "鲁肃", "吕蒙", "SP 吕蒙", "陆逊"],
     "全体速度提升16点，其中2人主动战法伤害提升5%"),

    ("赤壁之战",    3, ["周瑜", "SP 周瑜", "黄盖", "甘宁"],
     "全体武力、智力各提升12点"),

    ("霸王讨逆",    3, ["孙策", "周瑜", "SP 周瑜", "太史慈"],
     "前4回合发动主动战法时有15%概率为全队降低受到的伤害，最多叠加5次"),

    ("江东虎臣",    3, ["太史慈", "周泰", "甘宁", "凌统"],
     "全体统率提升20点，免疫水攻"),

    ("琴瑟和鸣",    3, ["周瑜", "SP 周瑜", "小乔"],
     ""),

    ("江东之娇",    3, ["孙尚香", "大乔", "小乔"],
     ""),

    ("共承天地",    2, ["孙权", "步练师", "SP 步练师"],
     ""),

    ("江东眷影",    2, ["孙策", "大乔"],
     ""),

    ("老当益壮",    3, ["黄忠", "SP 黄忠", "严颜", "黄盖", "程普"],
     ""),

    ("巾帼英雄",    3, ["孙尚香", "关银屏", "马云禄", "吕玲绮", "张姬"],
     ""),
]


DDL = """
CREATE TABLE IF NOT EXISTS affinities (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(30) NOT NULL UNIQUE,
    min_members SMALLINT    NOT NULL DEFAULT 3,
    members     TEXT        NOT NULL,   -- JSON数组
    effect      TEXT        NOT NULL DEFAULT ''
);
"""


def main():
    print(f"=== 缘分数据导入 ({len(AFFINITIES)} 组) ===\n")

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    cur.execute("TRUNCATE TABLE affinities RESTART IDENTITY")
    conn.commit()

    import json
    inserted, skipped = 0, 0
    for name, min_m, members, effect in AFFINITIES:
        try:
            cur.execute("SAVEPOINT sp1")
            cur.execute("""
                INSERT INTO affinities (name, min_members, members, effect)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    min_members = EXCLUDED.min_members,
                    members     = EXCLUDED.members,
                    effect      = EXCLUDED.effect
            """, (name, min_m, json.dumps(members, ensure_ascii=False), effect))
            cur.execute("RELEASE SAVEPOINT sp1")
            inserted += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp1")
            print(f"  ⚠️ 跳过 {name}: {e}")
            skipped += 1

    conn.commit()

    # 验证
    cur.execute("SELECT COUNT(*) FROM affinities")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM affinities WHERE effect != ''")
    with_effect = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM affinities WHERE effect = ''")
    no_effect = cur.fetchone()[0]

    print(f"写入：{inserted} 条，跳过：{skipped} 条")
    print(f"总计：{total} 组")
    print(f"  有效果描述：{with_effect} 组")
    print(f"  效果待补充：{no_effect} 组")

    print("\n--- 各缘分组成员数 ---")
    cur.execute("SELECT name, min_members, json_array_length(members::json), effect != '' FROM affinities ORDER BY name")
    for row in cur.fetchall():
        flag = "✓" if row[3] else "·"
        print(f"  {flag} {row[0]:<10} 需{row[1]}人  共{row[2]}人可用")

    cur.close()
    conn.close()
    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
