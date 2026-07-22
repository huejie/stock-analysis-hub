"""成交服务:录入 + 原子更新(现金/持仓/审计)+ T+1 语义(spec §8.8/§12.5)。

T+1:买入当日 available_quantity 不变;跨交易日 lazy 滚动。
原子性:repo 层无事务封装,但本服务按"先写 execution,再更新 account/position"顺序,
失败时由调用方处理(execution 已幂等,重复 POST 安全)。
"""
import json


class ExecutionService:
    def __init__(self, repo):
        self.repo = repo

    def record_execution(self, *, account_id: int, stock_code: str, side: str,
                         trade_date: str, price: float, quantity: int,
                         commission: float = 0, tax: float = 0,
                         client_execution_id: str, note: str = "",
                         plan_item_id: int | None = None) -> dict:
        """录入成交,原子更新现金/持仓/审计。"""
        # 幂等检查:通过 repo 查重(避免在 service 层开 sqlite 连接绕过 repo)
        existing = self.repo.get_execution_by_client_id(client_execution_id)
        if existing:
            return {"execution": existing, "position": self.repo.get_position(account_id, stock_code)}

        account = self.repo.get_account(account_id)
        if not account:
            raise ValueError(f"账户 {account_id} 不存在")

        trade_value = price * quantity
        position = self.repo.get_position(account_id, stock_code)

        if side == "BUY":
            total_cost = trade_value + commission + tax
            if account["cash_balance"] < total_cost:
                raise ValueError(
                    f"现金不足:需要 {total_cost},可用 {account['cash_balance']}"
                )
            # 更新现金
            self.repo.update_account(account_id, {"cash_balance": account["cash_balance"] - total_cost})
            # 更新持仓(平均成本混合)
            new_qty = (position["quantity"] if position else 0) + quantity
            if position:
                old_cost = position["quantity"] * position["average_cost"]
                new_avg = (old_cost + trade_value) / new_qty
                # T+1: available 不变
                self.repo.upsert_position(
                    account_id=account_id, stock_code=stock_code,
                    quantity=new_qty, available_quantity=position["available_quantity"],
                    average_cost=new_avg, stock_name=position.get("stock_name"),
                    initial_stop=position.get("initial_stop"),
                    trailing_stop=position.get("trailing_stop"),
                    opened_at=position.get("opened_at"),
                )
            else:
                self.repo.upsert_position(
                    account_id=account_id, stock_code=stock_code,
                    quantity=new_qty, available_quantity=0,  # T+1
                    average_cost=price,
                )

        elif side == "SELL":
            if not position or position["quantity"] == 0:
                raise ValueError(f"无 {stock_code} 持仓,无法卖出")
            if position["available_quantity"] < quantity:
                raise ValueError(
                    f"可卖数量不足:需要 {quantity},可用 {position['available_quantity']}"
                )
            net_proceeds = trade_value - commission - tax
            # 更新现金
            self.repo.update_account(account_id, {"cash_balance": account["cash_balance"] + net_proceeds})
            # 更新持仓
            new_qty = position["quantity"] - quantity
            new_avail = position["available_quantity"] - quantity
            if new_qty == 0:
                self.repo.delete_position(account_id, stock_code)
            else:
                self.repo.upsert_position(
                    account_id=account_id, stock_code=stock_code,
                    quantity=new_qty, available_quantity=new_avail,
                    average_cost=position["average_cost"],
                    stock_name=position.get("stock_name"),
                    initial_stop=position.get("initial_stop"),
                    trailing_stop=position.get("trailing_stop"),
                    opened_at=position.get("opened_at"),
                )
        else:
            raise ValueError(f"未知 side: {side}(仅支持 BUY/SELL)")

        # 写 execution(repo.create_execution 也是幂等的,但前面已确认不重复)
        execution = self.repo.create_execution(
            account_id=account_id, stock_code=stock_code, side=side,
            trade_date=trade_date, price=price, quantity=quantity,
            commission=commission, tax=tax, client_execution_id=client_execution_id,
            note=note, plan_item_id=plan_item_id,
        )
        # 审计
        self.repo.write_audit_log(
            actor="user", action=f"EXECUTION_{side}", entity_type="execution",
            entity_id=str(execution["id"]),
            after_json=json.dumps(execution, ensure_ascii=False),
        )
        return {"execution": execution, "position": self.repo.get_position(account_id, stock_code)}

    def roll_t1_available(self, account_id: int, new_trade_date: str) -> None:
        """T+1 滚动:把所有持仓的 available_quantity 设为 quantity。

        由调用方在新交易日首次访问时触发(lazy compute)。
        """
        positions = self.repo.get_positions(account_id)
        for p in positions:
            if p["quantity"] > 0 and p["available_quantity"] != p["quantity"]:
                self.repo.upsert_position(
                    account_id=account_id, stock_code=p["stock_code"],
                    quantity=p["quantity"], available_quantity=p["quantity"],
                    average_cost=p["average_cost"], stock_name=p.get("stock_name"),
                    initial_stop=p.get("initial_stop"),
                    trailing_stop=p.get("trailing_stop"),
                    opened_at=p.get("opened_at"),
                )
