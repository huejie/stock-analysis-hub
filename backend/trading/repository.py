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
                        warnings: list | None = None) -> dict:
        """创建计划运行。run_key UNIQUE 命中则返回已有(reused=True)。"""
        warnings = warnings or []
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id FROM trade_plan_runs WHERE run_key = ?",
                (run_key,),
            ).fetchone()
            if existing:
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
            conn.commit()
            return {"id": run_id, "run_key": run_key, "reused": False}
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
                       status: str | None = None) -> list[dict]:
        clauses = []
        params: list = []
        if signal_date:
            clauses.append("signal_date = ?")
            params.append(signal_date)
        if status:
            clauses.append("status = ?")
            params.append(status)
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
                               recommended_exposure: float | None = None) -> None:
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

    def publish_plan_run(self, run_id: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE trade_plan_runs "
                "SET status = 'PUBLISHED', published_at = datetime('now','localtime') "
                "WHERE id = ?",
                (run_id,),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_plan_run(row) -> dict:
        d = dict(row)
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
