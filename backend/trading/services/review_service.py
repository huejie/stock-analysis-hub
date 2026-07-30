"""复盘服务(spec §11.2 GET /reviews/summary + §14.3 复盘指标)。

基于实际成交(trade_executions)和净值快照(trade_equity_snapshots)计算:
- win_rate:已平仓交易的胜率(FIFO 配对 BUY/SELL)。
- avg_r:R 倍数,需关联 plan_item.stop_price(plan_item_id)。
- expectancy:单笔期望(spec §14.3)。
- max_drawdown:从 equity_snapshots 的 drawdown 列取区间内最大值。
- execution_rate:已执行 plan_items / 总 CONDITIONAL_BUY plan_items(区间内)。

Phase 5 简化:
- 平仓配对采用每股 FIFO(BUY 队列按时间消耗),不复用 trade_positions 的平均成本,
  以便精确还原每笔 R。当一笔 SELL 部分平仓时,按比例切分。
- R 倍数缺失 stop_price(BUY 无 plan_item_id 或 plan_item 无 stop_price)时
  记为 None,不计入 avg_r / expectancy。
"""
from datetime import date, timedelta


class ReviewService:
    def __init__(self, repo):
        self.repo = repo

    def get_summary(self, *, account_id: int, period: int = 30) -> dict:
        """复盘摘要(spec §11.2 /reviews/summary)。

        period:回看最近 N 天(默认 30)。区间为 [today - period, today]。
        """
        if period <= 0:
            raise ValueError("period 必须为正整数")
        account = self.repo.get_account(account_id)
        if account is None:
            raise ValueError(f"账户 {account_id} 不存在")

        today = date.today()
        start_d = today - timedelta(days=period)
        start, end = start_d.isoformat(), today.isoformat()

        # ---- 已执行成交(区间内)----
        execs = self.repo.list_executions(account_id, start=start, end=end)
        closed = self._pair_closed_trades(execs)

        # ---- R 倍数:关联 plan_item.stop_price ----
        stop_by_plan_item = self._load_stop_prices(account_id, start, end)
        enriched = self._enrich_r(closed, stop_by_plan_item)
        r_vals = [t["r_multiple"] for t in enriched if t["r_multiple"] is not None]

        # ---- §14.3 指标 ----
        trade_count = len(enriched)
        wins = [t for t in enriched if (t["pnl"] or 0) > 0]
        losses = [t for t in enriched if (t["pnl"] or 0) <= 0]
        win_rate = (len(wins) / trade_count) if trade_count else 0.0

        win_rs = [r for r in r_vals if r > 0]
        loss_rs = [r for r in r_vals if r <= 0]
        avg_win_r = (sum(win_rs) / len(win_rs)) if win_rs else 0.0
        avg_loss_r = (abs(sum(loss_rs) / len(loss_rs)) if loss_rs else 0.0)
        loss_rate = 1.0 - (
            len(win_rs) / len(r_vals) if r_vals else 0.0
        )
        # expectancy 用 R 倍数口径(有 stop_price 的交易)
        expectancy_r = (
            (len(win_rs) / len(r_vals)) * avg_win_r - loss_rate * avg_loss_r
            if r_vals else 0.0
        )

        gross_profit = sum((t["pnl"] or 0) for t in enriched if (t["pnl"] or 0) > 0)
        gross_loss = abs(sum((t["pnl"] or 0) for t in enriched if (t["pnl"] or 0) < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0
        )

        # ---- 最大回撤(从 equity_snapshots)----
        max_drawdown = self._max_drawdown(account_id, start, end)

        # ---- 执行率(已执行 CONDITIONAL_BUY / 总 CONDITIONAL_BUY)----
        executed, total = self._execution_rate(account_id, start, end, execs)
        execution_rate = (executed / total) if total > 0 else 0.0

        return {
            "account_id": account_id,
            "period": period,
            "start_date": start,
            "end_date": end,
            "trade_count": trade_count,
            "win_rate": round(win_rate, 6),
            "avg_win_r": round(avg_win_r, 6),
            "avg_loss_r": round(avg_loss_r, 6),
            "expectancy": round(expectancy_r, 6),
            "profit_factor": (round(profit_factor, 6)
                              if profit_factor != float("inf") else None),
            "max_drawdown": round(max_drawdown, 6),
            "execution_rate": round(execution_rate, 6),
            "executed_count": executed,
            "total_signals": total,
            "gross_profit": round(gross_profit, 4),
            "gross_loss": round(gross_loss, 4),
        }

    # ===================================================================
    # 平仓配对(FIFO)
    # ===================================================================

    def _pair_closed_trades(self, execs: list[dict]) -> list[dict]:
        """把 BUY/SELL 成交按每股 FIFO 配对为已平仓交易。

        返回 [{stock_code, entry_date, entry_price, exit_date, exit_price,
               quantity, pnl, plan_item_id, buy_exec_id}]。
        SELL 按 FIFO 消耗最早未平仓的 BUY。
        """
        # 按 trade_date 升序(同日按 id 升序)处理,保证 FIFO
        ordered = sorted(execs, key=lambda e: (e["trade_date"], e["id"]))
        open_lots: dict[str, list[dict]] = {}  # stock_code -> [buy lots]
        closed: list[dict] = []
        for e in ordered:
            code = e["stock_code"]
            side = e["side"]
            qty = int(e["quantity"])
            price = float(e["price"])
            if side == "BUY":
                open_lots.setdefault(code, []).append({
                    "entry_date": e["trade_date"], "entry_price": price,
                    "remaining": qty, "plan_item_id": e.get("plan_item_id"),
                    "buy_exec_id": e["id"],
                })
            elif side == "SELL":
                lots = open_lots.get(code, [])
                to_close = qty
                while to_close > 0 and lots:
                    lot = lots[0]
                    take = min(lot["remaining"], to_close)
                    pnl = (price - lot["entry_price"]) * take
                    closed.append({
                        "stock_code": code,
                        "entry_date": lot["entry_date"],
                        "entry_price": lot["entry_price"],
                        "exit_date": e["trade_date"],
                        "exit_price": price,
                        "quantity": take,
                        "pnl": pnl,
                        "plan_item_id": lot["plan_item_id"],
                        "buy_exec_id": lot["buy_exec_id"],
                    })
                    lot["remaining"] -= take
                    to_close -= take
                    if lot["remaining"] <= 0:
                        lots.pop(0)
                # 没有对应 BUY 的 SELL(裸卖空)忽略,不形成平仓交易
        return closed

    def _enrich_r(self, closed: list[dict],
                  stop_by_plan_item: dict) -> list[dict]:
        """为每笔平仓交易附加 r_multiple(需 entry 对应 plan_item 的 stop_price)。"""
        for t in closed:
            pid = t.get("plan_item_id")
            stop = stop_by_plan_item.get(pid) if pid is not None else None
            if stop is not None and stop > 0 and t["entry_price"] > 0:
                per_share_risk = t["entry_price"] - stop
                if per_share_risk > 0:
                    t["r_multiple"] = (t["exit_price"] - t["entry_price"]) / per_share_risk
                else:
                    t["r_multiple"] = None
            else:
                t["r_multiple"] = None
        return closed

    # ===================================================================
    # 数据加载辅助
    # ===================================================================

    def _load_stop_prices(self, account_id: int, start: str,
                          end: str) -> dict[int, float]:
        """加载区间内所有 plan_items 的 stop_price(按 plan_item_id 索引)。

        遍历区间内 plan_runs 的 items(回溯 plan_item_id → stop_price)。
        """
        stop_map: dict[int, float] = {}
        runs = self.repo.list_plan_runs()
        for run in runs:
            sd = run.get("signal_date") or ""
            if sd < start or sd > end:
                continue
            items = self.repo.get_plan_items(run["id"])
            for it in items:
                sp = it.get("stop_price")
                pid = it.get("id")
                if pid is not None and sp is not None:
                    stop_map[pid] = float(sp)
        return stop_map

    def _max_drawdown(self, account_id: int, start: str, end: str) -> float:
        """从 equity_snapshots 取区间内最大回撤(每行已存 drawdown)。"""
        snaps = self.repo.list_equity_snapshots(account_id, start=start, end=end)
        if not snaps:
            return 0.0
        return max((float(s.get("drawdown") or 0.0) for s in snaps), default=0.0)

    def _execution_rate(self, account_id: int, start: str, end: str,
                        execs: list[dict]) -> tuple[int, int]:
        """执行率:已执行 CONDITIONAL_BUY plan_items / 总 CONDITIONAL_BUY plan_items。

        已执行 = 有 BUY 成交关联(plan_item_id)的 plan_items 数(去重)。
        总数 = 区间内所有 plan_runs 中 action=CONDITIONAL_BUY 的 plan_items 数。
        """
        runs = self.repo.list_plan_runs()
        total = 0
        all_cb_items: list[dict] = []
        for run in runs:
            sd = run.get("signal_date") or ""
            if sd < start or sd > end:
                continue
            for it in self.repo.get_plan_items(run["id"]):
                if it.get("action") == "CONDITIONAL_BUY":
                    total += 1
                    all_cb_items.append(it)
        if total == 0:
            return 0, 0
        executed_plan_ids = {
            e["plan_item_id"] for e in execs
            if e.get("side") == "BUY" and e.get("plan_item_id") is not None
        }
        executed = sum(1 for it in all_cb_items if it.get("id") in executed_plan_ids)
        return executed, total
