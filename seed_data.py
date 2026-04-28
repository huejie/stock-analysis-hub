"""生成模拟数据用于功能预览。运行: python seed_data.py

⚠️  此脚本会清空并替换 data/stock.db 中的所有数据，仅用于开发环境！
"""
import json
import os
import random
import sqlite3
from pathlib import Path

random.seed(42)

DB_PATH = Path(__file__).parent / "data" / "stock.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 安全确认：检查数据库中是否已有真实数据
conn = sqlite3.connect(str(DB_PATH))
existing = conn.execute("SELECT COUNT(*) FROM stock_records").fetchone()[0]
if existing > 0:
    print(f"[WARNING] 数据库中已有 {existing} 条记录！")
    print("此脚本会清空所有数据并替换为模拟数据。")
    confirm = input("确认继续？(yes/no): ").strip().lower()
    if confirm != "yes":
        print("[INFO] 已取消。")
        conn.close()
        raise SystemExit(0)
conn.executescript("""
    DELETE FROM stock_records;
    DELETE FROM season_daily_stats;
    DELETE FROM seasons;
""")
conn.commit()
conn.close()
print("[INFO] 已清空旧数据")

SECTORS = ["人工智能", "芯片半导体", "新能源", "机器人", "低空经济", "光伏", "军工",
           "数字经济", "消费电子", "医药", "汽车", "跨境电商", "算力", "国产替代"]

STOCKS = [
    ("中际旭创", "300308"), ("寒武纪", "688256"), ("宁德时代", "300750"),
    ("贵州茅台", "600519"), ("比亚迪", "002594"), ("工业富联", "601138"),
    ("中科曙光", "603019"), ("科大讯飞", "002230"), ("浪潮信息", "000977"),
    ("紫光股份", "000938"), ("三六零", "601360"), ("兆易创新", "603986"),
    ("汇川技术", "300124"), ("通威股份", "600438"), ("天齐锂业", "002466"),
    ("赣锋锂业", "002460"), ("阳光电源", "300274"), ("迈瑞医疗", "300760"),
    ("恒瑞医药", "600276"), ("北方华创", "002371"), ("韦尔股份", "603501"),
    ("歌尔股份", "002241"), ("立讯精密", "002475"), ("海光信息", "688041"),
    ("金山办公", "688111"), ("中微公司", "688012"), ("拓荆科技", "688072"),
    ("长电科技", "600584"), ("深南电路", "002916"), ("中兴通讯", "000063"),
]

# 生成15个交易日的日期 (跳过周末)
from datetime import date, timedelta

dates = []
d = date(2026, 4, 8)
while len(dates) < 15:
    if d.weekday() < 5:
        dates.append(d.isoformat())
    d += timedelta(days=1)


def gen_records_for_date(date_str: str, prev_stocks: list[str] | None = None):
    """为某日生成 Top10 记录，部分股票延续前一天以产生排名变化。"""
    records = []
    # 70% 延续前一天，30% 新股
    if prev_stocks:
        carry = random.sample(prev_stocks, k=min(7, len(prev_stocks)))
        new_count = 10 - len(carry)
        pool = [s for s in STOCKS if s[1] not in carry]
        new_stocks = random.sample(pool, k=new_count)
        selected = carry + [s[1] for s in new_stocks]
    else:
        selected = [s[1] for s in random.sample(STOCKS, k=10)]

    random.shuffle(selected)

    for rank, code in enumerate(selected[:10], 1):
        name = next(n for n, c in STOCKS if c == code)
        heat = round(random.uniform(300, 2000) - rank * 80, 2)
        change = round(random.uniform(-8, 10), 2)
        turnover = round(random.uniform(3, 60) + (10 - rank) * 3, 1)
        holders_today = random.randint(80000, 300000)
        holders_yesterday = holders_today + random.randint(-20000, 25000)
        tags = random.sample(SECTORS, k=random.randint(1, 3))

        records.append({
            "date": date_str,
            "rank": rank,
            "stock_name": name,
            "stock_code": code,
            "heat_value": heat,
            "sector_tags": json.dumps(tags, ensure_ascii=False),
            "price_change_pct": change,
            "turnover_amount": turnover,
            "holders_today": holders_today,
            "holders_yesterday": holders_yesterday,
            "price_action": random.choice(["大阳线", "小阳线", "十字星", "小阴线", "大阴线", "涨停", "跌停"]),
            "per_capital_pnl": None,
            "per_capital_position": None,
        })
    return records


def gen_season_stats(dates: list[str]):
    """为每个交易日生成赛季统计数据（人均盈亏 + 仓位）。"""
    stats = []
    position = random.uniform(60, 85)  # 初始仓位
    for d in dates:
        pnl = round(random.uniform(-3, 4), 2)
        position = round(min(95, max(30, position + random.uniform(-5, 5))), 2)
        stats.append({
            "date": d,
            "per_capital_pnl": pnl,
            "per_capital_position": position,
        })
    return stats


# 创建数据库并插入数据
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

# 建表
conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, rank INTEGER NOT NULL,
        stock_name TEXT NOT NULL, stock_code TEXT NOT NULL,
        heat_value REAL, sector_tags TEXT,
        price_change_pct REAL, turnover_amount REAL,
        holders_today INTEGER, holders_yesterday INTEGER,
        price_action TEXT, per_capital_pnl REAL, per_capital_position REAL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(date, stock_code)
    )
""")
conn.execute("""
    CREATE TABLE IF NOT EXISTS season_daily_stats (
        date TEXT PRIMARY KEY,
        per_capital_pnl REAL, per_capital_position REAL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
""")
conn.execute("""
    CREATE TABLE IF NOT EXISTS seasons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
""")

# 插入股票记录
prev_codes = None
for d in dates:
    recs = gen_records_for_date(d, prev_codes)
    prev_codes = [r["stock_code"] for r in recs]
    conn.executemany("""
        INSERT INTO stock_records
            (date, rank, stock_name, stock_code, heat_value,
             sector_tags, price_change_pct, turnover_amount,
             holders_today, holders_yesterday, price_action,
             per_capital_pnl, per_capital_position)
        VALUES
            (:date, :rank, :stock_name, :stock_code, :heat_value,
             :sector_tags, :price_change_pct, :turnover_amount,
             :holders_today, :holders_yesterday, :price_action,
             :per_capital_pnl, :per_capital_position)
    """, recs)

# 插入赛季统计
season_stats = gen_season_stats(dates)
conn.executemany("""
    INSERT INTO season_daily_stats (date, per_capital_pnl, per_capital_position)
    VALUES (:date, :per_capital_pnl, :per_capital_position)
""", season_stats)

# 创建赛季
conn.execute(
    "INSERT INTO seasons (name, start_date, end_date) VALUES (?, ?, ?)",
    ("第1赛季", dates[0], dates[-1]),
)

conn.commit()

# 打印统计
stock_count = conn.execute("SELECT COUNT(*) FROM stock_records").fetchone()[0]
season_count = conn.execute("SELECT COUNT(*) FROM season_daily_stats").fetchone()[0]
date_list = [r[0] for r in conn.execute("SELECT DISTINCT date FROM stock_records ORDER BY date").fetchall()]

print(f"[OK] 生成完成:")
print(f"  - 股票记录: {stock_count} 条")
print(f"  - 交易日:   {len(date_list)} 天 ({date_list[0]} ~ {date_list[-1]})")
print(f"  - 赛季统计: {season_count} 条")
print(f"\n启动服务: python run.py")
print(f"访问:     http://localhost:8888/admin")

conn.close()
