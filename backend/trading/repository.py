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
from datetime import date, datetime, timezone
from pathlib import Path

from .domain import DailyBar, TradeDay
from .errors import ExecutionIdempotencyConflictError
from .migrations import _set_pragmas


def _db_timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(sep=" ", timespec="seconds")


def _db_lease_timestamp(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(sep=" ", timespec="microseconds")


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
    def _canonical_pool_items(items: list[dict]) -> list[dict]:
        return sorted(
            [{"stock_code": it["stock_code"].upper(),
              "stock_name": (it.get("stock_name") or "").strip()}
             for it in items],
            key=lambda x: x["stock_code"],
        )

    @classmethod
    def _items_hash(cls, items: list[dict]) -> str:
        """对 items 计算稳定哈希(键排序 + 大写化代码)。"""
        canonical = cls._canonical_pool_items(items)
        return hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:16]

    @classmethod
    def _symbol_repair_hash(
        cls, items: list[dict], old_version_id: int
    ) -> str:
        """修复身份包含来源旧版本，避免复用同内容的历史版本。"""
        identity = {
            "items": cls._canonical_pool_items(items),
            "old_version_id": old_version_id,
        }
        return hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()
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
                "is_usable": bool(ver["is_usable"]),
                "invalid_reason": ver["invalid_reason"],
                "items": [dict(it) for it in items],
            }
        finally:
            conn.close()

    def apply_symbol_repair_batch(self, repairs: list[dict]) -> list[dict]:
        """在单一事务中创建全部修复版本、封存旧版本并记录阻断问题。"""
        if not repairs:
            return []
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            prepared = []
            for repair in repairs:
                old_version_id = int(repair["old_version_id"])
                old = conn.execute(
                    "SELECT id, pool_name, version_no, is_usable "
                    "FROM trade_stock_pool_versions WHERE id=?",
                    (old_version_id,),
                ).fetchone()
                if old is None:
                    raise ValueError(f"股票池版本 {old_version_id} 不存在")
                if not old["is_usable"]:
                    raise RuntimeError(f"股票池版本 {old_version_id} 已失效")
                corrected_items = repair["corrected_items"]
                if not corrected_items:
                    raise ValueError("修复后的股票池不能为空")
                prepared.append((old, repair, corrected_items))

            # 同一池内按历史版本从老到新生成，保证最新污染源的修复成为当前版本。
            prepared.sort(
                key=lambda entry: (entry[0]["version_no"], entry[0]["id"])
            )
            results = []
            for old, repair, corrected_items in prepared:
                pool_name = old["pool_name"]
                old_version_id = old["id"]
                items_hash = self._symbol_repair_hash(
                    corrected_items, old_version_id
                )
                existing = conn.execute(
                    "SELECT id FROM trade_stock_pool_versions "
                    "WHERE pool_name=? AND items_hash=?",
                    (pool_name, items_hash),
                ).fetchone()
                if existing is not None:
                    raise RuntimeError(
                        f"股票池版本 {old_version_id} 已存在对应修复版本"
                    )
                max_version = conn.execute(
                    "SELECT MAX(version_no) AS m FROM trade_stock_pool_versions "
                    "WHERE pool_name=?",
                    (pool_name,),
                ).fetchone()
                next_version = (max_version["m"] or 0) + 1
                inserted = conn.execute(
                    "INSERT INTO trade_stock_pool_versions "
                    "(pool_name, version_no, items_hash, source) "
                    "VALUES (?, ?, ?, 'symbol_repair')",
                    (pool_name, next_version, items_hash),
                )
                new_version_id = inserted.lastrowid
                conn.executemany(
                    "INSERT INTO trade_stock_pool_items "
                    "(pool_version_id, stock_code, stock_name, sector_name, "
                    " manual_blacklist, note) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            new_version_id,
                            item["stock_code"],
                            item.get("stock_name"),
                            item.get("sector_name"),
                            int(bool(item.get("manual_blacklist", 0))),
                            item.get("note", ""),
                        )
                        for item in corrected_items
                    ],
                )
                invalidated = conn.execute(
                    "UPDATE trade_stock_pool_versions "
                    "SET is_usable=0, invalid_reason='SYMBOL_MARKET_MISMATCH' "
                    "WHERE id=? AND is_usable=1",
                    (old_version_id,),
                )
                if invalidated.rowcount != 1:
                    raise RuntimeError(
                        f"股票池版本 {old_version_id} 失效标记失败"
                    )
                details = {
                    "old_version_id": old_version_id,
                    "new_version_id": new_version_id,
                }
                conn.execute(
                    "INSERT INTO trade_data_issues "
                    "(severity, issue_code, message, source, details_json) "
                    "VALUES ('BLOCKING', 'SYMBOL_MARKET_MISMATCH', ?, "
                    "        'data_repair', ?)",
                    (
                        repair["message"],
                        json.dumps(details, ensure_ascii=False, sort_keys=True),
                    ),
                )
                results.append({
                    **details,
                    "version_no": next_version,
                    "items_hash": items_hash,
                    "source": "symbol_repair",
                })
            conn.commit()
            return results
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_stock_pool_versions(
        self,
        pool_name: str | None = None,
        *,
        usable_only: bool = False,
    ) -> list[dict]:
        clauses = []
        params: list = []
        if pool_name:
            clauses.append("v.pool_name = ?")
            params.append(pool_name)
        if usable_only:
            clauses.append("v.is_usable = 1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT v.id, v.pool_name, v.version_no, v.source, v.created_at, "
                "       v.is_usable, v.invalid_reason, "
                "       (SELECT COUNT(*) FROM trade_stock_pool_items i "
                "        WHERE i.pool_version_id = v.id) AS items_count "
                f"FROM trade_stock_pool_versions v{where} "
                "ORDER BY v.version_no DESC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_stock_pool_unusable(self, version_id: int, reason: str) -> bool:
        conn = self._conn()
        try:
            updated = conn.execute(
                "UPDATE trade_stock_pool_versions "
                "SET is_usable=0, invalid_reason=? "
                "WHERE id=? AND is_usable=1",
                (reason, version_id),
            )
            conn.commit()
            return updated.rowcount == 1
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

    # ---- 任务 ----

    @staticmethod
    def _decode_job_row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        job = dict(row)
        for source, target, fallback in (
            ("request_json", "request", {}),
            ("result_json", "result", None),
            ("error_json", "error", None),
        ):
            raw = job.pop(source, None)
            try:
                job[target] = json.loads(raw) if raw else fallback
            except ValueError:
                job[target] = fallback
        return job

    def create_job(self, job_type: str, job_key: str, request: dict) -> int:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM trade_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if existing:
                conn.commit()
                return existing["id"]
            cursor = conn.execute(
                "INSERT INTO trade_jobs "
                "(job_type, job_key, status, request_json) "
                "VALUES (?, ?, 'QUEUED', ?)",
                (job_type, job_key, json.dumps(request, ensure_ascii=False)),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- 交易日历 ----

    def upsert_trade_calendar(
        self, days: list[TradeDay], *, source: str
    ) -> int:
        if not days:
            return 0
        rows = [
            (day.date.isoformat(), day.exchange, int(day.is_open), source)
            for day in days
        ]
        conn = self._conn()
        try:
            conn.executemany(
                "INSERT INTO trade_calendar "
                "(trade_date, exchange, is_open, source) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(trade_date, exchange) DO UPDATE SET "
                "is_open=excluded.is_open, source=excluded.source, "
                "fetched_at=datetime('now')",
                rows,
            )
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_trade_day(
        self, trade_date: date, exchange: str = "SSE"
    ) -> TradeDay | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT trade_date, exchange, is_open FROM trade_calendar "
                "WHERE trade_date=? AND exchange=?",
                (trade_date.isoformat(), exchange),
            ).fetchone()
            if row is None:
                return None
            return TradeDay(
                date=date.fromisoformat(row["trade_date"]),
                is_open=bool(row["is_open"]),
                exchange=row["exchange"],
            )
        finally:
            conn.close()

    def get_job_by_key(self, job_key: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            return self._decode_job_row(row)
        finally:
            conn.close()

    def claim_next_job(
        self,
        *,
        owner_id: str,
        now: datetime,
        max_attempts: int = 3,
    ) -> dict | None:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, error_json FROM trade_jobs "
                "WHERE status = 'QUEUED' AND attempts < ? "
                "ORDER BY created_at, id LIMIT 1",
                (max_attempts,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            updated = conn.execute(
                "UPDATE trade_jobs SET status='RUNNING', "
                "attempts=attempts+1, progress=0, started_at=?, "
                "finished_at=NULL, error_json=NULL "
                "WHERE id=? AND status='QUEUED'",
                (_db_timestamp(now), row["id"]),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return None
            claimed = conn.execute(
                "SELECT * FROM trade_jobs WHERE id=?", (row["id"],)
            ).fetchone()
            conn.commit()
            result = self._decode_job_row(claimed)
            result["claimed_by"] = owner_id
            try:
                result["previous_error"] = json.loads(
                    row["error_json"] or "null"
                )
            except (TypeError, ValueError):
                result["previous_error"] = None
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_job(
        self,
        job_id: int,
        *,
        result: dict,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
    ) -> None:
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE trade_jobs SET status='SUCCEEDED', progress=1, "
                "result_json=?, error_json=NULL, finished_at=datetime('now') "
                "WHERE id=? AND status='RUNNING' AND attempts=? "
                "AND EXISTS ("
                "SELECT 1 FROM trade_job_locks "
                "WHERE lock_key=? AND owner_id=? AND expires_at > ?"
                ")",
                (
                    json.dumps(result, ensure_ascii=False),
                    job_id,
                    expected_attempt,
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    f"任务 {job_id} 没有当前 worker 的有效 execution lease"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def retry_or_fail_job(
        self,
        job_id: int,
        *,
        error: dict,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
        max_attempts: int = 3,
    ) -> str:
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts FROM trade_jobs "
                "WHERE id=? AND status='RUNNING' AND attempts=? "
                "AND EXISTS ("
                "SELECT 1 FROM trade_job_locks "
                "WHERE lock_key=? AND owner_id=? AND expires_at > ?"
                ")",
                (
                    job_id,
                    expected_attempt,
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                ),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"任务 {job_id} 没有当前 worker 的有效 execution lease"
                )
            status = "FAILED" if row["attempts"] >= max_attempts else "QUEUED"
            finished_sql = "datetime('now')" if status == "FAILED" else "NULL"
            updated = conn.execute(
                f"UPDATE trade_jobs SET status=?, progress=0, error_json=?, "
                f"finished_at={finished_sql}, "
                "started_at=CASE WHEN ?='QUEUED' THEN NULL ELSE started_at END "
                "WHERE id=? AND status='RUNNING' AND attempts=? "
                "AND EXISTS ("
                "SELECT 1 FROM trade_job_locks "
                "WHERE lock_key=? AND owner_id=? AND expires_at > ?"
                ")",
                (
                    status,
                    json.dumps(error, ensure_ascii=False),
                    status,
                    job_id,
                    expected_attempt,
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    f"任务 {job_id} 没有当前 worker 的有效 execution lease"
                )
            conn.commit()
            return status
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def requeue_stale_jobs(
        self, *, stale_before: datetime, now: datetime
    ) -> int:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT j.id, j.attempts FROM trade_jobs j "
                "WHERE j.status='RUNNING' "
                "AND (j.started_at < ? OR ("
                "j.job_type='generate_daily_plan' "
                "AND json_extract(j.error_json, '$.code')='PLAN_IN_PROGRESS'"
                ")) "
                "AND NOT (j.job_type='generate_daily_plan' "
                "AND EXISTS ("
                "SELECT 1 FROM trade_plan_runs p "
                "JOIN trade_job_locks pl "
                "ON pl.lock_key='plan-generation:' || p.id "
                "WHERE p.signal_date=json_extract(j.request_json, '$.trade_date') "
                "AND p.status IN ('CREATED','VALIDATING','GENERATING') "
                "AND pl.expires_at > ?"
                ")) "
                "AND NOT EXISTS ("
                "SELECT 1 FROM trade_job_locks l "
                "WHERE l.lock_key = "
                "'job-execution:' || j.id || ':' || j.attempts "
                "AND l.expires_at > ?"
                ")",
                (
                    _db_timestamp(stale_before),
                    _db_lease_timestamp(now),
                    _db_lease_timestamp(now),
                ),
            ).fetchall()
            for row in rows:
                if row["attempts"] >= 3:
                    error = json.dumps(
                        {
                            "code": "JOB_MAX_ATTEMPTS",
                            "message": "陈旧 RUNNING 任务已达到最大尝试次数",
                        },
                        ensure_ascii=False,
                    )
                    conn.execute(
                        "UPDATE trade_jobs SET status='FAILED', error_json=?, "
                        "finished_at=datetime('now') WHERE id=?",
                        (error, row["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE trade_jobs SET status='QUEUED', started_at=NULL "
                        "WHERE id=?",
                        (row["id"],),
                    )
                conn.execute(
                    "DELETE FROM trade_job_locks WHERE lock_key=?",
                    (self._execution_lease_key(row["id"], row["attempts"]),),
                )
            conn.commit()
            return len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _execution_lease_key(job_id: int, expected_attempt: int) -> str:
        return f"job-execution:{job_id}:{expected_attempt}"

    @staticmethod
    def _plan_generation_lease_key(run_id: int) -> str:
        return f"plan-generation:{run_id}"

    def wait_for_plan_generation(
        self,
        job_id: int,
        *,
        error: dict,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
    ) -> bool:
        """Keep a plan job RUNNING while another live plan owner works.

        The execution lease is still required for this write, but is released
        by the caller afterwards. ``requeue_stale_jobs`` will make the job
        eligible again only once the observed plan-generation lease expires.
        """
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE trade_jobs SET progress=0, result_json=NULL, error_json=? "
                "WHERE id=? AND status='RUNNING' AND attempts=? "
                "AND EXISTS ("
                "SELECT 1 FROM trade_job_locks "
                "WHERE lock_key=? AND owner_id=? AND expires_at > ?"
                ")",
                (
                    json.dumps(error, ensure_ascii=False),
                    job_id,
                    expected_attempt,
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                ),
            )
            conn.commit()
            return updated.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def acquire_plan_generation_lease(
        self,
        run_id: int,
        *,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Claim an in-progress plan only when no live owner already exists."""
        lock_key = self._plan_generation_lease_key(run_id)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT 1 FROM trade_plan_runs WHERE id=? "
                "AND status IN ('CREATED','VALIDATING','GENERATING')",
                (run_id,),
            ).fetchone()
            if run is None:
                conn.commit()
                return False
            conn.execute(
                "DELETE FROM trade_job_locks WHERE lock_key=? AND expires_at <= ?",
                (lock_key, _db_lease_timestamp(now)),
            )
            acquired = conn.execute(
                "INSERT INTO trade_job_locks "
                "(lock_key, owner_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                (
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                    _db_lease_timestamp(expires_at),
                ),
            )
            conn.commit()
            return acquired.rowcount == 1
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew_plan_generation_lease(
        self,
        run_id: int,
        *,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        lock_key = self._plan_generation_lease_key(run_id)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            renewed = conn.execute(
                "UPDATE trade_job_locks SET expires_at=? "
                "WHERE lock_key=? AND owner_id=? AND expires_at > ? "
                "AND EXISTS (SELECT 1 FROM trade_plan_runs WHERE id=? "
                "AND status IN ('CREATED','VALIDATING','GENERATING'))",
                (
                    _db_lease_timestamp(expires_at),
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                    run_id,
                ),
            )
            conn.commit()
            return renewed.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_plan_generation_lease(
        self, run_id: int, *, owner_id: str
    ) -> bool:
        conn = self._conn()
        try:
            released = conn.execute(
                "DELETE FROM trade_job_locks WHERE lock_key=? AND owner_id=?",
                (self._plan_generation_lease_key(run_id), owner_id),
            )
            conn.commit()
            return released.rowcount == 1
        finally:
            conn.close()

    def acquire_job_execution_lease(
        self,
        job_id: int,
        *,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
        expires_at: datetime,
    ) -> dict | None:
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT * FROM trade_jobs "
                "WHERE id=? AND status='RUNNING' AND attempts=?",
                (job_id, expected_attempt),
            ).fetchone()
            if job is None:
                conn.commit()
                return None
            acquired = conn.execute(
                "INSERT INTO trade_job_locks "
                "(lock_key, owner_id, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(lock_key) DO NOTHING",
                (
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                    _db_lease_timestamp(expires_at),
                ),
            )
            if acquired.rowcount != 1:
                conn.commit()
                return None
            conn.commit()
            return self._decode_job_row(job)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew_job_execution_lease(
        self,
        job_id: int,
        *,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            renewed = conn.execute(
                "UPDATE trade_job_locks SET expires_at=? "
                "WHERE lock_key=? AND owner_id=? AND expires_at > ? "
                "AND EXISTS ("
                "SELECT 1 FROM trade_jobs "
                "WHERE id=? AND status='RUNNING' AND attempts=?"
                ")",
                (
                    _db_lease_timestamp(expires_at),
                    lock_key,
                    owner_id,
                    _db_lease_timestamp(now),
                    job_id,
                    expected_attempt,
                ),
            )
            if renewed.rowcount == 1:
                liveness = conn.execute(
                    "UPDATE trade_jobs SET started_at=? "
                    "WHERE id=? AND status='RUNNING' AND attempts=?",
                    (
                        _db_timestamp(now),
                        job_id,
                        expected_attempt,
                    ),
                )
                if liveness.rowcount != 1:
                    conn.rollback()
                    return False
            conn.commit()
            return renewed.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew_or_reacquire_job_execution_lease(
        self,
        job_id: int,
        *,
        owner_id: str,
        expected_attempt: int,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Atomically restore a lease only for the same live execution."""
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT 1 FROM trade_jobs "
                "WHERE id=? AND status='RUNNING' AND attempts=?",
                (job_id, expected_attempt),
            ).fetchone()
            if job is None:
                conn.commit()
                return False
            lease = conn.execute(
                "SELECT owner_id FROM trade_job_locks WHERE lock_key=?",
                (lock_key,),
            ).fetchone()
            if lease is None or lease["owner_id"] != owner_id:
                conn.commit()
                return False
            restored = conn.execute(
                "UPDATE trade_job_locks "
                "SET acquired_at=?, expires_at=? "
                "WHERE lock_key=? AND owner_id=?",
                (
                    _db_lease_timestamp(now),
                    _db_lease_timestamp(expires_at),
                    lock_key,
                    owner_id,
                ),
            )
            if restored.rowcount != 1:
                conn.commit()
                return False
            liveness = conn.execute(
                "UPDATE trade_jobs SET started_at=? "
                "WHERE id=? AND status='RUNNING' AND attempts=?",
                (_db_timestamp(now), job_id, expected_attempt),
            )
            if liveness.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_job_execution_lease(
        self,
        job_id: int,
        *,
        owner_id: str,
        expected_attempt: int,
    ) -> bool:
        lock_key = self._execution_lease_key(job_id, expected_attempt)
        conn = self._conn()
        try:
            released = conn.execute(
                "DELETE FROM trade_job_locks "
                "WHERE lock_key=? AND owner_id=?",
                (lock_key, owner_id),
            )
            conn.commit()
            return released.rowcount == 1
        finally:
            conn.close()

    def acquire_job_lock(
        self,
        *,
        lock_key: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM trade_job_locks "
                "WHERE lock_key=? AND expires_at <= ?",
                (lock_key, _db_timestamp(now)),
            )
            cursor = conn.execute(
                "INSERT INTO trade_job_locks "
                "(lock_key, owner_id, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(lock_key) DO UPDATE SET "
                "acquired_at=excluded.acquired_at, "
                "expires_at=excluded.expires_at "
                "WHERE trade_job_locks.owner_id=excluded.owner_id",
                (
                    lock_key,
                    owner_id,
                    _db_timestamp(now),
                    _db_timestamp(expires_at),
                ),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_job_lock(self, *, lock_key: str, owner_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM trade_job_locks WHERE lock_key=? AND owner_id=?",
                (lock_key, owner_id),
            )
            conn.commit()
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
            return self._decode_job_row(row)
        finally:
            conn.close()

    # ---- Phase 2: Account ----

    def create_account(self, *, name: str, initial_equity: float, cash_balance: float,
                       risk_per_trade: float = 0.005, max_single_position: float = 0.15,
                       max_total_exposure: float = 0.60, max_sector_exposure: float = 0.30,
                       max_positions: int = 5, max_drawdown_limit: float = 0.08,
                       is_active: bool = True) -> dict:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO trade_accounts "
                "(name, initial_equity, cash_balance, risk_per_trade, max_single_position, "
                " max_total_exposure, max_sector_exposure, max_positions, max_drawdown_limit, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, initial_equity, cash_balance, risk_per_trade, max_single_position,
                 max_total_exposure, max_sector_exposure, max_positions, max_drawdown_limit,
                 1 if is_active else 0),
            )
            conn.commit()
            return self._row_to_account(
                conn.execute("SELECT * FROM trade_accounts WHERE id = ?", (cur.lastrowid,)).fetchone()
            )
        finally:
            conn.close()

    def get_account(self, account_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM trade_accounts WHERE id = ?", (account_id,)).fetchone()
            return self._row_to_account(row) if row else None
        finally:
            conn.close()

    def list_accounts(self, active_only: bool = False) -> list[dict]:
        conn = self._conn()
        try:
            if active_only:
                rows = conn.execute("SELECT * FROM trade_accounts WHERE is_active = 1 ORDER BY id").fetchall()
            else:
                rows = conn.execute("SELECT * FROM trade_accounts ORDER BY id").fetchall()
            return [self._row_to_account(r) for r in rows]
        finally:
            conn.close()

    def update_account(self, account_id: int, fields: dict) -> None:
        """更新账户。initial_equity 不在允许字段内(语义不可改)。"""
        allowed = {"cash_balance", "risk_per_trade", "max_single_position",
                   "max_total_exposure", "max_sector_exposure", "max_positions",
                   "max_drawdown_limit", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        # is_active bool -> int
        if "is_active" in updates:
            updates["is_active"] = 1 if updates["is_active"] else 0
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [account_id]
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE trade_accounts SET {sets}, updated_at = datetime('now','localtime') "
                f"WHERE id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_account(row) -> dict:
        return {
            "id": row["id"], "name": row["name"], "initial_equity": row["initial_equity"],
            "cash_balance": row["cash_balance"], "risk_per_trade": row["risk_per_trade"],
            "max_single_position": row["max_single_position"],
            "max_total_exposure": row["max_total_exposure"],
            "max_sector_exposure": row["max_sector_exposure"],
            "max_positions": row["max_positions"],
            "max_drawdown_limit": row["max_drawdown_limit"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    # ---- Phase 2: Position ----

    def upsert_position(self, *, account_id: int, stock_code: str,
                        quantity: int, available_quantity: int, average_cost: float,
                        stock_name: str | None = None,
                        initial_stop: float | None = None,
                        trailing_stop: float | None = None,
                        opened_at: str | None = None) -> dict:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO trade_positions "
                "(account_id, stock_code, stock_name, quantity, available_quantity, "
                " average_cost, initial_stop, trailing_stop, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, stock_code) DO UPDATE SET "
                "  quantity = excluded.quantity, "
                "  available_quantity = excluded.available_quantity, "
                "  average_cost = excluded.average_cost, "
                "  stock_name = COALESCE(excluded.stock_name, trade_positions.stock_name), "
                "  initial_stop = COALESCE(excluded.initial_stop, trade_positions.initial_stop), "
                "  trailing_stop = COALESCE(excluded.trailing_stop, trade_positions.trailing_stop), "
                "  updated_at = datetime('now','localtime')",
                (account_id, stock_code, stock_name, quantity, available_quantity,
                 average_cost, initial_stop, trailing_stop, opened_at),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM trade_positions WHERE account_id = ? AND stock_code = ?",
                               (account_id, stock_code)).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_positions(self, account_id: int) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_positions WHERE account_id = ? ORDER BY stock_code",
                (account_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_position(self, account_id: int, stock_code: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_positions WHERE account_id = ? AND stock_code = ?",
                (account_id, stock_code),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_position(self, account_id: int, stock_code: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM trade_positions WHERE account_id = ? AND stock_code = ?",
                (account_id, stock_code),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- Phase 2: Execution ----

    def create_execution(self, *, account_id: int, stock_code: str, side: str,
                         trade_date: str, price: float, quantity: int,
                         commission: float = 0, tax: float = 0,
                         client_execution_id: str, note: str = "",
                         plan_item_id: int | None = None) -> dict:
        conn = self._conn()
        try:
            # 幂等:同 client_execution_id 返回已有
            existing = conn.execute(
                "SELECT * FROM trade_executions WHERE client_execution_id = ?",
                (client_execution_id,),
            ).fetchone()
            if existing:
                d = dict(existing)
                d["reused"] = True
                return d
            cur = conn.execute(
                "INSERT INTO trade_executions "
                "(account_id, plan_item_id, stock_code, side, trade_date, price, quantity, "
                " commission, tax, note, client_execution_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, plan_item_id, stock_code, side, trade_date, price, quantity,
                 commission, tax, note, client_execution_id),
            )
            conn.commit()
            d = dict(conn.execute("SELECT * FROM trade_executions WHERE id = ?",
                                  (cur.lastrowid,)).fetchone())
            d["reused"] = False
            return d
        finally:
            conn.close()

    def record_execution_atomic(
        self,
        *,
        account_id: int,
        stock_code: str,
        side: str,
        trade_date: str,
        price: float,
        quantity: int,
        commission: float = 0,
        tax: float = 0,
        client_execution_id: str,
        note: str = "",
        plan_item_id: int | None = None,
    ) -> dict:
        """Atomically update cash/position and persist execution/audit."""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM trade_executions "
                "WHERE client_execution_id=?",
                (client_execution_id,),
            ).fetchone()
            if existing is not None:
                execution = dict(existing)
                requested = {
                    "account_id": account_id,
                    "plan_item_id": plan_item_id,
                    "stock_code": stock_code,
                    "side": side,
                    "trade_date": trade_date,
                    "price": price,
                    "quantity": quantity,
                    "commission": commission,
                    "tax": tax,
                    "note": note,
                }
                conflicting_fields = [
                    field
                    for field, value in requested.items()
                    if execution[field] != value
                ]
                if conflicting_fields:
                    raise ExecutionIdempotencyConflictError(
                        "client_execution_id 已绑定其他成交载荷",
                        details={
                            "client_execution_id": client_execution_id,
                            "conflicting_fields": conflicting_fields,
                        },
                    )
                execution["reused"] = True
                position = conn.execute(
                    "SELECT * FROM trade_positions "
                    "WHERE account_id=? AND stock_code=?",
                    (execution["account_id"], execution["stock_code"]),
                ).fetchone()
                conn.commit()
                return {
                    "execution": execution,
                    "position": dict(position) if position else None,
                }

            account = conn.execute(
                "SELECT * FROM trade_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if account is None:
                raise ValueError(f"账户 {account_id} 不存在")
            position = conn.execute(
                "SELECT * FROM trade_positions "
                "WHERE account_id=? AND stock_code=?",
                (account_id, stock_code),
            ).fetchone()
            trade_value = price * quantity

            if side == "BUY":
                total_cost = trade_value + commission + tax
                if account["cash_balance"] < total_cost:
                    raise ValueError(
                        f"现金不足:需要 {total_cost},"
                        f"可用 {account['cash_balance']}"
                    )
                conn.execute(
                    "UPDATE trade_accounts SET cash_balance=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (account["cash_balance"] - total_cost, account_id),
                )
                if position is None:
                    conn.execute(
                        "INSERT INTO trade_positions "
                        "(account_id, stock_code, quantity, "
                        "available_quantity, average_cost) "
                        "VALUES (?, ?, ?, 0, ?)",
                        (account_id, stock_code, quantity, price),
                    )
                else:
                    new_quantity = position["quantity"] + quantity
                    average_cost = (
                        position["quantity"] * position["average_cost"]
                        + trade_value
                    ) / new_quantity
                    conn.execute(
                        "UPDATE trade_positions SET quantity=?, "
                        "average_cost=?, updated_at=datetime('now','localtime') "
                        "WHERE id=?",
                        (new_quantity, average_cost, position["id"]),
                    )
            elif side == "SELL":
                if position is None or position["quantity"] == 0:
                    raise ValueError(f"无 {stock_code} 持仓,无法卖出")
                if position["available_quantity"] < quantity:
                    raise ValueError(
                        f"可卖数量不足:需要 {quantity},"
                        f"可用 {position['available_quantity']}"
                    )
                net_proceeds = trade_value - commission - tax
                conn.execute(
                    "UPDATE trade_accounts SET cash_balance=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (account["cash_balance"] + net_proceeds, account_id),
                )
                new_quantity = position["quantity"] - quantity
                if new_quantity == 0:
                    conn.execute(
                        "DELETE FROM trade_positions WHERE id=?",
                        (position["id"],),
                    )
                else:
                    conn.execute(
                        "UPDATE trade_positions SET quantity=?, "
                        "available_quantity=?, "
                        "updated_at=datetime('now','localtime') WHERE id=?",
                        (
                            new_quantity,
                            position["available_quantity"] - quantity,
                            position["id"],
                        ),
                    )
            else:
                raise ValueError(f"未知 side: {side}(仅支持 BUY/SELL)")

            cursor = conn.execute(
                "INSERT INTO trade_executions "
                "(account_id, plan_item_id, stock_code, side, trade_date, "
                "price, quantity, commission, tax, note, "
                "client_execution_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    plan_item_id,
                    stock_code,
                    side,
                    trade_date,
                    price,
                    quantity,
                    commission,
                    tax,
                    note,
                    client_execution_id,
                ),
            )
            execution = dict(
                conn.execute(
                    "SELECT * FROM trade_executions WHERE id=?",
                    (cursor.lastrowid,),
                ).fetchone()
            )
            execution["reused"] = False
            conn.execute(
                "INSERT INTO trade_audit_logs "
                "(actor, action, entity_type, entity_id, after_json) "
                "VALUES ('user', ?, 'execution', ?, ?)",
                (
                    f"EXECUTION_{side}",
                    str(execution["id"]),
                    json.dumps(execution, ensure_ascii=False),
                ),
            )
            current_position = conn.execute(
                "SELECT * FROM trade_positions "
                "WHERE account_id=? AND stock_code=?",
                (account_id, stock_code),
            ).fetchone()
            conn.commit()
            return {
                "execution": execution,
                "position": (
                    dict(current_position) if current_position else None
                ),
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_execution_by_client_id(self, client_execution_id: str) -> dict | None:
        """按 client_execution_id 查找成交(供服务层幂等检查使用)。

        返回 dict 含 "reused": True 标记(若存在),否则 None。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_executions WHERE client_execution_id = ?",
                (client_execution_id,),
            ).fetchone()
            if row:
                d = dict(row)
                d["reused"] = True
                return d
            return None
        finally:
            conn.close()

    def list_executions(self, account_id: int, start: str | None = None,
                        end: str | None = None) -> list[dict]:
        clauses = ["account_id = ?"]
        params: list = [account_id]
        if start:
            clauses.append("trade_date >= ?")
            params.append(start)
        if end:
            clauses.append("trade_date <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_executions WHERE {where} ORDER BY trade_date DESC, id DESC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def roll_t1_available_atomic(
        self, account_id: int, trade_date: str
    ) -> int:
        """Atomically unlock prior-day holdings without stale-row writes."""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            buy_rows = conn.execute(
                "SELECT stock_code, SUM(quantity) AS quantity "
                "FROM trade_executions WHERE account_id=? "
                "AND trade_date=? AND side='BUY' GROUP BY stock_code",
                (account_id, trade_date),
            ).fetchall()
            same_day_buys = {
                row["stock_code"]: row["quantity"] for row in buy_rows
            }
            positions = conn.execute(
                "SELECT id, stock_code, quantity, available_quantity "
                "FROM trade_positions WHERE account_id=?",
                (account_id,),
            ).fetchall()
            changed = 0
            for position in positions:
                if position["quantity"] <= 0:
                    continue
                available = max(
                    0,
                    position["quantity"]
                    - same_day_buys.get(position["stock_code"], 0),
                )
                if position["available_quantity"] == available:
                    continue
                updated = conn.execute(
                    "UPDATE trade_positions SET available_quantity=? "
                    "WHERE id=? AND available_quantity=?",
                    (
                        available,
                        position["id"],
                        position["available_quantity"],
                    ),
                )
                changed += updated.rowcount
            conn.commit()
            return changed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- Phase 2: Equity Snapshot ----

    def upsert_equity_snapshot(self, *, account_id: int, trade_date: str,
                               cash: float, market_value: float, total_equity: float,
                               exposure: float, peak_equity: float, drawdown: float) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO trade_equity_snapshots "
                "(account_id, trade_date, cash, market_value, total_equity, exposure, "
                " peak_equity, drawdown) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, trade_date) DO UPDATE SET "
                "  cash = excluded.cash, market_value = excluded.market_value, "
                "  total_equity = excluded.total_equity, exposure = excluded.exposure, "
                "  peak_equity = excluded.peak_equity, drawdown = excluded.drawdown",
                (account_id, trade_date, cash, market_value, total_equity, exposure,
                 peak_equity, drawdown),
            )
            conn.commit()
        finally:
            conn.close()

    def get_equity_snapshot(self, account_id: int, trade_date: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_equity_snapshots WHERE account_id = ? AND trade_date = ?",
                (account_id, trade_date),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_latest_equity_snapshot(self, account_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_equity_snapshots WHERE account_id = ? "
                "ORDER BY trade_date DESC LIMIT 1",
                (account_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_equity_snapshots(self, account_id: int, start: str | None = None,
                              end: str | None = None) -> list[dict]:
        """列出账户在 [start, end] 区间的净值快照(按 trade_date 升序)。"""
        clauses = ["account_id = ?"]
        params: list = [account_id]
        if start:
            clauses.append("trade_date >= ?")
            params.append(start)
        if end:
            clauses.append("trade_date <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_equity_snapshots WHERE {where} "
                f"ORDER BY trade_date ASC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---- Phase 3: Strategy Version ----

    @staticmethod
    def _params_hash(params_json: dict) -> str:
        """对 params_json 计算稳定哈希(键排序 + ensure_ascii=False)[:16]。"""
        canonical = json.dumps(params_json, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def create_strategy_version(self, *, strategy_code: str, name: str,
                                params_json: dict) -> dict:
        """创建策略版本。

        params_hash = sha256(canonical_json(params))[:16]。
        UNIQUE(params_hash) 命中则返回已有(reused=True)。
        否则 version_no = MAX(version_no)+1(按 strategy_code),status='DRAFT'。
        """
        params_hash = self._params_hash(params_json)
        params_json_str = json.dumps(params_json, ensure_ascii=False)
        conn = self._conn()
        try:
            # 1. 幂等:同 params_hash 已存在则复用(全局唯一)
            existing = conn.execute(
                "SELECT id, version_no, status FROM trade_strategy_versions "
                "WHERE params_hash = ?",
                (params_hash,),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "version_no": existing["version_no"],
                        "params_hash": params_hash, "status": existing["status"],
                        "reused": True}

            # 2. 计算新版本号(按 strategy_code)
            max_ver = conn.execute(
                "SELECT MAX(version_no) AS m FROM trade_strategy_versions "
                "WHERE strategy_code = ?",
                (strategy_code,),
            ).fetchone()
            next_ver = (max_ver["m"] or 0) + 1

            cur = conn.execute(
                "INSERT INTO trade_strategy_versions "
                "(strategy_code, version_no, name, params_json, params_hash, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'DRAFT', datetime('now','localtime'))",
                (strategy_code, next_ver, name, params_json_str, params_hash),
            )
            version_id = cur.lastrowid
            conn.commit()
            return {"id": version_id, "version_no": next_ver,
                    "params_hash": params_hash, "status": "DRAFT", "reused": False}
        finally:
            conn.close()

    def get_strategy(self, version_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_strategy_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_strategy(row)
        finally:
            conn.close()

    def list_strategies(self, strategy_code: str | None = None) -> list[dict]:
        conn = self._conn()
        try:
            if strategy_code:
                rows = conn.execute(
                    "SELECT * FROM trade_strategy_versions WHERE strategy_code = ? "
                    "ORDER BY version_no ASC",
                    (strategy_code,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trade_strategy_versions ORDER BY strategy_code, version_no ASC"
                ).fetchall()
            return [self._row_to_strategy(r) for r in rows]
        finally:
            conn.close()

    def get_active_strategy(self, strategy_code: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_strategy_versions "
                "WHERE strategy_code = ? AND status = 'ACTIVE' "
                "ORDER BY version_no DESC LIMIT 1",
                (strategy_code,),
            ).fetchone()
            return self._row_to_strategy(row) if row else None
        finally:
            conn.close()

    def activate_strategy(self, version_id: int) -> int | None:
        """激活策略版本:目标→ACTIVE,同 strategy_code 其它→RETIRED。

        事务内一次提交。返回之前 ACTIVE 版本的 id(或 None)。
        """
        conn = self._conn()
        try:
            target = conn.execute(
                "SELECT strategy_code FROM trade_strategy_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            if not target:
                raise ValueError(f"策略版本 {version_id} 不存在")
            code = target["strategy_code"]
            # 找出当前 ACTIVE(若存在),用于返回
            prev = conn.execute(
                "SELECT id FROM trade_strategy_versions "
                "WHERE strategy_code = ? AND status = 'ACTIVE' AND id != ? "
                "ORDER BY version_no DESC LIMIT 1",
                (code, version_id),
            ).fetchone()
            prev_id = prev["id"] if prev else None
            # 其它版本 RETIRED
            conn.execute(
                "UPDATE trade_strategy_versions SET status = 'RETIRED' "
                "WHERE strategy_code = ? AND id != ? AND status = 'ACTIVE'",
                (code, version_id),
            )
            # 目标 ACTIVE
            conn.execute(
                "UPDATE trade_strategy_versions "
                "SET status = 'ACTIVE', activated_at = datetime('now','localtime') "
                "WHERE id = ?",
                (version_id,),
            )
            conn.commit()
            return prev_id
        finally:
            conn.close()

    @staticmethod
    def _row_to_strategy(row) -> dict:
        d = dict(row)
        try:
            d["params_json"] = json.loads(d.get("params_json") or "{}")
        except (ValueError, TypeError):
            d["params_json"] = {}
        return d

    # ---- Phase 3: Plan Run ----

    def create_plan_run(self, *, run_key: str, account_id: int, signal_date: str,
                        target_trade_date: str, stock_pool_version_id: int,
                        strategy_version_id: int, status: str,
                        account_snapshot_json: dict, data_snapshot_hash: str,
                        warnings: list | None = None,
                        generation_owner_id: str | None = None,
                        generation_now: datetime | None = None,
                        generation_expires_at: datetime | None = None) -> dict:
        """创建计划运行。run_key UNIQUE 命中则返回已有(reused=True)。"""
        warnings = warnings or []
        if generation_owner_id is not None and (
            generation_now is None or generation_expires_at is None
        ):
            raise ValueError("generation lease 必须同时提供 now 和 expires_at")
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM trade_plan_runs WHERE run_key = ?",
                (run_key,),
            ).fetchone()
            if existing:
                conn.commit()
                return {"id": existing["id"], "run_key": run_key, "reused": True}
            cur = conn.execute(
                "INSERT INTO trade_plan_runs "
                "(run_key, account_id, signal_date, target_trade_date, "
                " stock_pool_version_id, strategy_version_id, status, "
                " account_snapshot_json, data_snapshot_hash, warnings_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
                (run_key, account_id, signal_date, target_trade_date,
                 stock_pool_version_id, strategy_version_id, status,
                 json.dumps(account_snapshot_json, ensure_ascii=False),
                 data_snapshot_hash,
                 json.dumps(warnings, ensure_ascii=False)),
            )
            run_id = cur.lastrowid
            if generation_owner_id is not None:
                conn.execute(
                    "INSERT INTO trade_job_locks "
                    "(lock_key, owner_id, acquired_at, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        self._plan_generation_lease_key(run_id),
                        generation_owner_id,
                        _db_lease_timestamp(generation_now),
                        _db_lease_timestamp(generation_expires_at),
                    ),
                )
            conn.commit()
            return {"id": run_id, "run_key": run_key, "reused": False}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover_stale_plan_run_and_create_successor(
        self,
        *,
        stale_run_id: int,
        run_key: str,
        account_id: int,
        signal_date: str,
        target_trade_date: str,
        stock_pool_version_id: int,
        strategy_version_id: int,
        account_snapshot_json: dict,
        data_snapshot_hash: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
        warnings: list | None = None,
    ) -> dict:
        """Atomically fence an expired in-progress owner and create its successor."""
        warnings = warnings or []
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            stale = conn.execute(
                "SELECT * FROM trade_plan_runs WHERE id=?",
                (stale_run_id,),
            ).fetchone()
            if stale is None:
                conn.commit()
                return {"recovered": False, "run": None}
            if stale["status"] not in ("CREATED", "VALIDATING", "GENERATING"):
                conn.commit()
                return {"recovered": False, "run": self._row_to_plan_run(stale)}
            lock_key = self._plan_generation_lease_key(stale_run_id)
            lease = conn.execute(
                "SELECT owner_id, expires_at FROM trade_job_locks WHERE lock_key=?",
                (lock_key,),
            ).fetchone()
            if lease is not None and lease["expires_at"] > _db_lease_timestamp(now):
                conn.commit()
                return {"recovered": False, "run": self._row_to_plan_run(stale)}
            conn.execute("DELETE FROM trade_job_locks WHERE lock_key=?", (lock_key,))
            cur = conn.execute(
                "INSERT INTO trade_plan_runs "
                "(run_key, account_id, signal_date, target_trade_date, "
                " stock_pool_version_id, strategy_version_id, status, "
                " account_snapshot_json, data_snapshot_hash, warnings_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, ?, datetime('now','localtime'))",
                (
                    run_key, account_id, signal_date, target_trade_date,
                    stock_pool_version_id, strategy_version_id,
                    json.dumps(account_snapshot_json, ensure_ascii=False),
                    data_snapshot_hash, json.dumps(warnings, ensure_ascii=False),
                ),
            )
            successor_id = cur.lastrowid
            recovery_marker = {
                "code": "PLAN_GENERATION_RECOVERED",
                "message": "generation owner lease expired; created successor plan",
                "details": {
                    "reason": "GENERATION_LEASE_EXPIRED",
                    "successor_run_id": successor_id,
                    "original_run_key": stale["run_key"],
                },
            }
            fenced = conn.execute(
                "UPDATE trade_plan_runs SET status='SUPERSEDED', superseded_by_id=?, "
                "error_json=? "
                "WHERE id=? AND status IN ('CREATED','VALIDATING','GENERATING')",
                (successor_id, json.dumps(recovery_marker, ensure_ascii=False), stale_run_id),
            )
            if fenced.rowcount != 1:
                conn.rollback()
                return {"recovered": False, "run": self.get_plan_run(stale_run_id)}
            conn.execute(
                "INSERT INTO trade_job_locks "
                "(lock_key, owner_id, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                (
                    self._plan_generation_lease_key(successor_id),
                    owner_id,
                    _db_lease_timestamp(now),
                    _db_lease_timestamp(expires_at),
                ),
            )
            successor = conn.execute(
                "SELECT * FROM trade_plan_runs WHERE id=?", (successor_id,)
            ).fetchone()
            conn.commit()
            return {"recovered": True, "run": self._row_to_plan_run(successor)}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_plan_run(self, run_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_plan_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return self._row_to_plan_run(row) if row else None
        finally:
            conn.close()

    def get_plan_run_by_key(self, run_key: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_plan_runs WHERE run_key = ?", (run_key,)
            ).fetchone()
            return self._row_to_plan_run(row) if row else None
        finally:
            conn.close()

    def list_plan_runs(self, signal_date: str | None = None,
                       status: str | None = None,
                       account_id: int | None = None) -> list[dict]:
        clauses = []
        params: list = []
        if signal_date:
            clauses.append("signal_date = ?")
            params.append(signal_date)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_plan_runs{where} ORDER BY id DESC",
                params,
            ).fetchall()
            return [self._row_to_plan_run(r) for r in rows]
        finally:
            conn.close()

    def update_plan_run_status(self, run_id: int, status: str, *,
                               error: dict | None = None,
                               market_regime: str | None = None,
                               market_score: int | None = None,
                               recommended_exposure: float | None = None,
                               warnings: list | None = None,
                               degraded: bool | None = None) -> None:
        sets = ["status = ?"]
        params: list = [status]
        if error is not None:
            sets.append("error_json = ?")
            params.append(json.dumps(error, ensure_ascii=False))
        if market_regime is not None:
            sets.append("market_regime = ?")
            params.append(market_regime)
        if market_score is not None:
            sets.append("market_score = ?")
            params.append(market_score)
        if recommended_exposure is not None:
            sets.append("recommended_exposure = ?")
            params.append(recommended_exposure)
        if warnings is not None:
            sets.append("warnings_json = ?")
            params.append(json.dumps(warnings, ensure_ascii=False))
        if degraded is not None:
            sets.append("degraded = ?")
            params.append(1 if degraded else 0)
        params.append(run_id)
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE trade_plan_runs SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()

    def supersede_plan_run(self, old_run_id: int, new_run_id: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE trade_plan_runs "
                "SET status = 'SUPERSEDED', superseded_by_id = ? WHERE id = ?",
                (new_run_id, old_run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def publish_plan_run(self, run_id: int) -> bool:
        """Publish only a still-executable plan; never revive a fenced row."""
        conn = self._conn()
        try:
            updated = conn.execute(
                "UPDATE trade_plan_runs "
                "SET status = 'PUBLISHED', published_at = datetime('now','localtime') "
                "WHERE id = ? AND status IN ('READY', 'PARTIAL')",
                (run_id,),
            )
            conn.commit()
            return updated.rowcount == 1
        finally:
            conn.close()

    @staticmethod
    def _row_to_plan_run(row) -> dict:
        d = dict(row)
        d["degraded"] = bool(d.get("degraded", 0))
        try:
            d["account_snapshot"] = json.loads(d.pop("account_snapshot_json") or "{}")
        except (ValueError, TypeError):
            d["account_snapshot"] = {}
        try:
            d["warnings"] = json.loads(d.pop("warnings_json") or "[]")
        except (ValueError, TypeError):
            d["warnings"] = []
        try:
            d["error"] = json.loads(d.pop("error_json") or "null")
        except (ValueError, TypeError):
            d["error"] = None
        return d

    # ---- Phase 3: Plan Item ----

    def create_plan_item(self, *, plan_run_id: int, stock_code: str,
                         action: str, **kwargs) -> dict:
        """创建计划项。可选字段缺失用 DB 默认值。

        rule_hits_json/rule_misses_json 接受 list,存为 JSON TEXT。
        invalidation_reason 接受 str。
        """
        rule_hits = kwargs.get("rule_hits_json", [])
        rule_misses = kwargs.get("rule_misses_json", [])
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO trade_plan_items "
                "(plan_run_id, stock_code, stock_name, action, score, rank_no, "
                " trigger_price, do_not_chase_price, stop_price, target_2r_price, "
                " suggested_quantity, suggested_position_pct, risk_amount, risk_pct, "
                " rule_hits_json, rule_misses_json, invalidation_reason, execution_status, "
                " created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                " datetime('now','localtime'))",
                (plan_run_id, stock_code, kwargs.get("stock_name"), action,
                 kwargs.get("score"), kwargs.get("rank_no"),
                 kwargs.get("trigger_price"), kwargs.get("do_not_chase_price"),
                 kwargs.get("stop_price"), kwargs.get("target_2r_price"),
                 kwargs.get("suggested_quantity", 0),
                 kwargs.get("suggested_position_pct", 0),
                 kwargs.get("risk_amount", 0), kwargs.get("risk_pct", 0),
                 json.dumps(rule_hits, ensure_ascii=False),
                 json.dumps(rule_misses, ensure_ascii=False),
                 kwargs.get("invalidation_reason"),
                 kwargs.get("execution_status", "PENDING")),
            )
            item_id = cur.lastrowid
            conn.commit()
            return {"id": item_id, "plan_run_id": plan_run_id,
                    "stock_code": stock_code, "action": action}
        finally:
            conn.close()

    def transition_plan_run_status(
        self,
        run_id: int,
        status: str,
        *,
        expected_statuses: tuple[str, ...],
        generation_owner_id: str,
        now: datetime,
        error: dict | None = None,
    ) -> bool:
        """Transition only while this owner holds a live generation lease."""
        if not expected_statuses:
            raise ValueError("expected_statuses 不能为空")
        placeholders = ", ".join("?" for _ in expected_statuses)
        sets = ["status = ?"]
        params: list = [status]
        if error is not None:
            sets.append("error_json = ?")
            params.append(json.dumps(error, ensure_ascii=False))
        params.extend([
            run_id,
            *expected_statuses,
            run_id,
            generation_owner_id,
            _db_lease_timestamp(now),
        ])
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                f"UPDATE trade_plan_runs SET {', '.join(sets)} "
                f"WHERE id=? AND status IN ({placeholders}) "
                "AND EXISTS ("
                "SELECT 1 FROM trade_job_locks "
                "WHERE lock_key='plan-generation:' || ? "
                "AND owner_id=? AND expires_at > ?"
                ")",
                params,
            )
            conn.commit()
            return updated.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _plan_item_row(plan_run_id: int, item: dict) -> tuple:
        return (
            plan_run_id,
            item["stock_code"], item.get("stock_name"), item["action"],
            item.get("score"), item.get("rank_no"),
            item.get("trigger_price"), item.get("do_not_chase_price"),
            item.get("stop_price"), item.get("target_2r_price"),
            item.get("suggested_quantity", 0),
            item.get("suggested_position_pct", 0),
            item.get("risk_amount", 0), item.get("risk_pct", 0),
            json.dumps(item.get("rule_hits_json", []), ensure_ascii=False),
            json.dumps(item.get("rule_misses_json", []), ensure_ascii=False),
            item.get("invalidation_reason"),
            item.get("execution_status", "PENDING"),
        )

    def finalize_plan_run(
        self,
        run_id: int,
        *,
        status: str,
        items: tuple[dict, ...],
        market_regime: str,
        market_score: int,
        recommended_exposure: float,
        warnings: list[str],
        degraded: bool,
        generation_owner_id: str,
        now: datetime,
    ) -> dict:
        """Persist a successful run and fence all peer executable plans atomically."""
        if status not in ("READY", "PARTIAL"):
            raise ValueError(f"不可作为计划成功终态: {status}")
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM trade_plan_runs WHERE id=?", (run_id,)
            ).fetchone()
            if current is None:
                raise ValueError(f"计划 {run_id} 不存在")
            if current["status"] != "GENERATING":
                conn.commit()
                return self._row_to_plan_run(current)

            lease = conn.execute(
                "SELECT 1 FROM trade_job_locks "
                "WHERE lock_key='plan-generation:' || ? "
                "AND owner_id=? AND expires_at > ?",
                (run_id, generation_owner_id, _db_lease_timestamp(now)),
            ).fetchone()
            if lease is None:
                conn.commit()
                return self._row_to_plan_run(current)

            published = conn.execute(
                "SELECT id FROM trade_plan_runs WHERE account_id=? AND signal_date=? "
                "AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",
                (current["account_id"], current["signal_date"]),
            ).fetchone()
            if published is not None:
                conn.execute(
                    "UPDATE trade_plan_runs SET status='SUPERSEDED', superseded_by_id=? "
                    "WHERE id=? AND status='GENERATING'",
                    (published["id"], run_id),
                )
                final = conn.execute(
                    "SELECT * FROM trade_plan_runs WHERE id=?", (run_id,)
                ).fetchone()
                conn.commit()
                return self._row_to_plan_run(final)

            if items:
                conn.executemany(
                    "INSERT INTO trade_plan_items "
                    "(plan_run_id, stock_code, stock_name, action, score, rank_no, "
                    " trigger_price, do_not_chase_price, stop_price, target_2r_price, "
                    " suggested_quantity, suggested_position_pct, risk_amount, risk_pct, "
                    " rule_hits_json, rule_misses_json, invalidation_reason, execution_status, "
                    " created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    " datetime('now','localtime'))",
                    [self._plan_item_row(run_id, item) for item in items],
                )
            updated = conn.execute(
                "UPDATE trade_plan_runs SET status=?, market_regime=?, market_score=?, "
                "recommended_exposure=?, warnings_json=?, degraded=? "
                "WHERE id=? AND status='GENERATING'",
                (
                    status, market_regime, market_score, recommended_exposure,
                    json.dumps(warnings, ensure_ascii=False), 1 if degraded else 0,
                    run_id,
                ),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return self.get_plan_run(run_id)
            conn.execute(
                "UPDATE trade_plan_runs SET status='SUPERSEDED', superseded_by_id=? "
                "WHERE account_id=? AND signal_date=? AND id<>? "
                "AND status IN ('CREATED','VALIDATING','GENERATING','READY','PARTIAL','BLOCKED','FAILED')",
                (run_id, current["account_id"], current["signal_date"], run_id),
            )
            final = conn.execute(
                "SELECT * FROM trade_plan_runs WHERE id=?", (run_id,)
            ).fetchone()
            conn.commit()
            return self._row_to_plan_run(final)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_plan_item_execution_status(
        self,
        item_id: int,
        expected_status: str,
        new_status: str,
    ) -> bool:
        conn = self._conn()
        try:
            updated = conn.execute(
                "UPDATE trade_plan_items SET execution_status=? "
                "WHERE id=? AND execution_status=?",
                (new_status, item_id, expected_status),
            )
            conn.commit()
            return updated.rowcount == 1
        finally:
            conn.close()

    def get_plan_items(self, plan_run_id: int) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_plan_items WHERE plan_run_id = ? ORDER BY id ASC",
                (plan_run_id,),
            ).fetchall()
            return [self._row_to_plan_item(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_plan_item(row) -> dict:
        d = dict(row)
        try:
            d["rule_hits"] = json.loads(d.pop("rule_hits_json") or "[]")
        except (ValueError, TypeError):
            d["rule_hits"] = []
        try:
            d["rule_misses"] = json.loads(d.pop("rule_misses_json") or "[]")
        except (ValueError, TypeError):
            d["rule_misses"] = []
        return d

    # ---- Phase 2: Audit Log ----

    def write_audit_log(self, *, actor: str, action: str, entity_type: str,
                        entity_id: str | None = None, before_json=None,
                        after_json=None, request_id: str | None = None) -> int:
        """写审计日志。before_json/after_json 接受 dict 或 str(自动序列化 dict)。"""
        if isinstance(before_json, (dict, list)):
            before_json = json.dumps(before_json, ensure_ascii=False)
        if isinstance(after_json, (dict, list)):
            after_json = json.dumps(after_json, ensure_ascii=False)
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO trade_audit_logs "
                "(actor, action, entity_type, entity_id, before_json, after_json, request_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (actor, action, entity_type, entity_id, before_json, after_json, request_id),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def list_audit_logs(self, *, entity_type: str | None = None,
                        entity_id: str | None = None,
                        action: str | None = None,
                        limit: int = 50) -> list[dict]:
        """查询审计日志(spec §11.1:所有写操作可追溯)。"""
        clauses = []
        params: list = []
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id:
            clauses.append("action = ?")
            params.append(action)
        if action:
            clauses.append("action = ?")
            params.append(action)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_audit_logs{where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                for key in ("before_json", "after_json"):
                    val = d.get(key)
                    if val:
                        try:
                            d[key] = json.loads(val)
                        except (ValueError, TypeError):
                            pass
                result.append(d)
            return result
        finally:
            conn.close()

    # ---- Phase 5: Backtest Run ----

    def create_backtest_run(self, *, job_id: int, strategy_version_id: int,
                            stock_pool_version_id: int, start_date: str,
                            end_date: str, initial_equity: float,
                            fee_params_json: dict, status: str) -> dict:
        """创建回测运行(spec §10.2)。job_id UNIQUE 命中则返回已有。"""
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id FROM trade_backtest_runs WHERE job_id = ?", (job_id,),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "reused": True}
            cur = conn.execute(
                "INSERT INTO trade_backtest_runs "
                "(job_id, strategy_version_id, stock_pool_version_id, start_date, end_date, "
                " initial_equity, fee_params_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, strategy_version_id, stock_pool_version_id, start_date, end_date,
                 initial_equity, json.dumps(fee_params_json, ensure_ascii=False), status),
            )
            run_id = cur.lastrowid
            conn.commit()
            return {"id": run_id, "reused": False}
        finally:
            conn.close()

    def update_backtest_run(self, run_id: int, *, status: str,
                            metrics_json: dict | None = None,
                            equity_curve_json: list | None = None,
                            finished_at: str | None = None) -> None:
        sets = ["status = ?"]
        params: list = [status]
        if metrics_json is not None:
            sets.append("metrics_json = ?")
            params.append(json.dumps(metrics_json, ensure_ascii=False))
        if equity_curve_json is not None:
            sets.append("equity_curve_json = ?")
            params.append(json.dumps(equity_curve_json, ensure_ascii=False))
        if finished_at is not None:
            sets.append("finished_at = ?")
            params.append(finished_at)
        else:
            sets.append("finished_at = datetime('now','localtime')")
        params.append(run_id)
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE trade_backtest_runs SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
        finally:
            conn.close()

    def get_backtest_run(self, run_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM trade_backtest_runs WHERE id = ?", (run_id,),
            ).fetchone()
            return self._row_to_backtest_run(row) if row else None
        finally:
            conn.close()

    def list_backtest_runs(self, strategy_version_id: int | None = None,
                           status: str | None = None) -> list[dict]:
        """列出回测运行(最新优先)。用于激活门禁查最近一次回测。"""
        clauses = []
        params: list = []
        if strategy_version_id is not None:
            clauses.append("strategy_version_id = ?")
            params.append(strategy_version_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM trade_backtest_runs{where} ORDER BY id DESC", params,
            ).fetchall()
            return [self._row_to_backtest_run(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_backtest_run(row) -> dict:
        d = dict(row)
        try:
            d["fee_params"] = json.loads(d.pop("fee_params_json") or "{}")
        except (ValueError, TypeError):
            d["fee_params"] = {}
        try:
            d["metrics"] = json.loads(d.pop("metrics_json") or "null")
        except (ValueError, TypeError):
            d["metrics"] = None
        try:
            d["equity_curve"] = json.loads(d.pop("equity_curve_json") or "null")
        except (ValueError, TypeError):
            d["equity_curve"] = None
        return d

    # ---- Phase 5: Backtest Trade ----

    def create_backtest_trade(self, *, backtest_run_id: int, stock_code: str,
                              signal_date: str, entry_date: str | None = None,
                              entry_price: float | None = None,
                              exit_date: str | None = None,
                              exit_price: float | None = None,
                              quantity: int | None = None,
                              pnl: float | None = None,
                              r_multiple: float | None = None,
                              exit_reason: str | None = None,
                              details: dict | None = None) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO trade_backtest_trades "
                "(backtest_run_id, stock_code, signal_date, entry_date, entry_price, "
                " exit_date, exit_price, quantity, pnl, r_multiple, exit_reason, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (backtest_run_id, stock_code, signal_date, entry_date, entry_price,
                 exit_date, exit_price, quantity, pnl, r_multiple, exit_reason,
                 json.dumps(details or {}, ensure_ascii=False)),
            )
            trade_id = cur.lastrowid
            conn.commit()
            return trade_id
        finally:
            conn.close()

    def list_backtest_trades(self, run_id: int) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_backtest_trades WHERE backtest_run_id = ? "
                "ORDER BY id ASC",
                (run_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["details"] = json.loads(d.pop("details_json") or "{}")
                except (ValueError, TypeError):
                    d["details"] = {}
                result.append(d)
            return result
        finally:
            conn.close()

    # ---- 热榜只读复用(spec §2.1: 现有热榜数据只读,不改语义) ----

    def get_hotlist_presence(self, codes: list[str], days: int = 5) -> dict[str, dict]:
        """查询股票近 N 日热榜在榜情况(只读 stock_records)。

        返回 {bare_code: {"on_list_days": int, "latest_rank": int,
                          "rank_improved": bool, "heat_value": float|None}}。
        rank_improved: 最新排名比首次上榜排名上升(数字变小)。
        """
        if not codes:
            return {}
        bares = [c.split(".")[0] for c in codes]
        placeholders = ",".join("?" * len(bares))
        from datetime import date as _date, timedelta as _td
        start = (_date.today() - _td(days=days)).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT date, stock_code, rank, heat_value FROM stock_records "
                f"WHERE stock_code IN ({placeholders}) AND date >= ? "
                f"ORDER BY date DESC",
                (*bares, start),
            ).fetchall()
        finally:
            conn.close()
        by_code: dict[str, list] = {}
        for r in rows:
            by_code.setdefault(r["stock_code"], []).append(r)
        result: dict[str, dict] = {}
        for bare, rs in by_code.items():
            rs_sorted = sorted(rs, key=lambda x: x["date"])
            first_rank = rs_sorted[0]["rank"]
            latest = rs_sorted[-1]
            result[bare] = {
                "on_list_days": len(rs),
                "latest_rank": latest["rank"],
                "rank_improved": latest["rank"] < first_rank,
                "heat_value": latest["heat_value"],
            }
        return result

    def get_latest_hotlist_top(self, limit: int = 10) -> list[dict]:
        """最新交易日的热榜 Top N(只读,用于股池同步)。

        返回 [{stock_code(裸6位), stock_name, rank}]。过滤 ST/非沪深主板代码。
        """
        conn = self._conn()
        try:
            latest = conn.execute(
                "SELECT MAX(date) AS d FROM stock_records"
            ).fetchone()
            if not latest or not latest["d"]:
                return []
            rows = conn.execute(
                "SELECT stock_code, stock_name, rank FROM stock_records "
                "WHERE date = ? ORDER BY rank ASC LIMIT ?",
                (latest["d"], limit * 2),  # 多取,过滤后再截断
            ).fetchall()
        finally:
            conn.close()
        result = []
        for r in rows:
            code, name = r["stock_code"], r["stock_name"] or ""
            # 过滤 ST/退市
            if "ST" in name.upper() or "退" in name:
                continue
            # 只留沪深常规代码(0/3 开头深市,6 开头沪市;排除北交所 8/4 开头)
            if not (code.startswith(("0", "3", "6")) and len(code) == 6 and code.isdigit()):
                continue
            result.append({
                "stock_code": code, "stock_name": name, "rank": r["rank"],
            })
            if len(result) >= limit:
                break
        return result
