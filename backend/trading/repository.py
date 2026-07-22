"""交易模块 Repository:仅负责 trade_* 表读写。

设计(文档第 6、10 章):
- 不向现有 Database 类堆叠 SQL。
- 每次操作新建连接(与现有 Database._get_conn 一致)。
- 写操作使用稳定哈希(便于幂等)。

Windows 注意:sqlite3.Connection 的 __exit__ 只 commit 不 close。
对于需要释放文件锁的测试场景,本类的方法都使用短连接并在 finally 中 close,
避免 test fixture 清理时遇到 PermissionError。
"""
import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .domain import DailyBar
from .migrations import _set_pragmas


class TradingRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        _set_pragmas(conn)
        return conn

    # ---- 股票池 ----

    @staticmethod
    def _items_hash(items: list[dict]) -> str:
        """对 items 计算稳定哈希(键排序 + 小写化代码)。"""
        canonical = sorted(
            [{"stock_code": it["stock_code"].upper(),
              "stock_name": (it.get("stock_name") or "").strip()}
             for it in items],
            key=lambda x: x["stock_code"],
        )
        return hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:16]

    def create_stock_pool_version(self, pool_name: str, items: list[dict],
                                  source: str = "text") -> dict:
        """创建股票池版本。

        相同 items_hash 返回已存在版本(幂等);items 变化时 version_no +1。
        """
        if not items:
            raise ValueError("股票池不能为空")
        items_hash = self._items_hash(items)
        conn = self._conn()
        try:
            # 1. 查重
            existing = conn.execute(
                "SELECT id, version_no FROM trade_stock_pool_versions "
                "WHERE pool_name = ? AND items_hash = ?",
                (pool_name, items_hash),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "version_no": existing["version_no"],
                        "items_hash": items_hash, "items_count": len(items), "reused": True}

            # 2. 计算新版本号
            max_ver = conn.execute(
                "SELECT MAX(version_no) AS m FROM trade_stock_pool_versions WHERE pool_name = ?",
                (pool_name,),
            ).fetchone()
            next_ver = (max_ver["m"] or 0) + 1

            cur = conn.execute(
                "INSERT INTO trade_stock_pool_versions (pool_name, version_no, items_hash, source) "
                "VALUES (?, ?, ?, ?)",
                (pool_name, next_ver, items_hash, source),
            )
            version_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO trade_stock_pool_items "
                "(pool_version_id, stock_code, stock_name, sector_name, note) "
                "VALUES (?, ?, ?, ?, ?)",
                [(version_id, it["stock_code"], it.get("stock_name"),
                  it.get("sector_name"), it.get("note", ""))
                 for it in items],
            )
            conn.commit()
            return {"id": version_id, "version_no": next_ver,
                    "items_hash": items_hash, "items_count": len(items), "reused": False}
        finally:
            conn.close()

    def get_stock_pool_version(self, version_id: int) -> dict | None:
        conn = self._conn()
        try:
            ver = conn.execute(
                "SELECT * FROM trade_stock_pool_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            if not ver:
                return None
            items = conn.execute(
                "SELECT stock_code, stock_name, sector_name, manual_blacklist, note "
                "FROM trade_stock_pool_items WHERE pool_version_id = ? "
                "ORDER BY stock_code",
                (version_id,),
            ).fetchall()
            return {
                "id": ver["id"], "pool_name": ver["pool_name"],
                "version_no": ver["version_no"], "items_hash": ver["items_hash"],
                "source": ver["source"], "created_at": ver["created_at"],
                "items": [dict(it) for it in items],
            }
        finally:
            conn.close()

    def list_stock_pool_versions(self, pool_name: str | None = None) -> list[dict]:
        conn = self._conn()
        try:
            if pool_name:
                rows = conn.execute(
                    "SELECT v.id, v.pool_name, v.version_no, v.source, v.created_at, "
                    "       (SELECT COUNT(*) FROM trade_stock_pool_items i WHERE i.pool_version_id = v.id) AS items_count "
                    "FROM trade_stock_pool_versions v WHERE v.pool_name = ? "
                    "ORDER BY v.version_no DESC",
                    (pool_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT v.id, v.pool_name, v.version_no, v.source, v.created_at, "
                    "       (SELECT COUNT(*) FROM trade_stock_pool_items i WHERE i.pool_version_id = v.id) AS items_count "
                    "FROM trade_stock_pool_versions v ORDER BY v.version_no DESC",
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_latest_pool_codes(self, pool_name: str = "default") -> list[str]:
        """取最新版本的股票代码列表(用于行情回补)。"""
        conn = self._conn()
        try:
            ver = conn.execute(
                "SELECT id FROM trade_stock_pool_versions WHERE pool_name = ? "
                "ORDER BY version_no DESC LIMIT 1",
                (pool_name,),
            ).fetchone()
            if not ver:
                return []
            rows = conn.execute(
                "SELECT stock_code FROM trade_stock_pool_items WHERE pool_version_id = ?",
                (ver["id"],),
            ).fetchall()
            return [r["stock_code"] for r in rows]
        finally:
            conn.close()

    # ---- 日线行情 ----

    @staticmethod
    def _bar_checksum(bar: DailyBar) -> str:
        s = f"{bar.code}|{bar.trade_date}|{bar.open}|{bar.high}|{bar.low}|{bar.close}|{bar.volume}"
        return hashlib.md5(s.encode()).hexdigest()[:12]

    def upsert_daily_bars(self, bars: list[DailyBar]) -> int:
        if not bars:
            return 0
        rows = []
        for b in bars:
            rows.append((
                b.code, b.trade_date.isoformat(), b.open, b.high, b.low, b.close,
                b.volume, b.amount, None, b.change_pct, b.adjust_factor,
                b.source, self._bar_checksum(b),
            ))
        conn = self._conn()
        try:
            conn.executemany(
                "INSERT INTO trade_daily_bars "
                "(stock_code, trade_date, open, high, low, close, volume, amount, "
                " pre_close, change_pct, adjust_factor, source, checksum) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(stock_code, trade_date) DO UPDATE SET "
                "  open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                "  volume=excluded.volume, amount=COALESCE(excluded.amount, trade_daily_bars.amount), "
                "  change_pct=excluded.change_pct, adjust_factor=excluded.adjust_factor, "
                "  source=excluded.source, checksum=excluded.checksum, fetched_at=datetime('now','localtime')",
                rows,
            )
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_daily_bars(self, codes: list[str], start: date, end: date) -> list[dict]:
        if not codes:
            return []
        placeholders = ",".join("?" * len(codes))
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_daily_bars WHERE stock_code IN ({placeholders}) "
                f"AND trade_date BETWEEN ? AND ? ORDER BY stock_code, trade_date",
                (*codes, start.isoformat(), end.isoformat()),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_latest_bar_date(self, code: str) -> date | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT MAX(trade_date) AS d FROM trade_daily_bars WHERE stock_code = ?",
                (code,),
            ).fetchone()
            if row and row["d"]:
                return date.fromisoformat(row["d"])
            return None
        finally:
            conn.close()

    def pool_missing_codes(self, pool_name: str, target_date: date) -> list[str]:
        """股票池中在 target_date 缺失行情的代码。"""
        codes = self.get_latest_pool_codes(pool_name)
        if not codes:
            return []
        target_str = target_date.isoformat()
        placeholders = ",".join("?" * len(codes))
        conn = self._conn()
        try:
            have = {
                r["stock_code"] for r in conn.execute(
                    f"SELECT DISTINCT stock_code FROM trade_daily_bars "
                    f"WHERE stock_code IN ({placeholders}) AND trade_date = ?",
                    (*codes, target_str),
                ).fetchall()
            }
        finally:
            conn.close()
        return [c for c in codes if c not in have]

    # ---- 数据问题 ----

    def create_data_issue(self, *, severity: str, issue_code: str, message: str,
                          stock_code: str | None = None, trade_date: str | None = None,
                          source: str | None = None, details: dict | None = None,
                          run_id: int | None = None) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO trade_data_issues "
                "(run_id, stock_code, trade_date, severity, issue_code, message, source, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, stock_code, trade_date, severity, issue_code, message, source,
                 json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def list_data_issues(self, trade_date: str | None = None,
                         severity: str | None = None) -> list[dict]:
        clauses = []
        params: list = []
        if trade_date:
            clauses.append("trade_date = ?")
            params.append(trade_date)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_data_issues{where} ORDER BY created_at DESC",
                params,
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["details"] = json.loads(d.pop("details_json"))
                except (ValueError, KeyError):
                    d["details"] = {}
                result.append(d)
            return result
        finally:
            conn.close()

    # ---- 任务(Phase 1 仅最小实现,供数据更新使用) ----

    def create_job(self, job_type: str, job_key: str, request: dict) -> int:
        conn = self._conn()
        try:
            # 幂等:同 job_key 已存在且未完成则复用
            existing = conn.execute(
                "SELECT id, status FROM trade_jobs WHERE job_key = ? "
                "ORDER BY id DESC LIMIT 1",
                (job_key,),
            ).fetchone()
            if existing and existing["status"] in ("QUEUED", "RUNNING"):
                return existing["id"]
            cur = conn.execute(
                "INSERT INTO trade_jobs (job_type, job_key, status, request_json) "
                "VALUES (?, ?, 'QUEUED', ?)",
                (job_type, job_key, json.dumps(request, ensure_ascii=False)),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_job(self, job_id: int, *, status: str, progress: float = 0,
                   result: dict | None = None, error: dict | None = None) -> None:
        sets = ["status = ?", "progress = ?"]
        params: list = [status, progress]
        if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            sets.append("finished_at = datetime('now','localtime')")
        if result is not None:
            sets.append("result_json = ?")
            params.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            sets.append("error_json = ?")
            params.append(json.dumps(error, ensure_ascii=False))
        if status == "RUNNING":
            sets.append("started_at = datetime('now','localtime')")
        params.append(job_id)
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE trade_jobs SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
        finally:
            conn.close()

    def get_job(self, job_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["request"] = json.loads(d.pop("request_json") or "{}")
            except ValueError:
                d["request"] = {}
            try:
                d["result"] = json.loads(d.pop("result_json") or "null")
            except ValueError:
                d["result"] = None
            try:
                d["error"] = json.loads(d.pop("error_json") or "null")
            except ValueError:
                d["error"] = None
            return d
        finally:
            conn.close()
