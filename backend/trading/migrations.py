"""交易模块独立迁移系统。

设计原则(文档第 10 章):
- 迁移必须可重复执行。
- 不依赖"捕获 ALTER TABLE 异常即忽略"的隐式方式。
- 用显式 trade_migrations 表记录已应用版本。

Phase 1 只创建 trade_* 表(全部用 CREATE TABLE IF NOT EXISTS),后续新增列通过
新增 version 的 ALTER 语句实现。
"""
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger("trading.migrations")


def _set_pragmas(conn: sqlite3.Connection) -> None:
    """开启 WAL、外键、busy_timeout(文档 10.1)。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


# 每个迁移是一个 (version, name, sql_statements) 元组
# sql 用 list[str] 是为了在失败时能定位具体语句
MIGRATION_VERSIONS: list[dict] = [
    {
        "version": 1,
        "name": "initial_trade_tables",
        "description": "创建 Phase 1 所需的 trade_* 表(股池/日线/数据问题/任务)",
    },
    {
        "version": 2,
        "name": "phase2_account_tables",
        "description": "Phase 2 账户/持仓/成交/净值快照 + Phase 3 预建空表(保证 FK 完整性)",
    },
    {
        "version": 3,
        "name": "phase3_plan_indexes",
        "description": "Phase 3 计划表索引(FK 由应用层校验,SQLite 不支持事后加 FK)",
    },
]

# 版本 1 的完整 DDL(来自设计文档第 10.2 章,只取 Phase 1 需要的表)
_MIGRATION_1_SQL = [
    """CREATE TABLE IF NOT EXISTS trade_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS trade_stock_pool_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pool_name TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        items_hash TEXT NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(pool_name, version_no),
        UNIQUE(pool_name, items_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_stock_pool_items (
        pool_version_id INTEGER NOT NULL,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        sector_name TEXT,
        manual_blacklist INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        PRIMARY KEY (pool_version_id, stock_code),
        FOREIGN KEY (pool_version_id) REFERENCES trade_stock_pool_versions(id)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_daily_bars (
        stock_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        amount REAL,
        pre_close REAL,
        change_pct REAL,
        adjust_factor REAL,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        checksum TEXT NOT NULL,
        PRIMARY KEY (stock_code, trade_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trade_daily_bars_date ON trade_daily_bars(trade_date)",
    """CREATE TABLE IF NOT EXISTS trade_data_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        stock_code TEXT,
        trade_date TEXT,
        severity TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','BLOCKING')),
        issue_code TEXT NOT NULL,
        message TEXT NOT NULL,
        source TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        resolved_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS trade_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type TEXT NOT NULL,
        job_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
        progress REAL NOT NULL DEFAULT 0,
        request_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT,
        error_json TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        started_at TEXT,
        finished_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS trade_job_locks (
        lock_key TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        expires_at TEXT NOT NULL
    )""",
]

# 版本 2 的完整 DDL(Phase 2 主表 + Phase 3 预建空表)
# 顺序:Phase 3 预建空表(trade_strategy_versions/trade_plan_runs/trade_plan_items)
# 必须在 trade_executions 之前,因为 trade_executions.plan_item_id 有 FK 引用 trade_plan_items。
# trade_stock_pool_versions Phase 1 已建,IF NOT EXISTS 保证幂等。
_MIGRATION_2_SQL = [
    # ---- Phase 3 预建空表(必须在 trade_executions/positions 之前,FK 依赖) ----
    """CREATE TABLE IF NOT EXISTS trade_strategy_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_code TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        name TEXT NOT NULL,
        params_json TEXT NOT NULL,
        params_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('DRAFT','ACTIVE','RETIRED')),
        created_at TEXT NOT NULL,
        activated_at TEXT,
        UNIQUE(strategy_code, version_no),
        UNIQUE(params_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_stock_pool_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pool_name TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        items_hash TEXT NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(pool_name, version_no),
        UNIQUE(pool_name, items_hash)
    )""",  # 注:此表 Phase 1 已建,IF NOT EXISTS 保证幂等
    """CREATE TABLE IF NOT EXISTS trade_plan_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_key TEXT NOT NULL UNIQUE,
        account_id INTEGER NOT NULL,
        signal_date TEXT NOT NULL,
        target_trade_date TEXT NOT NULL,
        stock_pool_version_id INTEGER NOT NULL,
        strategy_version_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        market_regime TEXT,
        market_score INTEGER,
        recommended_exposure REAL,
        account_snapshot_json TEXT NOT NULL,
        data_snapshot_hash TEXT NOT NULL,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        error_json TEXT,
        created_at TEXT NOT NULL,
        published_at TEXT,
        superseded_by_id INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS trade_plan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_run_id INTEGER NOT NULL,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        action TEXT NOT NULL,
        score REAL,
        rank_no INTEGER,
        trigger_price REAL,
        do_not_chase_price REAL,
        stop_price REAL,
        target_2r_price REAL,
        suggested_quantity INTEGER NOT NULL DEFAULT 0,
        suggested_position_pct REAL NOT NULL DEFAULT 0,
        risk_amount REAL NOT NULL DEFAULT 0,
        risk_pct REAL NOT NULL DEFAULT 0,
        rule_hits_json TEXT NOT NULL DEFAULT '[]',
        rule_misses_json TEXT NOT NULL DEFAULT '[]',
        invalidation_reason TEXT,
        execution_status TEXT NOT NULL DEFAULT 'PENDING',
        created_at TEXT NOT NULL,
        UNIQUE(plan_run_id, stock_code)
    )""",
    # ---- Phase 2 主表 ----
    """CREATE TABLE IF NOT EXISTS trade_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        initial_equity REAL NOT NULL CHECK (initial_equity > 0),
        cash_balance REAL NOT NULL CHECK (cash_balance >= 0),
        risk_per_trade REAL NOT NULL DEFAULT 0.005,
        max_single_position REAL NOT NULL DEFAULT 0.15,
        max_total_exposure REAL NOT NULL DEFAULT 0.60,
        max_sector_exposure REAL NOT NULL DEFAULT 0.30,
        max_positions INTEGER NOT NULL DEFAULT 5,
        max_drawdown_limit REAL NOT NULL DEFAULT 0.08,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS trade_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        stock_code TEXT NOT NULL,
        stock_name TEXT,
        quantity INTEGER NOT NULL CHECK (quantity >= 0),
        available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
        average_cost REAL NOT NULL CHECK (average_cost >= 0),
        initial_stop REAL,
        trailing_stop REAL,
        opened_at TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(account_id, stock_code),
        FOREIGN KEY (account_id) REFERENCES trade_accounts(id)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        plan_item_id INTEGER,
        stock_code TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
        trade_date TEXT NOT NULL,
        price REAL NOT NULL CHECK (price > 0),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        commission REAL NOT NULL DEFAULT 0,
        tax REAL NOT NULL DEFAULT 0,
        note TEXT,
        client_execution_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        UNIQUE(client_execution_id),
        FOREIGN KEY (account_id) REFERENCES trade_accounts(id),
        FOREIGN KEY (plan_item_id) REFERENCES trade_plan_items(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trade_executions_account_date ON trade_executions(account_id, trade_date)",
    """CREATE TABLE IF NOT EXISTS trade_equity_snapshots (
        account_id INTEGER NOT NULL,
        trade_date TEXT NOT NULL,
        cash REAL NOT NULL,
        market_value REAL NOT NULL,
        total_equity REAL NOT NULL,
        exposure REAL NOT NULL,
        peak_equity REAL NOT NULL,
        drawdown REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (account_id, trade_date),
        FOREIGN KEY (account_id) REFERENCES trade_accounts(id)
    )""",
    """CREATE TABLE IF NOT EXISTS trade_audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT,
        before_json TEXT,
        after_json TEXT,
        request_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    )""",
]

# Phase 3: 计划表索引。
# 注:SQLite 不支持对已存在的表事后添加 FK 约束(ALTER TABLE ADD CONSTRAINT),
# Phase 2 预建 plan_runs/plan_items 时省略了 FK,这里只补索引;FK 完整性由应用层校验。
_MIGRATION_3_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_trade_plan_runs_signal_date ON trade_plan_runs(signal_date, status)",
]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _set_pragmas(conn)
    return conn


def run_migrations(db_path: str) -> None:
    """运行所有未应用的迁移。

    幂等:重复调用只应用未记录在 trade_migrations 中的版本。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # 注意:sqlite3.Connection 的 __exit__ 只提交/回滚事务,不会关闭连接。
    # 必须显式 close(),否则 Windows 上文件锁会阻止外部删除。
    conn = _connect(db_path)
    try:
        # 确保 trade_migrations 表存在(首次迁移用它记录自己)
        conn.execute(_MIGRATION_1_SQL[0])
        applied = {
            r["version"] for r in conn.execute(
                "SELECT version FROM trade_migrations"
            ).fetchall()
        }

        if 1 not in applied:
            for stmt in _MIGRATION_1_SQL[1:]:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO trade_migrations (version, name) VALUES (?, ?)",
                (1, "initial_trade_tables"),
            )
            conn.commit()
            logger.info("trading migration v1 applied")

        if 2 not in applied:
            for stmt in _MIGRATION_2_SQL:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO trade_migrations (version, name) VALUES (?, ?)",
                (2, "phase2_account_tables"),
            )
            conn.commit()
            logger.info("trading migration v2 applied")

        if 3 not in applied:
            for stmt in _MIGRATION_3_SQL:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO trade_migrations (version, name) VALUES (?, ?)",
                (3, "phase3_plan_indexes"),
            )
            conn.commit()
            logger.info("trading migration v3 applied")
    finally:
        conn.close()
