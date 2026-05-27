import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from backend.config import settings

logger = logging.getLogger("database")

_SECTOR_API = "https://push2his.eastmoney.com/api/qt/stock/get"
_SECTOR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


class Database:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    stock_name TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    heat_value REAL,
                    sector_tags TEXT,
                    price_change_pct REAL,
                    turnover_amount REAL,
                    holders_today INTEGER,
                    holders_yesterday INTEGER,
                    price_action TEXT,
                    per_capital_pnl REAL,
                    per_capital_position REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(date, stock_code)
                )
            """)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_records)").fetchall()}
            if "per_capital_pnl" not in cols:
                conn.execute("ALTER TABLE stock_records ADD COLUMN per_capital_pnl REAL")
            if "per_capital_position" not in cols:
                conn.execute("ALTER TABLE stock_records ADD COLUMN per_capital_position REAL")
            if "total_fund" not in cols:
                conn.execute("ALTER TABLE stock_records ADD COLUMN total_fund REAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS season_daily_stats (
                    date TEXT PRIMARY KEY,
                    per_capital_pnl REAL,
                    per_capital_position REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lhb_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    close_price REAL,
                    change_rate REAL,
                    billboard_buy_amt REAL,
                    billboard_sell_amt REAL,
                    billboard_net_amt REAL,
                    billboard_deal_amt REAL,
                    deal_net_ratio REAL,
                    deal_amount_ratio REAL,
                    turnover_rate REAL,
                    reason TEXT,
                    d1_change REAL,
                    d2_change REAL,
                    d5_change REAL,
                    d10_change REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(date, stock_code, reason)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lhb_trading_desk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    side TEXT NOT NULL,
                    seat_index INTEGER NOT NULL DEFAULT 0,
                    dept_name TEXT NOT NULL,
                    buy_amt REAL,
                    sell_amt REAL,
                    net_amt REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(date, stock_code, side, dept_name, seat_index)
                )
            """)

            # lhb_trading_desk: 迁移旧表添加 seat_index 字段并更新 UNIQUE 约束
            td_cols = {r[1] for r in conn.execute("PRAGMA table_info(lhb_trading_desk)").fetchall()}
            if "seat_index" not in td_cols:
                conn.execute("ALTER TABLE lhb_trading_desk ADD COLUMN seat_index INTEGER NOT NULL DEFAULT 0")
            # 检查UNIQUE约束是否包含seat_index（旧约束是 date,stock_code,side,dept_name）
            # SQLite不支持ALTER约束，需要重建表
            idx_cols = set()
            for idx_row in conn.execute("PRAGMA index_list(lhb_trading_desk)").fetchall():
                if idx_row[3] == 'u':  # unique index
                    for c in conn.execute(f"PRAGMA index_info({idx_row[1]})").fetchall():
                        col_name = conn.execute("PRAGMA table_info(lhb_trading_desk)").fetchall()[c[1] - 1][1] if c[1] > 0 else ''
                        idx_cols.add(col_name)
            if 'seat_index' not in idx_cols:
                logger.info("迁移 lhb_trading_desk: 重建表以更新UNIQUE约束")
                conn.execute("""
                    CREATE TABLE lhb_trading_desk_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL,
                        stock_code TEXT NOT NULL,
                        stock_name TEXT NOT NULL,
                        side TEXT NOT NULL,
                        seat_index INTEGER NOT NULL DEFAULT 0,
                        dept_name TEXT NOT NULL,
                        buy_amt REAL,
                        sell_amt REAL,
                        net_amt REAL,
                        created_at TEXT DEFAULT (datetime('now','localtime')),
                        UNIQUE(date, stock_code, side, dept_name, seat_index)
                    )
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO lhb_trading_desk_new
                    SELECT id, date, stock_code, stock_name, side,
                           COALESCE(seat_index, 0), dept_name, buy_amt, sell_amt, net_amt, created_at
                    FROM lhb_trading_desk
                """)
                conn.execute("DROP TABLE lhb_trading_desk")
                conn.execute("ALTER TABLE lhb_trading_desk_new RENAME TO lhb_trading_desk")
                logger.info("lhb_trading_desk 表迁移完成")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lhb_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    close_price REAL,
                    change_rate REAL,
                    buy_amt REAL,
                    sell_amt REAL,
                    net_amt REAL,
                    inst_count INTEGER,
                    concept_tags TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(date, stock_code, signal_type)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lhb_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    entry_price REAL,
                    concept_tags TEXT,
                    d1_change REAL,
                    d3_change REAL,
                    d5_change REAL,
                    d10_change REAL,
                    d20_change REAL,
                    d30_change REAL,
                    latest_price REAL,
                    latest_date TEXT,
                    tracking_days INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(signal_date, stock_code, signal_type)
                )
            """)

            # 自动创建默认赛季（如果表为空且有数据）
            count = conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
            if count == 0:
                row = conn.execute(
                    "SELECT MIN(date) AS mn, MAX(date) AS mx FROM ("
                    "SELECT date FROM stock_records UNION SELECT date FROM season_daily_stats"
                    ")"
                ).fetchone()
                if row["mn"] and row["mx"]:
                    conn.execute(
                        "INSERT INTO seasons (name, start_date, end_date) VALUES (?, ?, ?)",
                        ("第1赛季", row["mn"], row["mx"]),
                    )

            # 迁移记录表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)

            # 回填空 sector_tags
            mig = conn.execute(
                "SELECT 1 FROM _migrations WHERE name = 'backfill_sector_tags'"
            ).fetchone()
            if not mig:
                empty_count = conn.execute(
                    "SELECT COUNT(DISTINCT stock_code) FROM stock_records "
                    "WHERE sector_tags IS NULL OR sector_tags = '[]' OR sector_tags = ''"
                ).fetchone()[0]
                if empty_count > 0:
                    logger.info("开始回填 %d 只股票的 sector_tags...", empty_count)
                    self._backfill_sector_tags(conn)
                conn.execute(
                    "INSERT INTO _migrations (name) VALUES ('backfill_sector_tags')"
                )

    def _backfill_sector_tags(self, conn):
        """从东方财富 API 回填空 sector_tags。"""
        import httpx
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM stock_records "
            "WHERE sector_tags IS NULL OR sector_tags = '[]' OR sector_tags = ''"
        ).fetchall()
        updated = 0
        for (code,) in rows:
            prefix = "1" if code[0] == "6" else "0"
            try:
                resp = httpx.get(
                    _SECTOR_API,
                    params={"secid": f"{prefix}.{code}", "fields": "f127,f129",
                            "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
                    headers=_SECTOR_HEADERS,
                    timeout=10,
                )
                data = resp.json().get("data")
                if not data:
                    continue
                tags = []
                if data.get("f127"):
                    tags.append(data["f127"])
                if data.get("f129"):
                    tags.extend(c.strip() for c in data["f129"].split(",") if c.strip())
                if tags:
                    tags_json = json.dumps(tags, ensure_ascii=False)
                    conn.execute(
                        "UPDATE stock_records SET sector_tags = ? WHERE stock_code = ? "
                        "AND (sector_tags IS NULL OR sector_tags = '[]' OR sector_tags = '')",
                        (tags_json, code),
                    )
                    updated += conn.total_changes
            except Exception as e:
                logger.warning("sector_tags 回填失败 %s: %s", code, e)
        conn.commit()
        logger.info("sector_tags 回填完成: %d 条更新", updated)

    def insert_records(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO stock_records
                    (date, rank, stock_name, stock_code, heat_value,
                     sector_tags, price_change_pct, turnover_amount,
                     holders_today, holders_yesterday, price_action,
                     per_capital_pnl, per_capital_position, total_fund)
                VALUES
                    (:date, :rank, :stock_name, :stock_code, :heat_value,
                     :sector_tags, :price_change_pct, :turnover_amount,
                     :holders_today, :holders_yesterday, :price_action,
                     :per_capital_pnl, :per_capital_position, :total_fund)
            """, records)

    def upsert_records(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO stock_records
                    (date, rank, stock_name, stock_code, heat_value,
                     sector_tags, price_change_pct, turnover_amount,
                     holders_today, holders_yesterday, price_action,
                     per_capital_pnl, per_capital_position, total_fund)
                VALUES
                    (:date, :rank, :stock_name, :stock_code, :heat_value,
                     :sector_tags, :price_change_pct, :turnover_amount,
                     :holders_today, :holders_yesterday, :price_action,
                     :per_capital_pnl, :per_capital_position, :total_fund)
                ON CONFLICT(date, stock_code) DO UPDATE SET
                    rank = excluded.rank,
                    stock_name = excluded.stock_name,
                    heat_value = COALESCE(excluded.heat_value, stock_records.heat_value),
                    sector_tags = COALESCE(excluded.sector_tags, stock_records.sector_tags),
                    price_change_pct = COALESCE(excluded.price_change_pct, stock_records.price_change_pct),
                    turnover_amount = COALESCE(excluded.turnover_amount, stock_records.turnover_amount),
                    holders_today = COALESCE(excluded.holders_today, stock_records.holders_today),
                    holders_yesterday = COALESCE(excluded.holders_yesterday, stock_records.holders_yesterday),
                    price_action = CASE
                        WHEN excluded.price_action = '' THEN stock_records.price_action
                        ELSE excluded.price_action
                    END,
                    per_capital_pnl = COALESCE(excluded.per_capital_pnl, stock_records.per_capital_pnl),
                    per_capital_position = COALESCE(excluded.per_capital_position, stock_records.per_capital_position),
                    total_fund = COALESCE(excluded.total_fund, stock_records.total_fund)
            """, records)

    def delete_records_by_date(self, date_str: str):
        """删除指定日期的所有股票记录，用于重新爬取时避免叠加。"""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM stock_records WHERE date = ?",
                (date_str,),
            )
            logger.info("已清除 %s 的旧记录", date_str)

    def query_by_date(self, date: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stock_records WHERE date = ? ORDER BY rank",
                (date,)
            ).fetchall()
            return [dict(r) for r in rows]

    def query_date_range(self, start: str, end: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stock_records WHERE date BETWEEN ? AND ? ORDER BY date, rank",
                (start, end)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_dates(self) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM stock_records ORDER BY date DESC"
            ).fetchall()
            return [r["date"] for r in rows]

    def execute(self, sql: str, params=None):
        with self._get_conn() as conn:
            rows = conn.execute(sql, params or []).fetchall()
            return [dict(r) for r in rows]

    # ---- 赛季每日统计 ----

    def upsert_season_stats(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO season_daily_stats (date, per_capital_pnl, per_capital_position)
                VALUES (:date, :per_capital_pnl, :per_capital_position)
                ON CONFLICT(date) DO UPDATE SET
                    per_capital_pnl = excluded.per_capital_pnl,
                    per_capital_position = COALESCE(excluded.per_capital_position, season_daily_stats.per_capital_position)
            """, records)

    def query_season_stats(self, start: str = "", end: str = "") -> list[dict]:
        with self._get_conn() as conn:
            if start and end:
                rows = conn.execute(
                    "SELECT * FROM season_daily_stats WHERE date BETWEEN ? AND ? ORDER BY date",
                    (start, end),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM season_daily_stats ORDER BY date"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_season_dates(self) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT date FROM season_daily_stats ORDER BY date DESC"
            ).fetchall()
            return [r["date"] for r in rows]

    # ---- 赛季管理 ----

    def get_all_seasons(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM seasons ORDER BY start_date DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_season(self, season_id: int) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM seasons WHERE id = ?", (season_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_season(self, name: str, start_date: str, end_date: str) -> dict:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO seasons (name, start_date, end_date) VALUES (?, ?, ?)",
                (name, start_date, end_date),
            )
            return {"id": cur.lastrowid, "name": name, "start_date": start_date, "end_date": end_date}

    def update_season(self, season_id: int, name: str | None = None,
                      start_date: str | None = None, end_date: str | None = None) -> bool:
        sets, params = [], []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if start_date is not None:
            sets.append("start_date = ?")
            params.append(start_date)
        if end_date is not None:
            sets.append("end_date = ?")
            params.append(end_date)
        if not sets:
            return False
        params.append(season_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE seasons SET {', '.join(sets)} WHERE id = ?", params)
            return True

    # ---- 龙虎榜 ----

    def upsert_lhb_records(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO lhb_records
                    (date, stock_code, stock_name, close_price, change_rate,
                     billboard_buy_amt, billboard_sell_amt, billboard_net_amt,
                     billboard_deal_amt, deal_net_ratio, deal_amount_ratio,
                     turnover_rate, reason, d1_change, d2_change, d5_change, d10_change)
                VALUES
                    (:date, :stock_code, :stock_name, :close_price, :change_rate,
                     :billboard_buy_amt, :billboard_sell_amt, :billboard_net_amt,
                     :billboard_deal_amt, :deal_net_ratio, :deal_amount_ratio,
                     :turnover_rate, :reason, :d1_change, :d2_change, :d5_change, :d10_change)
                ON CONFLICT(date, stock_code, reason) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    close_price = COALESCE(excluded.close_price, lhb_records.close_price),
                    change_rate = COALESCE(excluded.change_rate, lhb_records.change_rate),
                    billboard_buy_amt = COALESCE(excluded.billboard_buy_amt, lhb_records.billboard_buy_amt),
                    billboard_sell_amt = COALESCE(excluded.billboard_sell_amt, lhb_records.billboard_sell_amt),
                    billboard_net_amt = COALESCE(excluded.billboard_net_amt, lhb_records.billboard_net_amt),
                    billboard_deal_amt = COALESCE(excluded.billboard_deal_amt, lhb_records.billboard_deal_amt),
                    turnover_rate = COALESCE(excluded.turnover_rate, lhb_records.turnover_rate),
                    d1_change = COALESCE(excluded.d1_change, lhb_records.d1_change),
                    d2_change = COALESCE(excluded.d2_change, lhb_records.d2_change),
                    d5_change = COALESCE(excluded.d5_change, lhb_records.d5_change),
                    d10_change = COALESCE(excluded.d10_change, lhb_records.d10_change)
            """, records)

    def upsert_lhb_trading_desk(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO lhb_trading_desk
                    (date, stock_code, stock_name, side, seat_index, dept_name, buy_amt, sell_amt, net_amt)
                VALUES
                    (:date, :stock_code, :stock_name, :side, :seat_index, :dept_name, :buy_amt, :sell_amt, :net_amt)
                ON CONFLICT(date, stock_code, side, dept_name, seat_index) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    buy_amt = COALESCE(excluded.buy_amt, lhb_trading_desk.buy_amt),
                    sell_amt = COALESCE(excluded.sell_amt, lhb_trading_desk.sell_amt),
                    net_amt = COALESCE(excluded.net_amt, lhb_trading_desk.net_amt)
            """, records)

    def upsert_lhb_signals(self, records: list[dict]):
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO lhb_signals
                    (date, stock_code, stock_name, signal_type, close_price, change_rate,
                     buy_amt, sell_amt, net_amt, inst_count, concept_tags)
                VALUES
                    (:date, :stock_code, :stock_name, :signal_type, :close_price, :change_rate,
                     :buy_amt, :sell_amt, :net_amt, :inst_count, :concept_tags)
                ON CONFLICT(date, stock_code, signal_type) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    close_price = COALESCE(excluded.close_price, lhb_signals.close_price),
                    change_rate = COALESCE(excluded.change_rate, lhb_signals.change_rate),
                    buy_amt = COALESCE(excluded.buy_amt, lhb_signals.buy_amt),
                    sell_amt = COALESCE(excluded.sell_amt, lhb_signals.sell_amt),
                    net_amt = COALESCE(excluded.net_amt, lhb_signals.net_amt),
                    inst_count = COALESCE(excluded.inst_count, lhb_signals.inst_count),
                    concept_tags = COALESCE(excluded.concept_tags, lhb_signals.concept_tags)
            """, records)

    @staticmethod
    def _is_st(stock_name: str) -> bool:
        """判断是否为ST股票（含*ST、ST、S*ST等）。"""
        return "ST" in stock_name.upper()

    def query_lhb_signals(self, date: str = "") -> list[dict]:
        with self._get_conn() as conn:
            if date:
                rows = conn.execute(
                    "SELECT * FROM lhb_signals WHERE date = ? ORDER BY signal_type, net_amt DESC",
                    (date,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM lhb_signals ORDER BY date DESC, signal_type, net_amt DESC"
                ).fetchall()
            return [dict(r) for r in rows if not self._is_st(r["stock_name"])]

    def query_lhb_signals_range(self, start: str, end: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lhb_signals WHERE date BETWEEN ? AND ? ORDER BY date DESC",
                (start, end),
            ).fetchall()
            return [dict(r) for r in rows if not self._is_st(r["stock_name"])]

    def get_lhb_signal_dates(self) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM lhb_signals ORDER BY date DESC"
            ).fetchall()
            return [r["date"] for r in rows]

    def query_lhb_trading_desk(self, date: str, stock_code: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lhb_trading_desk WHERE date = ? AND stock_code = ? ORDER BY side, net_amt DESC",
                (date, stock_code),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- 龙虎榜股池 ----

    def sync_lhb_pool_signals(self, records: list[dict]):
        """信号同步阶段专用：插入新记录，冲突时只更新信号字段（名称/价格/板块），不碰跟踪数据。"""
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO lhb_pool
                    (signal_date, stock_code, stock_name, signal_type, entry_price,
                     concept_tags, d1_change, d3_change, d5_change, d10_change,
                     d20_change, d30_change, latest_price, latest_date, tracking_days)
                VALUES
                    (:signal_date, :stock_code, :stock_name, :signal_type, :entry_price,
                     :concept_tags, :d1_change, :d3_change, :d5_change, :d10_change,
                     :d20_change, :d30_change, :latest_price, :latest_date, :tracking_days)
                ON CONFLICT(signal_date, stock_code, signal_type) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    entry_price = COALESCE(excluded.entry_price, lhb_pool.entry_price),
                    concept_tags = COALESCE(excluded.concept_tags, lhb_pool.concept_tags),
                    updated_at = datetime('now','localtime')
            """, records)

    def upsert_lhb_pool(self, records: list[dict]):
        """更新股池记录（跟踪阶段）。冲突时用 COALESCE 保护已有数据不被 NULL 覆盖。"""
        with self._get_conn() as conn:
            conn.executemany("""
                INSERT INTO lhb_pool
                    (signal_date, stock_code, stock_name, signal_type, entry_price,
                     concept_tags, d1_change, d3_change, d5_change, d10_change,
                     d20_change, d30_change, latest_price, latest_date, tracking_days)
                VALUES
                    (:signal_date, :stock_code, :stock_name, :signal_type, :entry_price,
                     :concept_tags, :d1_change, :d3_change, :d5_change, :d10_change,
                     :d20_change, :d30_change, :latest_price, :latest_date, :tracking_days)
                ON CONFLICT(signal_date, stock_code, signal_type) DO UPDATE SET
                    stock_name = excluded.stock_name,
                    entry_price = COALESCE(excluded.entry_price, lhb_pool.entry_price),
                    d1_change = COALESCE(excluded.d1_change, lhb_pool.d1_change),
                    d3_change = COALESCE(excluded.d3_change, lhb_pool.d3_change),
                    d5_change = COALESCE(excluded.d5_change, lhb_pool.d5_change),
                    d10_change = COALESCE(excluded.d10_change, lhb_pool.d10_change),
                    d20_change = COALESCE(excluded.d20_change, lhb_pool.d20_change),
                    d30_change = COALESCE(excluded.d30_change, lhb_pool.d30_change),
                    latest_price = COALESCE(excluded.latest_price, lhb_pool.latest_price),
                    latest_date = COALESCE(excluded.latest_date, lhb_pool.latest_date),
                    concept_tags = COALESCE(excluded.concept_tags, lhb_pool.concept_tags),
                    tracking_days = CASE
                        WHEN excluded.tracking_days > lhb_pool.tracking_days THEN excluded.tracking_days
                        ELSE lhb_pool.tracking_days
                    END,
                    updated_at = datetime('now','localtime')
            """, records)

    def query_lhb_pool(self, signal_type: str = "") -> list[dict]:
        """查询股池，按 stock_code 合并去重，只返回最近 30 天内有上榜的股票。排除ST。"""
        with self._get_conn() as conn:
            where = "stock_code IN (SELECT DISTINCT stock_code FROM lhb_signals WHERE date >= date('now','-30 days'))"
            params: list = []
            if signal_type:
                where += " AND signal_type = ?"
                params.append(signal_type)

            sql = f"""
                SELECT
                    stock_code,
                    MIN(signal_date) AS signal_date,
                    MAX(stock_name) AS stock_name,
                    GROUP_CONCAT(DISTINCT signal_type) AS signal_types,
                    MIN(entry_price) AS entry_price,
                    MAX(concept_tags) AS concept_tags,
                    SUM(CASE WHEN d1_change IS NOT NULL THEN 1 ELSE 0 END) +
                    SUM(CASE WHEN d30_change IS NOT NULL THEN 1 ELSE 0 END) AS _dummy,
                    MIN(d1_change) AS d1_change,
                    MIN(d3_change) AS d3_change,
                    MIN(d5_change) AS d5_change,
                    MIN(d10_change) AS d10_change,
                    MIN(d20_change) AS d20_change,
                    MIN(d30_change) AS d30_change,
                    MAX(latest_price) AS latest_price,
                    MAX(latest_date) AS latest_date,
                    MAX(tracking_days) AS tracking_days
                FROM lhb_pool
                WHERE {where}
                GROUP BY stock_code
                ORDER BY signal_date DESC
            """
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows if not self._is_st(r["stock_name"])]

    def query_lhb_pool_tracking(self) -> list[dict]:
        """查询所有未完成跟踪的股池记录（tracking_days < 30），优先处理 tracking_days=0 的。"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lhb_pool WHERE tracking_days < 30 ORDER BY tracking_days ASC, signal_date DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- 连板统计 / 个股历史 / 回测 ----

    def query_streak_stats(self, days: int = 30, min_streak: int = 2) -> dict:
        """统计指定天数内连续上榜的股票。

        从最近交易日往前遍历，计算每只股票的连续上榜天数。
        rank_trend: 最近3个排名趋势 rising/stable/falling。
        is_dark_horse: 最早排名 >= 5 且最新排名 <= 3。
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        with self._get_conn() as conn:
            # 获取日期范围内所有交易日（降序）
            trade_dates = [
                r["date"] for r in conn.execute(
                    "SELECT DISTINCT date FROM stock_records "
                    "WHERE date BETWEEN ? AND ? ORDER BY date DESC",
                    (start_str, end_str),
                ).fetchall()
            ]
            if not trade_dates:
                return {"start_date": start_str, "end_date": end_str, "streaks": []}

            # 获取所有记录按 stock_code 分组
            rows = conn.execute(
                "SELECT date, rank, stock_code, stock_name, sector_tags, "
                "heat_value, price_change_pct "
                "FROM stock_records WHERE date BETWEEN ? AND ? ORDER BY date DESC",
                (start_str, end_str),
            ).fetchall()

        # 按 stock_code 分组
        code_map: dict[str, dict] = {}
        for row in rows:
            code = row["stock_code"]
            if code not in code_map:
                code_map[code] = {
                    "stock_code": code,
                    "stock_name": row["stock_name"],
                    "records": [],
                }
            code_map[code]["records"].append({
                "date": row["date"],
                "rank": row["rank"],
                "sector_tags": row["sector_tags"],
                "heat_value": row["heat_value"],
                "price_change_pct": row["price_change_pct"],
            })

        streaks = []
        for code, info in code_map.items():
            # 按日期降序排列（已在 SQL 中排序）
            recs = info["records"]
            rec_dates = {r["date"] for r in recs}
            rec_by_date = {r["date"]: r for r in recs}

            # 从最近交易日往前遍历，计算连续天数
            streak_days = 0
            for td in trade_dates:
                if td in rec_dates:
                    streak_days += 1
                else:
                    break

            if streak_days < min_streak:
                continue

            # 最近3个排名趋势
            recent_ranks = [rec_by_date[td]["rank"] for td in trade_dates[:3] if td in rec_by_date]
            rank_trend = "stable"
            if len(recent_ranks) >= 2:
                first, last = recent_ranks[0], recent_ranks[-1]
                if last > first:
                    rank_trend = "rising"
                elif last < first:
                    rank_trend = "falling"

            # 判断是否黑马（最早排名 >= 5 且最新排名 <= 3）
            streak_recs = [rec_by_date[td] for td in trade_dates[:streak_days] if td in rec_by_date]
            first_rank = streak_recs[-1]["rank"] if streak_recs else 0
            last_rank = streak_recs[0]["rank"] if streak_recs else 0
            is_dark_horse = first_rank >= 5 and last_rank <= 3

            # 解析 sector_tags JSON
            sector_tags_raw = recs[0].get("sector_tags", "[]")
            try:
                sector_tags = json.loads(sector_tags_raw) if sector_tags_raw else []
            except (json.JSONDecodeError, TypeError):
                sector_tags = []

            streak_dates = [td for td in trade_dates[:streak_days] if td in rec_by_date]
            streak_ranks = [rec_by_date[td]["rank"] for td in streak_dates]
            streak_heat = [rec_by_date[td].get("heat_value") for td in streak_dates]
            latest_change = None
            if streak_dates:
                latest_rec = rec_by_date[streak_dates[0]]
                latest_change = latest_rec.get("price_change_pct")

            streaks.append({
                "stock_code": code,
                "stock_name": info["stock_name"],
                "streak_days": streak_days,
                "dates": streak_dates,
                "ranks": streak_ranks,
                "heat_values": streak_heat,
                "rank_trend": rank_trend,
                "is_dark_horse": is_dark_horse,
                "latest_change": latest_change,
                "sector_tags": sector_tags,
            })

        streaks.sort(key=lambda x: x["streak_days"], reverse=True)
        return {"start_date": start_str, "end_date": end_str, "streaks": streaks}

    def query_stock_history(self, stock_code: str) -> dict:
        """查询单只股票的全部历史数据（stock_records + lhb_signals + lhb_trading_desk）。"""
        with self._get_conn() as conn:
            stock_rows = conn.execute(
                "SELECT * FROM stock_records WHERE stock_code = ? ORDER BY date DESC",
                (stock_code,),
            ).fetchall()

            signal_rows = conn.execute(
                "SELECT * FROM lhb_signals WHERE stock_code = ? ORDER BY date DESC",
                (stock_code,),
            ).fetchall()

            desk_rows = conn.execute(
                "SELECT * FROM lhb_trading_desk WHERE stock_code = ? "
                "ORDER BY date DESC, side, net_amt DESC",
                (stock_code,),
            ).fetchall()

        records = []
        stock_name = ""
        for r in stock_rows:
            d = dict(r)
            if not stock_name:
                stock_name = d["stock_name"]
            if d.get("sector_tags"):
                try:
                    d["sector_tags"] = json.loads(d["sector_tags"])
                except (json.JSONDecodeError, TypeError):
                    pass
            records.append(d)

        signals = []
        for r in signal_rows:
            d = dict(r)
            if not stock_name:
                stock_name = d["stock_name"]
            if d.get("concept_tags"):
                try:
                    d["concept_tags"] = json.loads(d["concept_tags"])
                except (json.JSONDecodeError, TypeError):
                    pass
            signals.append(d)

        desks = []
        for r in desk_rows:
            d = dict(r)
            if not stock_name:
                stock_name = d["stock_name"]
            desks.append(d)

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "records": records,
            "lhb_signals": signals,
            "lhb_trading_desk": desks,
        }

    def query_backtest(self, signal_type: str = "", months: int = 3,
                       group_by: str = "month") -> dict:
        """龙虎榜回测统计。

        horizon_stats: 各持仓天数的胜率和平均涨幅。
        period_stats: 按 month 或 sector 分组的统计。
        sector_stats: 按概念板块分组的统计。
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=months * 30)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        with self._get_conn() as conn:
            where = "signal_date BETWEEN ? AND ?"
            params: list = [start_str, end_str]
            if signal_type:
                where += " AND signal_type = ?"
                params.append(signal_type)

            rows = conn.execute(
                f"SELECT * FROM lhb_pool WHERE {where} ORDER BY signal_date DESC",
                params,
            ).fetchall()
            pool_records = [dict(r) for r in rows]

        total_signals = len(pool_records)
        if total_signals == 0:
            return {
                "signal_type": signal_type,
                "total_signals": 0,
                "overall_win_rate": None,
                "overall_avg_change": None,
                "period_stats": [],
                "horizon_stats": [],
                "sector_stats": [],
            }

        # horizon_stats: 对各持仓天数计算胜率和平均涨幅
        horizon_fields = ["d1_change", "d3_change", "d5_change", "d10_change", "d20_change", "d30_change"]
        horizon_stats = []
        for field in horizon_fields:
            values = [r[field] for r in pool_records if r.get(field) is not None]
            if values:
                wins = sum(1 for v in values if v > 0)
                horizon_stats.append({
                    "horizon": field.replace("_change", ""),
                    "total": len(values),
                    "win_count": wins,
                    "win_rate": round(wins / len(values), 4),
                    "avg_change": round(sum(values) / len(values), 4),
                })
            else:
                horizon_stats.append({
                    "horizon": field.replace("_change", ""),
                    "total": 0,
                    "win_count": 0,
                    "win_rate": None,
                    "avg_change": None,
                })

        # overall_win_rate/overall_avg_change 用 d5_change
        d5_values = [r["d5_change"] for r in pool_records if r.get("d5_change") is not None]
        if d5_values:
            d5_wins = sum(1 for v in d5_values if v > 0)
            overall_win_rate = round(d5_wins / len(d5_values), 4)
            overall_avg_change = round(sum(d5_values) / len(d5_values), 4)
        else:
            overall_win_rate = None
            overall_avg_change = None

        # period_stats: 按月分组
        period_stats = []
        month_groups: dict[str, list] = {}
        for r in pool_records:
            month_key = r["signal_date"][:7]
            if month_key not in month_groups:
                month_groups[month_key] = []
            month_groups[month_key].append(r)

        for month_key in sorted(month_groups.keys()):
            group = month_groups[month_key]
            changes = [r["d5_change"] for r in group if r.get("d5_change") is not None]
            if changes:
                wins = sum(1 for v in changes if v > 0)
                sorted_changes = sorted(changes)
                period_stats.append({
                    "period": month_key,
                    "count": len(group),
                    "win_rate": round(wins / len(changes), 4),
                    "avg_change": round(sum(changes) / len(changes), 4),
                    "median_change": sorted_changes[len(sorted_changes) // 2],
                    "max_change": max(changes),
                    "min_change": min(changes),
                })
            else:
                period_stats.append({
                    "period": month_key,
                    "count": len(group),
                    "win_rate": None,
                    "avg_change": None,
                    "median_change": None,
                    "max_change": None,
                    "min_change": None,
                })

        # sector_stats: 按概念板块分组
        sector_stats = []
        sector_groups: dict[str, list] = {}
        for r in pool_records:
            tags_raw = r.get("concept_tags", "[]")
            try:
                tags = json.loads(tags_raw) if tags_raw else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            for tag in tags:
                if tag not in sector_groups:
                    sector_groups[tag] = []
                sector_groups[tag].append(r)

        for tag in sorted(sector_groups.keys()):
            group = sector_groups[tag]
            changes = [r["d5_change"] for r in group if r.get("d5_change") is not None]
            if changes:
                wins = sum(1 for v in changes if v > 0)
                sector_stats.append({
                    "sector": tag,
                    "count": len(group),
                    "win_rate": round(wins / len(changes), 4),
                    "avg_change": round(sum(changes) / len(changes), 4),
                })
            else:
                sector_stats.append({
                    "sector": tag,
                    "count": len(group),
                    "win_rate": None,
                    "avg_change": None,
                })

        return {
            "signal_type": signal_type,
            "total_signals": total_signals,
            "overall_win_rate": overall_win_rate,
            "overall_avg_change": overall_avg_change,
            "period_stats": period_stats,
            "horizon_stats": horizon_stats,
            "sector_stats": sector_stats,
        }
