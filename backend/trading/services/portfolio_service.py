"""组合服务:净值快照 + 风险预算 + 暴露计算(spec §8.7/§12.2)。

净值公式(标准定义,spec 未明确):
  cash         = account.cash_balance
  market_value = SUM(position.quantity * close_price)
  total_equity = cash + market_value
  exposure     = market_value / total_equity
  peak_equity  = MAX(prior_peak, total_equity)  单调非递减
  drawdown     = (peak_equity - total_equity) / peak_equity
"""
from datetime import date


class PortfolioService:
    def __init__(self, repo):
        self.repo = repo

    def compute_equity_snapshot(self, account_id: int, trade_date: date) -> dict:
        """计算(不保存)指定交易日的净值快照。

        持仓缺失行情时抛异常(阻断,不"凑"结论)。
        """
        account = self.repo.get_account(account_id)
        if not account:
            raise ValueError(f"账户 {account_id} 不存在")

        positions = self.repo.get_positions(account_id)
        open_positions = [p for p in positions if p["quantity"] > 0]

        # 取所有持仓股票在 trade_date 的收盘价
        codes = [p["stock_code"] for p in open_positions]
        bars = {}
        if codes:
            bar_rows = self.repo.get_daily_bars(codes, trade_date, trade_date)
            for r in bar_rows:
                if r["trade_date"] == trade_date.isoformat():
                    bars[r["stock_code"]] = r["close"]

        # 检查缺失
        missing = [c for c in codes if c not in bars]
        if missing:
            raise ValueError(
                f"持仓行情缺失,无法计算净值: {', '.join(missing)} (trade_date={trade_date})"
            )

        cash = account["cash_balance"]
        market_value = sum(p["quantity"] * bars[p["stock_code"]] for p in open_positions)
        total_equity = cash + market_value
        exposure = (market_value / total_equity) if total_equity > 0 else 0.0

        # peak 从上一个快照取(单调非递减)
        prior = self.repo.get_latest_equity_snapshot(account_id)
        prior_peak = prior["peak_equity"] if prior else total_equity
        peak_equity = max(prior_peak, total_equity)
        drawdown = (peak_equity - total_equity) / peak_equity if peak_equity > 0 else 0.0

        return {
            "account_id": account_id,
            "trade_date": trade_date.isoformat(),
            "cash": cash,
            "market_value": market_value,
            "total_equity": total_equity,
            "exposure": exposure,
            "peak_equity": peak_equity,
            "drawdown": drawdown,
        }

    def compute_and_save_equity_snapshot(self, account_id: int, trade_date: date) -> dict:
        """计算并保存净值快照(返回含 created_at,匹配 EquitySnapshotResponse)。"""
        snap = self.compute_equity_snapshot(account_id, trade_date)
        self.repo.upsert_equity_snapshot(
            account_id=account_id, trade_date=snap["trade_date"],
            cash=snap["cash"], market_value=snap["market_value"],
            total_equity=snap["total_equity"], exposure=snap["exposure"],
            peak_equity=snap["peak_equity"], drawdown=snap["drawdown"],
        )
        # 保存后回读,取 DDL 生成的 created_at(修复 500: schema 要求该字段)
        saved = self.repo.get_equity_snapshot(account_id, snap["trade_date"])
        if saved:
            snap["created_at"] = saved.get("created_at") or ""
        else:
            from datetime import datetime
            snap["created_at"] = datetime.now().isoformat(timespec="seconds")
        return snap

    def compute_sector_exposure(self, account_id: int, trade_date: date) -> list[dict]:
        """按行业聚合持仓暴露(spec §8.2 max_sector_exposure 检查用)。

        sector 标签来自 trade_stock_pool_items.sector_name(Phase 1);
        未匹配的按 "未知" 聚合(spec §8.2:行业缺失按未知统一计算)。
        """
        snapshot = self.compute_equity_snapshot(account_id, trade_date)
        total_equity = snapshot["total_equity"]
        if total_equity <= 0:
            return []

        positions = [p for p in self.repo.get_positions(account_id) if p["quantity"] > 0]
        bars = {}
        if positions:
            codes = [p["stock_code"] for p in positions]
            for r in self.repo.get_daily_bars(codes, trade_date, trade_date):
                if r["trade_date"] == trade_date.isoformat():
                    bars[r["stock_code"]] = r["close"]

        sector_map: dict[str, float] = {}
        for p in positions:
            close = bars.get(p["stock_code"], 0)
            mv = p["quantity"] * close
            # Phase 2 简化:sector 标签暂未从 pool_items 接入,统一按"未知"聚合。
            # Phase 3 接入 regime + sector 后替换为真实行业标签。
            sector = "未知"
            sector_map[sector] = sector_map.get(sector, 0) + mv

        return [
            {"sector": s, "market_value": mv,
             "exposure_pct": round(mv / total_equity, 6)}
            for s, mv in sector_map.items()
        ]

    def check_risk_limits(self, account_id: int, trade_date: date,
                         proposed_buy_value: float = 0,
                         proposed_stock_sector: str = "未知") -> dict:
        """检查新增买入是否违反账户级风险限制(spec §8.2)。

        返回 {"violations": [...], "current_exposure": ..., "post_exposure": ...}。
        Phase 2 只检查绝对限制;regime 条件限制由 Phase 3 注入。
        """
        account = self.repo.get_account(account_id)
        if not account:
            raise ValueError(f"账户 {account_id} 不存在")

        snap = self.compute_equity_snapshot(account_id, trade_date)
        current_mv = snap["market_value"]
        total_equity = snap["total_equity"]
        post_mv = current_mv + proposed_buy_value
        post_exposure = post_mv / total_equity if total_equity > 0 else 0

        violations = []
        if post_exposure > account["max_total_exposure"]:
            violations.append({
                "code": "TOTAL_EXPOSURE_EXCEEDED",
                "message": f"总仓位 {post_exposure:.2%} 超过上限 {account['max_total_exposure']:.2%}",
            })

        # 单股仓位(假设 proposed 全部是一只)
        single_pct = proposed_buy_value / total_equity if total_equity > 0 else 0
        if single_pct > account["max_single_position"]:
            violations.append({
                "code": "SINGLE_POSITION_EXCEEDED",
                "message": f"单股仓位 {single_pct:.2%} 超过上限 {account['max_single_position']:.2%}",
            })

        # 行业暴露
        sector_exp = self.compute_sector_exposure(account_id, trade_date)
        sector_map = {s["sector"]: s["market_value"] for s in sector_exp}
        current_sector_mv = sector_map.get(proposed_stock_sector, 0)
        post_sector_pct = (current_sector_mv + proposed_buy_value) / total_equity if total_equity > 0 else 0
        if post_sector_pct > account["max_sector_exposure"]:
            violations.append({
                "code": "SECTOR_EXPOSURE_EXCEEDED",
                "message": f"行业 {proposed_stock_sector} 仓位 {post_sector_pct:.2%} 超过上限 {account['max_sector_exposure']:.2%}",
            })

        # 持仓数量
        positions = [p for p in self.repo.get_positions(account_id) if p["quantity"] > 0]
        # 如果是新股票(不在现有持仓),+1
        post_count = len(positions) + (0 if proposed_buy_value == 0 else 1)
        if post_count > account["max_positions"]:
            violations.append({
                "code": "POSITION_COUNT_EXCEEDED",
                "message": f"持仓数 {post_count} 超过上限 {account['max_positions']}",
            })

        # 回撤暂停
        if snap["drawdown"] >= account["max_drawdown_limit"]:
            violations.append({
                "code": "DRAWDOWN_PAUSE",
                "message": f"回撤 {snap['drawdown']:.2%} 达到暂停阈值 {account['max_drawdown_limit']:.2%},需人工复盘",
            })

        return {
            "violations": violations,
            "current_exposure": snap["exposure"],
            "post_exposure": round(post_exposure, 6),
            "total_equity": total_equity,
            "drawdown": snap["drawdown"],
        }
