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
    finally:
        conn.close()
