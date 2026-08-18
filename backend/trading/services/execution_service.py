"""成交服务:原子录入 + T+1 语义(spec §8.8/§12.5)。

T+1:买入当日 available_quantity 不变;跨交易日 lazy 滚动。
原子性:现金、持仓、execution 和 audit 由 Repository 在同一事务内写入。
"""
from datetime import date
from typing import Callable


class ExecutionService:
    def __init__(self, repo):
        self.repo = repo

    def record_execution(self, *, account_id: int, stock_code: str, side: str,
                         trade_date: str, price: float, quantity: int,
                         commission: float = 0, tax: float = 0,
                         client_execution_id: str, note: str = "",
                         plan_item_id: int | None = None) -> dict:
        """录入成交,原子更新现金/持仓/审计。"""
        return self.repo.record_execution_atomic(
            account_id=account_id, stock_code=stock_code, side=side,
            trade_date=trade_date, price=price, quantity=quantity,
            commission=commission, tax=tax, client_execution_id=client_execution_id,
            note=note, plan_item_id=plan_item_id,
        )

    def roll_t1_available(self, account_id: int, new_trade_date: str) -> int:
        """T+1 滚动:解锁目标日前买入的持仓，保留当日买入冻结量。

        由调用方在新交易日首次访问时触发(lazy compute)。
        """
        return self.repo.roll_t1_available_atomic(
            account_id, new_trade_date
        )

    def reconcile_orders(
        self,
        trade_date: date | str,
        *,
        check_fence: Callable[[], None] | None = None,
    ) -> dict:
        """Roll T+1 availability and persist missed conditional buys.

        Only plan items whose target trading day matches ``trade_date`` are
        considered.  The PENDING -> NOT_FILLED transition is compare-and-set,
        so replaying the job is idempotent.
        """
        target = (
            date.fromisoformat(trade_date)
            if isinstance(trade_date, str)
            else trade_date
        )
        target_str = target.isoformat()
        fence = check_fence or (lambda: None)

        accounts_rolled = 0
        for account in self.repo.list_accounts(active_only=True):
            fence()
            if self.roll_t1_available(account["id"], target_str):
                accounts_rolled += 1

        items_not_filled = 0
        for run in self.repo.list_plan_runs():
            fence()
            if run.get("target_trade_date") != target_str:
                continue
            if run.get("status") not in ("READY", "PARTIAL", "PUBLISHED"):
                continue
            for item in self.repo.get_plan_items(run["id"]):
                fence()
                if item.get("action") != "CONDITIONAL_BUY":
                    continue
                if item.get("execution_status") != "PENDING":
                    continue
                do_not_chase = item.get("do_not_chase_price")
                if do_not_chase is None:
                    continue
                bars = self.repo.get_daily_bars(
                    [item["stock_code"]], target, target
                )
                if not bars:
                    continue
                open_price = bars[0].get("open")
                if open_price is None or open_price <= do_not_chase:
                    continue
                fence()
                if self.repo.update_plan_item_execution_status(
                    item["id"], "PENDING", "NOT_FILLED"
                ):
                    items_not_filled += 1

        return {
            "accounts_rolled": accounts_rolled,
            "items_not_filled": items_not_filled,
        }
