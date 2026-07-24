"""策略服务:版本 CRUD + 激活状态机(spec §14.4)。

Phase 3 的策略激活门禁是 stub:不校验回测(依赖 Phase 5 回测引擎),
仅在响应中返回 warning。Phase 5 就绪后在此处接入真校验。

设计要点:
- 同 strategy_code 同一时间只允许一个 ACTIVE(由 Repository 事务保证)。
- 激活新版本时,旧 ACTIVE 版本自动 RETIRED。
- 已 RETIRED 的版本不可再次激活。
"""
class StrategyService:
    def __init__(self, repo):
        self.repo = repo

    def create_strategy(self, *, strategy_code: str, name: str,
                        params_json: dict) -> dict:
        """创建策略版本(DRAFT)。params_hash 命中则复用(reused=True)。"""
        return self.repo.create_strategy_version(
            strategy_code=strategy_code, name=name, params_json=params_json,
        )

    def get_strategy(self, version_id: int) -> dict | None:
        return self.repo.get_strategy(version_id)

    def list_strategies(self, strategy_code: str | None = None) -> list[dict]:
        return self.repo.list_strategies(strategy_code=strategy_code)

    def get_active_strategy(self, strategy_code: str) -> dict | None:
        return self.repo.get_active_strategy(strategy_code)

    def activate_strategy(self, version_id: int) -> dict:
        """激活策略。

        门禁 stub:Phase 5 回测引擎就绪后启用真校验。当前只记录 warning。
        状态机:
        - DRAFT/ACTIVE → ACTIVE
        - RETIRED → 拒绝(已退役不可再激活)
        激活时同 strategy_code 的旧 ACTIVE 自动 RETIRED。
        """
        strat = self.repo.get_strategy(version_id)
        if not strat:
            raise ValueError(f"策略版本 {version_id} 不存在")
        if strat["status"] == "RETIRED":
            raise ValueError("已退役策略不能激活")
        warnings = ["激活门禁未验证(Phase 5 回测引擎就绪后启用)"]
        retired_prev = self.repo.activate_strategy(version_id)
        return {
            "id": version_id,
            "status": "ACTIVE",
            "warnings": warnings,
            "retired_previous_id": retired_prev,
        }
