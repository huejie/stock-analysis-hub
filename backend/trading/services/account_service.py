"""账户服务:CRUD + 唯一 active 账户约束(spec §3.2 单主账户)。"""
import json


class AccountService:
    def __init__(self, repo):
        self.repo = repo

    def create_account(self, *, name: str, initial_equity: float, cash_balance: float,
                       risk_per_trade: float = 0.005, max_single_position: float = 0.15,
                       max_total_exposure: float = 0.60, max_sector_exposure: float = 0.30,
                       max_positions: int = 5, max_drawdown_limit: float = 0.08,
                       is_active: bool = True) -> dict:
        acc = self.repo.create_account(
            name=name, initial_equity=initial_equity, cash_balance=cash_balance,
            risk_per_trade=risk_per_trade, max_single_position=max_single_position,
            max_total_exposure=max_total_exposure, max_sector_exposure=max_sector_exposure,
            max_positions=max_positions, max_drawdown_limit=max_drawdown_limit,
            is_active=is_active,
        )
        self.repo.write_audit_log(
            actor="user", action="CREATE_ACCOUNT", entity_type="account",
            entity_id=str(acc["id"]), after_json=json.dumps(acc, ensure_ascii=False),
        )
        # 单主账户约束:如果新建为 active,把其他设为 inactive
        if is_active:
            self._ensure_single_active(acc["id"])
        return acc

    def get_account(self, account_id: int) -> dict | None:
        return self.repo.get_account(account_id)

    def list_accounts(self, active_only: bool = False) -> list[dict]:
        return self.repo.list_accounts(active_only=active_only)

    def update_account(self, account_id: int, fields: dict) -> dict:
        """更新账户。initial_equity 不可改。"""
        if "initial_equity" in fields:
            raise ValueError("initial_equity 创建后不可修改(如需调整初始资金,请新建账户)")
        before = self.repo.get_account(account_id)
        self.repo.update_account(account_id, fields)
        after = self.repo.get_account(account_id)
        self.repo.write_audit_log(
            actor="user", action="UPDATE_ACCOUNT", entity_type="account",
            entity_id=str(account_id),
            before_json=json.dumps(before, ensure_ascii=False) if before else None,
            after_json=json.dumps(after, ensure_ascii=False),
        )
        # 如果设为 active,其他账户 inactive
        if fields.get("is_active") is True:
            self._ensure_single_active(account_id)
        return after

    def activate_account(self, account_id: int) -> dict:
        """激活指定账户,其他自动 inactive。"""
        return self.update_account(account_id, {"is_active": True})

    def _ensure_single_active(self, active_id: int) -> None:
        """保证只有一个 active 账户。"""
        for acc in self.repo.list_accounts(active_only=True):
            if acc["id"] != active_id:
                self.repo.update_account(acc["id"], {"is_active": False})
