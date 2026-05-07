import sqlite3
from pathlib import Path
from backend.config import settings


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
                    dept_name TEXT NOT NULL,
                    buy_amt REAL,
                    sell_amt REAL,
                    net_amt REAL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(date, stock_code, side, dept_name)
                )
            """)

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

    def insert_record(self, record: dict):
        with self._get_conn() as conn:
            conn.execute("""
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
            """, record)

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
                    (date, stock_code, stock_name, side, dept_name, buy_amt, sell_amt, net_amt)
                VALUES
                    (:date, :stock_code, :stock_name, :side, :dept_name, :buy_amt, :sell_amt, :net_amt)
                ON CONFLICT(date, stock_code, side, dept_name) DO UPDATE SET
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
            return [dict(r) for r in rows]

    def query_lhb_signals_range(self, start: str, end: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lhb_signals WHERE date BETWEEN ? AND ? ORDER BY date DESC",
                (start, end),
            ).fetchall()
            return [dict(r) for r in rows]

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
