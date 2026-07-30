"""策略服务:版本 CRUD + 激活状态机(spec §14.4)。

激活门禁(spec §14.4 第一版默认门槛):
- 至少 100 笔历史交易(trade_count >= 100)。
- 期望值(expectancy)大于 0。
- 盈利因子(profit_factor)大于 1。
- 最大回撤不超过阈值(max_drawdown <= MAX_DRAWDOWN_GATE,默认 0.30)。
- 不存在单一股票或单一月份贡献大部分利润(concentration 检查)。

必须先有回测数据:无回测 → ValueError "无回测数据,请先运行回测"。
门槛通过不代表未来盈利保证,只代表该版本具备进入模拟或小仓位验证的资格。

设计要点:
- 同 strategy_code 同一时间只允许一个 ACTIVE(由 Repository 事务保证)。
- 激活新版本时,旧 ACTIVE 版本自动 RETIRED。
- 已 RETIRED 的版本不可再次激活。
"""

# §14.4 默认最大回撤门槛(用户可按账户阈值配置;此处取较宽松的 30%,
# 因为 Phase 5 单策略回测样本可能波动较大。生产环境应按 account.max_drawdown_limit)。
MAX_DRAWDOWN_GATE = 0.30
MIN_TRADE_COUNT = 100


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

    def activate_strategy(self, version_id: int, *,
                          max_drawdown_gate: float = MAX_DRAWDOWN_GATE,
                          min_trade_count: int = MIN_TRADE_COUNT) -> dict:
        """激活策略(spec §14.4 回测门禁)。

        状态机:
        - DRAFT/ACTIVE → ACTIVE(门禁通过后)
        - RETIRED → 拒绝(已退役不可再激活)
        门禁:取该 strategy_version_id 最近一次 SUCCEEDED 回测的指标,
        全部门槛通过才激活;任一失败 → ValueError 列出失败门(→ 400/422)。
        无回测数据 → ValueError "无回测数据,请先运行回测"。
        """
        strat = self.repo.get_strategy(version_id)
        if not strat:
            raise ValueError(f"策略版本 {version_id} 不存在")
        if strat["status"] == "RETIRED":
            raise ValueError("已退役策略不能激活")

        # ---- 回测门禁(spec §14.4)----
        gate_errors = self._check_activation_gate(
            version_id, max_drawdown_gate=max_drawdown_gate,
            min_trade_count=min_trade_count,
        )
        if gate_errors:
            # 无回测数据 → 特定消息;否则列出失败门
            if gate_errors == ["NO_BACKTEST"]:
                raise ValueError("无回测数据,请先运行回测")
            raise ValueError("策略激活门禁未通过: " + "; ".join(gate_errors))

        retired_prev = self.repo.activate_strategy(version_id)
        return {
            "id": version_id,
            "status": "ACTIVE",
            "warnings": [],   # 门禁通过,无 warning(stub 时代结束)
            "retired_previous_id": retired_prev,
        }

    def _check_activation_gate(self, version_id: int, *,
                               max_drawdown_gate: float,
                               min_trade_count: int) -> list[str]:
        """检查 §14.4 门禁,返回失败门描述列表(空 = 全部通过)。

        特殊返回 ["NO_BACKTEST"] 表示无任何成功回测。
        """
        runs = self.repo.list_backtest_runs(strategy_version_id=version_id)
        succeeded = [r for r in runs if r["status"] == "SUCCEEDED"]
        if not succeeded:
            return ["NO_BACKTEST"]
        # 取最近一次(id 最大 = 最新)
        latest = succeeded[0]
        metrics = latest.get("metrics") or {}

        errors: list[str] = []
        trade_count = int(metrics.get("trade_count") or 0)
        if trade_count < min_trade_count:
            errors.append(f"交易笔数 {trade_count} < {min_trade_count}")

        expectancy = metrics.get("expectancy")
        if expectancy is None or expectancy <= 0:
            errors.append(f"期望值 {expectancy} 不大于 0")

        pf = metrics.get("profit_factor")
        # profit_factor=None 表示 inf(全胜),视为通过(>1)
        if pf is not None and pf <= 1:
            errors.append(f"盈利因子 {pf} 不大于 1")

        max_dd = metrics.get("max_drawdown")
        if max_dd is not None and max_dd > max_drawdown_gate:
            errors.append(f"最大回撤 {max_dd:.2%} 超过门槛 {max_drawdown_gate:.2%}")

        conc = metrics.get("concentration") or {}
        if conc.get("concentrated_stock"):
            errors.append(
                f"单一股票贡献利润 {conc.get('max_stock_share', 0):.0%} 超过 50%"
            )
        if conc.get("concentrated_month"):
            errors.append(
                f"单一月份贡献利润 {conc.get('max_month_share', 0):.0%} 超过 50%"
            )
        return errors
