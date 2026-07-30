"""回测服务(spec §14)。

核心职责:
- 遍历交易日 t(信号日),只使用 trade_date <= t 的 K 线(防未来函数,§14.1)。
- 复用策略纯函数(market_regime / scoring / entry_rules / exit_rules)生成信号。
- 按 §14.2 日线成交模拟在 t+1 撮合:
  * 开盘 > do_not_chase_price → 不成交(NOT_FILLED)
  * 最高 >= trigger_price → 成交价 = max(开盘, 触发价) + 滑点
  * 同日触及止损和止盈 → 保守假设止损先发生
  * T+1:买入当日不能卖
  * 停牌 / 一字涨跌停 → 不可成交
  * 佣金 / 最低佣金 / 印花税 / 滑点 全部参数化
- 统计 §14.3 核心指标并持久化到 trade_backtest_runs + trade_backtest_trades。

Phase 5 简化:
- 市场宽度(breadth_ratio=None,降级,与 PlanService 一致)。
- 分组表现(按 regime / 评分 / 行业 / 持仓周期)留 TODO,只实现核心指标。
- 基准对比留 TODO(只输出绝对累计收益)。
- 同步运行(无异步任务队列)。

与 PlanService 的差异:
PlanService 耦合账户/组合/风控/现金,适合"给定真实账户生成今日计划"。
回测需要独立账本(initial_equity 起步、随成交滚动),且要在长区间逐日模拟,
因此本服务自带轻量持仓/现金状态机,不依赖 PortfolioService/AccountService。
策略评分/入场/退出逻辑与 PlanService 保持一致(复用同一批纯函数 + 同样的降级默认值),
以保证回测信号与实盘计划可解释性一致。
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..domain import DailyBar
from ..position_sizing import (
    compute_buy_quantity,
    compute_stop_price,
    effective_risk_per_trade,
)
from ..strategies.entry_rules import compute_trigger_prices, evaluate_entry
from ..strategies.exit_rules import compute_trailing_stop, evaluate_exit
from ..strategies.market_regime import compute_market_score
from ..strategies.scoring import compute_score
from .indicator_service import compute_indicators

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK = "000300.SH"

# 默认费率(spec §14.2:启用回测前应由用户按券商实际费率确认)
DEFAULT_FEE_PARAMS = {
    "commission_rate": 0.0003,   # 双边佣金费率
    "min_commission": 5.0,       # 单笔最低佣金(元)
    "stamp_tax": 0.001,          # 印花税(仅卖出)
    "slippage": 0.0,             # 滑点(元/股,成交价叠加)
}

# 与 PlanService 一致的降级默认值(保证回测信号与实盘可解释性一致)
_DEFAULT_POOL_RETURN_PERCENTILE = 0.6
_DEFAULT_EXCESS_20D = 0.01
_DEFAULT_EXCESS_60D = 0.01
_DEFAULT_VOLUME_RATIO = 1.5
_DEFAULT_AUXILIARY_BONUS = 0
_DEFAULT_VOLATILITY_PERCENTILE = 0.50
_DEFAULT_MIN_SCORE = 70
_DEFAULT_BASE_RISK = 0.005
_DEFAULT_MAX_SINGLE_POSITION = 0.15
_DEFAULT_MAX_POSITIONS = 5

# 状态
_STATUS_RUNNING = "RUNNING"
_STATUS_SUCCEEDED = "SUCCEEDED"
_STATUS_FAILED = "FAILED"

# 一字涨跌停判定阈值(spec §14.2:一字涨跌停不可成交)
_LIMIT_LOCK_THRESHOLD = 0.0001  # open == high == low 视为一字,价格比较容差


@dataclass
class _OpenPosition:
    """回测中的开仓状态(内存,不写持仓表)。"""
    stock_code: str
    quantity: int
    entry_price: float
    stop_price: float               # 当前生效止损(初始止损)
    entry_date: str
    signal_date: str                # 信号日(用于审计)
    trailing_stop: float | None = None
    target_2r_price: float | None = None
    highest_close: float = 0.0      # 持仓期最高收盘(用于移动止损)
    available: bool = False         # T+1:信号日次日买入,下一个交易日才能卖


@dataclass
class _TradeRecord:
    """已平仓的回测交易(写入 trade_backtest_trades)。"""
    stock_code: str
    signal_date: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: int
    pnl: float
    r_multiple: float
    exit_reason: str
    details: dict = field(default_factory=dict)


class BacktestService:
    def __init__(self, repo):
        self.repo = repo

    def get_backtest(self, run_id: int) -> dict | None:
        """返回回测运行 + trades(spec §11.2 GET /backtests/{id})。"""
        run = self.repo.get_backtest_run(run_id)
        if run is None:
            return None
        trades = self.repo.list_backtest_trades(run_id)
        return {
            "id": run["id"], "job_id": run["job_id"],
            "strategy_version_id": run["strategy_version_id"],
            "stock_pool_version_id": run["stock_pool_version_id"],
            "start_date": run["start_date"], "end_date": run["end_date"],
            "initial_equity": run["initial_equity"],
            "fee_params": run["fee_params"], "status": run["status"],
            "metrics": run["metrics"], "equity_curve": run["equity_curve"],
            "trades": trades, "created_at": run["created_at"],
            "finished_at": run.get("finished_at"),
        }

    def list_backtest_runs(self, strategy_version_id: int | None = None) -> list[dict]:
        return self.repo.list_backtest_runs(strategy_version_id=strategy_version_id)

    def run_backtest(self, *, strategy_version_id: int, stock_pool_version_id: int,
                     start_date, end_date, initial_equity: float = 100_000,
                     fee_params: dict | None = None) -> dict:
        """运行回测(spec §14)。

        返回 {id, job_id, status, metrics, trades, equity_curve}。
        """
        start_d = _coerce_date(start_date)
        end_d = _coerce_date(end_date)
        if end_d < start_d:
            raise ValueError("end_date 不能早于 start_date")
        if initial_equity <= 0:
            raise ValueError("initial_equity 必须为正")

        # 加载策略 + 池
        strategy = self.repo.get_strategy(strategy_version_id)
        if strategy is None:
            raise ValueError(f"策略版本 {strategy_version_id} 不存在")
        pool = self.repo.get_stock_pool_version(stock_pool_version_id)
        if pool is None:
            raise ValueError(f"股票池版本 {stock_pool_version_id} 不存在")
        pool_codes = [it["stock_code"] for it in pool["items"]]

        params = strategy.get("params_json") or {}
        min_score = int(params.get("min_score", _DEFAULT_MIN_SCORE))
        base_risk = float(params.get("risk_per_trade", _DEFAULT_BASE_RISK))
        max_single = float(params.get("max_single_position", _DEFAULT_MAX_SINGLE_POSITION))
        max_positions = int(params.get("max_positions", _DEFAULT_MAX_POSITIONS))

        fees = dict(DEFAULT_FEE_PARAMS)
        if fee_params:
            fees.update({k: v for k, v in fee_params.items() if v is not None})

        # 创建 run 记录(job_id 由调用方在 router 层生成;这里用参数指纹兜底)
        job_key_payload = json.dumps({
            "sv": strategy_version_id, "pv": stock_pool_version_id,
            "s": start_d.isoformat(), "e": end_d.isoformat(),
            "eq": initial_equity, "fees": fees,
        }, ensure_ascii=False, sort_keys=True)
        job_id = int(hashlib.sha256(job_key_payload.encode()).hexdigest()[:12], 16)
        created = self.repo.create_backtest_run(
            job_id=job_id, strategy_version_id=strategy_version_id,
            stock_pool_version_id=stock_pool_version_id,
            start_date=start_d.isoformat(), end_date=end_d.isoformat(),
            initial_equity=initial_equity, fee_params_json=fees,
            status=_STATUS_RUNNING,
        )
        run_id = created["id"]
        if created["reused"]:
            # 同参数已运行过:直接返回已有结果(幂等)
            return self.get_backtest(run_id)

        try:
            metrics, trades, equity_curve = self._simulate(
                run_id=run_id, pool_codes=pool_codes, benchmark_code=DEFAULT_BENCHMARK,
                start_d=start_d, end_d=end_d, initial_equity=initial_equity,
                fees=fees, min_score=min_score, base_risk=base_risk,
                max_single=max_single, max_positions=max_positions,
            )
        except Exception as exc:
            logger.exception("BacktestService 运行失败 run_id=%s", run_id)
            self.repo.update_backtest_run(run_id, status=_STATUS_FAILED)
            raise

        self.repo.update_backtest_run(
            run_id, status=_STATUS_SUCCEEDED, metrics_json=metrics,
            equity_curve_json=equity_curve,
        )
        # 持久化 trades
        for t in trades:
            self.repo.create_backtest_trade(
                backtest_run_id=run_id, stock_code=t.stock_code,
                signal_date=t.signal_date, entry_date=t.entry_date,
                entry_price=t.entry_price, exit_date=t.exit_date,
                exit_price=t.exit_price, quantity=t.quantity, pnl=t.pnl,
                r_multiple=t.r_multiple, exit_reason=t.exit_reason,
                details=t.details,
            )
        return self.get_backtest(run_id)

    # ===================================================================
    # 核心模拟循环
    # ===================================================================

    def _simulate(self, *, run_id, pool_codes, benchmark_code, start_d, end_d,
                  initial_equity, fees, min_score, base_risk, max_single,
                  max_positions) -> tuple[dict, list[_TradeRecord], list]:
        """执行逐日模拟,返回 (metrics, closed_trades, equity_curve)。"""
        # 收集回测区间内所有可用交易日(基准 K 线存在的日期,升序)。
        # 回测只在"有行情的日子"推进,自然跳过周末/节假日。
        benchmark_all = self._load_all_bars(benchmark_code, start_d, end_d)
        trade_dates = [b.trade_date for b in benchmark_all]
        if not trade_dates:
            # 区间内无任何交易日 → 空回测
            return self._empty_metrics(initial_equity, start_d, end_d), [], \
                [{"trade_date": start_d.isoformat(), "equity": initial_equity}]

        # 预加载所有池内股票 + 基准的 K 线(按代码分组),含预热窗口。
        # 预热窗口:回测起点前 ~130 自然日(约 60 个交易日,够 MA60 + ATR14)。
        # 逐日切片只取 trade_date <= t(防未来函数,§14.1)。
        warmup_start = start_d - timedelta(days=130)
        all_codes = list(dict.fromkeys(pool_codes + [benchmark_code]))
        raw_bars = self.repo.get_daily_bars(all_codes, warmup_start, end_d)
        bars_by_code: dict[str, list[DailyBar]] = {}
        for r in raw_bars:
            d = date.fromisoformat(r["trade_date"])
            bars_by_code.setdefault(r["stock_code"], []).append(DailyBar(
                code=r["stock_code"], trade_date=d,
                open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume=r["volume"], amount=r.get("amount"),
                pre_close=r.get("pre_close"), change_pct=r.get("change_pct"),
                adjust_factor=r.get("adjust_factor") or 1.0,
                source=r.get("source") or "",
            ))
        for code in bars_by_code:
            bars_by_code[code].sort(key=lambda b: b.trade_date)

        cash = initial_equity
        positions: list[_OpenPosition] = []
        closed_trades: list[_TradeRecord] = []
        equity_curve: list[dict] = []
        peak_equity = initial_equity
        max_drawdown = 0.0

        for idx, t in enumerate(trade_dates):
            # ---- T+1 滚动:持仓在 entry_date 之后的第一个交易日才可卖 ----
            # (买入当日 t+1 不可卖;entry_date = t+1,故 t+2 起可卖)。
            for p in positions:
                if not p.available:
                    entry_d = date.fromisoformat(p.entry_date)
                    if t > entry_d:
                        p.available = True

            t_bars = self._slice_up_to(bars_by_code, benchmark_code, t)
            bm_bars = t_bars

            # ---- 步骤 a:持仓退出评估(信号日 t)+ t+1 撮合 ----
            # 退出决策只用 <= t 的数据。撮合发生在 t 的下一根基准交易日。
            next_t = trade_dates[idx + 1] if idx + 1 < len(trade_dates) else None
            cash, closed_trades = self._process_exits(
                positions=positions, cash=cash, t=t, next_t=next_t,
                bars_by_code=bars_by_code, benchmark_bars=bm_bars,
                closed_trades=closed_trades, fees=fees, trade_dates=trade_dates,
                idx=idx,
            )

            # ---- 步骤 b:市场状态 ----
            regime_result = self._regime(bm_bars)

            # ---- 步骤 c:候选入场评估(信号日 t)+ t+1 撮合 ----
            held_codes = {p.stock_code for p in positions}
            cash, positions = self._process_entries(
                positions=positions, pool_codes=pool_codes, t=t, next_t=next_t,
                bars_by_code=bars_by_code, benchmark_bars=bm_bars,
                regime_result=regime_result, min_score=min_score,
                base_risk=base_risk, max_single=max_single,
                max_positions=max_positions, total_equity=self._mark_to_market(
                    positions, bars_by_code, t, cash,
                ), cash=cash, held_codes=held_codes, fees=fees,
            )

            # ---- 步骤 d:净值快照(含现金 + 持仓市值)----
            equity = self._mark_to_market(positions, bars_by_code, t, cash)
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                dd = (peak_equity - equity) / peak_equity
                max_drawdown = max(max_drawdown, dd)
            equity_curve.append({
                "trade_date": t.isoformat(), "equity": round(equity, 4),
                "cash": round(cash, 4),
                "positions": len(positions),
            })

        # ---- 区间结束:强制平仓未结头寸(按末日收盘),统计 ----
        last_t = trade_dates[-1]
        cash, closed_trades = self._force_close_all(
            positions=positions, cash=cash, t=last_t, bars_by_code=bars_by_code,
            closed_trades=closed_trades, fees=fees,
        )
        final_equity = self._mark_to_market([], bars_by_code, last_t, cash)
        if not equity_curve:
            equity_curve.append({"trade_date": last_t.isoformat(),
                                 "equity": round(final_equity, 4),
                                 "cash": round(cash, 4), "positions": 0})
        else:
            equity_curve[-1]["equity"] = round(final_equity, 4)
            equity_curve[-1]["cash"] = round(cash, 4)

        metrics = _compute_metrics(
            closed_trades=closed_trades, initial_equity=initial_equity,
            final_equity=final_equity, max_drawdown=max_drawdown,
            start_d=start_d, end_d=end_d, filled=len(closed_trades),
            total_signals=sum(1 for _ in closed_trades),
        )
        return metrics, closed_trades, equity_curve

    # ===================================================================
    # 退出处理
    # ===================================================================

    def _process_exits(self, *, positions, cash, t, next_t, bars_by_code,
                       benchmark_bars, closed_trades, fees, trade_dates, idx) -> tuple:
        """评估每个持仓在信号日 t 的退出信号,并在 t+1 撮合(spec §14.2)。"""
        if next_t is None:
            return cash, closed_trades  # 末日由 force_close 处理
        still_open: list[_OpenPosition] = []
        for pos in positions:
            bars = self._slice_up_to(bars_by_code, pos.stock_code, t)
            if len(bars) < 2:
                still_open.append(pos)
                continue
            ind = compute_indicators(bars)
            closes = [b.close for b in bars]
            prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
            prev_prev_close = closes[-3] if len(closes) >= 3 else prev_close
            highest_close = max(closes) if closes else ind.close
            atr = ind.atr if ind.atr is not None else 0.0

            exit_result = evaluate_exit(
                close=ind.close,
                initial_stop=pos.stop_price,
                trailing_stop=pos.trailing_stop,
                ma20=ind.ma20, ma60=ind.ma60,
                prev_close=prev_close, prev_prev_close=prev_prev_close,
                entry_price=pos.entry_price, atr=atr,
                highest_close=max(highest_close, pos.highest_close),
                target_2r_price=pos.target_2r_price,
            )
            # 更新移动止损(只上移)+ 最高收盘
            pos.highest_close = max(pos.highest_close, ind.close)
            if exit_result.new_trailing_stop is not None:
                pos.trailing_stop = exit_result.new_trailing_stop

            if exit_result.action in ("EXIT", "REDUCE"):
                # 撮合在 t+1(next_t)。仅可卖持仓(T+1)能平。
                if not pos.available:
                    still_open.append(pos)
                    continue
                # 保守假设:exit_rules 已用收盘价判断触及止损/止盈。
                # §14.2:同日触及止损和止盈时止损先发生 —— 这里以 exit_result.reason
                # 表达(若是 TARGET_2R 则按目标价成交;否则按 t+1 开盘成交)。
                fill = self._fill_exit(
                    bars_by_code=bars_by_code, code=pos.stock_code,
                    next_t=next_t, reason=exit_result.reason,
                    stop_price=pos.stop_price,
                    trailing_stop=pos.trailing_stop,
                    target_2r_price=pos.target_2r_price, fees=fees,
                )
                if fill is None:
                    # t+1 停牌/一字板/未触及 → 继续持有
                    still_open.append(pos)
                    continue
                exit_price, exit_reason = fill
                # REDUCE(target_2r):Phase 5 简化为全部退出(不拆分仓位),
                # 以避免部分平仓后止损/目标价维护的复杂度;reason 保留为 TARGET_2R。
                pnl, _ = self._settle(
                    quantity=pos.quantity, entry_price=pos.entry_price,
                    exit_price=exit_price, side="SELL", fees=fees,
                    proceeds_only=False,
                )
                per_share_risk = pos.entry_price - pos.stop_price
                r_mult = ((exit_price - pos.entry_price) / per_share_risk
                          if per_share_risk > 0 else 0.0)
                cash += pos.quantity * exit_price
                cash -= self._sell_cost(pos.quantity * exit_price, fees)
                closed_trades.append(_TradeRecord(
                    stock_code=pos.stock_code, signal_date=pos.signal_date,
                    entry_date=pos.entry_date, entry_price=pos.entry_price,
                    exit_date=next_t.isoformat(), exit_price=exit_price,
                    quantity=pos.quantity, pnl=pnl, r_multiple=r_mult,
                    exit_reason=exit_reason,
                    details={"regime_at_signal": exit_result.reason},
                ))
                # 不加入 still_open(已平仓)
            else:
                still_open.append(pos)
        # 原地替换 positions 列表的内容(调用方持有同一引用)
        positions.clear()
        positions.extend(still_open)
        return cash, closed_trades

    def _fill_exit(self, *, bars_by_code, code, next_t, reason, stop_price,
                   trailing_stop, target_2r_price, fees) -> tuple | None:
        """t+1 退出撮合(spec §14.2)。

        返回 (exit_price, exit_reason) 或 None(不可成交/未触及)。
        保守顺序:同日触及止损和止盈 → 止损先发生。
        """
        next_bar = self._bar_on(bars_by_code, code, next_t)
        if next_bar is None or _is_untradable(next_bar):
            return None
        opn, hi, lo = next_bar.open, next_bar.high, next_bar.low

        # 保守假设:先判止损是否在 t+1 被触及(low <= stop)
        effective_stop = trailing_stop if trailing_stop is not None else stop_price
        if effective_stop is not None and lo <= effective_stop:
            # 止损先发生:成交价取 max(开盘, 止损价) (§14.2:若开盘已跌破则以开盘计)
            price = max(opn, effective_stop) + fees["slippage"]
            return (price, "INITIAL_STOP" if effective_stop == stop_price else "TRAILING_STOP")

        # 止盈(target_2r):若同日触及,止损已在上面优先处理
        if reason == "TARGET_2R" and target_2r_price is not None and hi >= target_2r_price:
            price = max(opn, target_2r_price) + fees["slippage"]
            return (price, "TARGET_2R")

        # 其它 EXIT 信号(趋势失效/低于 MA60):按 t+1 开盘成交
        if reason in ("TREND_FAILURE", "BELOW_MA60", "FORCE_EXIT"):
            return (opn + fees["slippage"], reason)

        # REDUCE/其它但未在 t+1 明确触及 → 不撮合(继续持有)
        return None

    # ===================================================================
    # 入场处理
    # ===================================================================

    def _process_entries(self, *, positions, pool_codes, t, next_t, bars_by_code,
                         benchmark_bars, regime_result, min_score, base_risk,
                         max_single, max_positions, total_equity, cash,
                         held_codes, fees) -> tuple:
        """评估池内候选在信号日 t 的入场,并在 t+1 撮合(spec §14.2)。"""
        if next_t is None:
            return cash, positions
        # 防守市场不开新仓(spec §8.6)
        if regime_result.regime == "DEFENSE":
            return cash, positions

        bm_closes = [b.close for b in benchmark_bars]
        bm_ret_20d = _ret_over(bm_closes, 20)
        bm_ret_60d = _ret_over(bm_closes, 60)

        scored = []
        for code in pool_codes:
            if code in held_codes:
                continue
            bars = self._slice_up_to(bars_by_code, code, t)
            if len(bars) < 20:
                continue
            ind = compute_indicators(bars)
            closes = [b.close for b in bars]
            stk_ret_20d = _ret_over(closes, 20)
            stk_ret_60d = _ret_over(closes, 60)
            excess_20d = (stk_ret_20d - bm_ret_20d) if stk_ret_20d is not None else 0.0
            excess_60d = (stk_ret_60d - bm_ret_60d) if stk_ret_60d is not None else 0.0
            breakdown = compute_score(
                ind,
                pool_return_percentile=_DEFAULT_POOL_RETURN_PERCENTILE,
                excess_20d=excess_20d if excess_20d > 0 else _DEFAULT_EXCESS_20D,
                excess_60d=excess_60d if excess_60d > 0 else _DEFAULT_EXCESS_60D,
                volume_ratio=_DEFAULT_VOLUME_RATIO,
                has_gap=False, auxiliary_bonus=_DEFAULT_AUXILIARY_BONUS,
            )
            scored.append((code, ind, breakdown))

        scored.sort(key=lambda x: x[2].total, reverse=True)

        eff_risk = effective_risk_per_trade(
            base_risk=base_risk, consecutive_losses=0,
            regime=regime_result.regime, degraded=regime_result.degraded,
        )

        for code, ind, breakdown in scored:
            if len({p.stock_code for p in positions}) >= max_positions:
                break
            entry_result = evaluate_entry(
                regime=regime_result.regime, score=breakdown, ind=ind,
                entry_price=ind.close,
                atr=ind.atr if ind.atr is not None else 0.0,
                low_10d=ind.low_10d,
                portfolio_has_capacity=(
                    len({p.stock_code for p in positions}) < max_positions
                ),
                min_score=min_score,
            )
            if entry_result.action != "CONDITIONAL_BUY":
                continue
            if entry_result.trigger_prices is None:
                continue

            buy = compute_buy_quantity(
                total_equity=total_equity, cash=cash,
                entry_price=ind.close, stop_price=entry_result.stop_price,
                effective_risk_per_trade=eff_risk, max_single_position=max_single,
            )
            if buy.buy_quantity < 100:
                continue

            trigger = entry_result.trigger_prices.trigger_price
            chase = entry_result.trigger_prices.do_not_chase_price

            # t+1 撮合
            next_bar = self._bar_on(bars_by_code, code, next_t)
            if next_bar is None or _is_untradable(next_bar):
                continue
            # §14.2:开盘 > do_not_chase_price → 不成交(NOT_FILLED)
            if next_bar.open > chase:
                continue
            # §14.2:最高 >= 触发价 → 成交价 = max(开盘, 触发价) + 滑点
            if next_bar.high < trigger:
                continue
            fill_price = max(next_bar.open, trigger) + fees["slippage"]
            cost = buy.buy_quantity * fill_price + self._buy_cost(
                buy.buy_quantity * fill_price, fees,
            )
            if cash < cost:
                continue
            cash -= cost
            positions.append(_OpenPosition(
                stock_code=code, quantity=buy.buy_quantity,
                entry_price=fill_price, stop_price=entry_result.stop_price,
                trailing_stop=None, target_2r_price=entry_result.target_2r_price,
                entry_date=next_t.isoformat(), signal_date=t.isoformat(),
                available=False,  # T+1:买入当日不可卖
                highest_close=ind.close,
            ))
        return cash, positions

    # ===================================================================
    # 费用 / 强平 / 估值 辅助
    # ===================================================================

    def _buy_cost(self, trade_value: float, fees: dict) -> float:
        """买入费用:佣金(无印花税)。"""
        comm = max(trade_value * fees["commission_rate"], fees["min_commission"])
        return comm

    def _sell_cost(self, trade_value: float, fees: dict) -> float:
        """卖出费用:佣金 + 印花税。"""
        comm = max(trade_value * fees["commission_rate"], fees["min_commission"])
        return comm + trade_value * fees["stamp_tax"]

    def _settle(self, *, quantity, entry_price, exit_price, side, fees,
                proceeds_only: bool) -> tuple:
        """计算成交后的盈亏(毛盈亏,费用由调用方在现金里扣)。"""
        gross_pnl = (exit_price - entry_price) * quantity if side == "SELL" else 0.0
        return gross_pnl, 0.0

    def _force_close_all(self, *, positions, cash, t, bars_by_code,
                         closed_trades, fees) -> tuple:
        """区间结束按末日收盘强制平仓(避免未结头寸影响统计)。"""
        for pos in positions:
            bar = self._bar_on(bars_by_code, pos.stock_code, t)
            if bar is None:
                # 无末日行情则按成本价记 0 盈亏(无法平仓)
                continue
            exit_price = bar.close
            cash += pos.quantity * exit_price
            cash -= self._sell_cost(pos.quantity * exit_price, fees)
            per_share_risk = pos.entry_price - pos.stop_price
            r_mult = ((exit_price - pos.entry_price) / per_share_risk
                      if per_share_risk > 0 else 0.0)
            pnl = (exit_price - pos.entry_price) * pos.quantity
            closed_trades.append(_TradeRecord(
                stock_code=pos.stock_code, signal_date=pos.signal_date,
                entry_date=pos.entry_date, entry_price=pos.entry_price,
                exit_date=t.isoformat(), exit_price=exit_price,
                quantity=pos.quantity, pnl=pnl, r_multiple=r_mult,
                exit_reason="FORCE_CLOSE_AT_END",
                details={"regime_at_signal": "END_OF_PERIOD"},
            ))
        positions.clear()
        return cash, closed_trades

    def _mark_to_market(self, positions, bars_by_code, t, cash) -> float:
        """t 日总净值 = 现金 + 持仓市值(用 t 收盘)。"""
        mv = 0.0
        for pos in positions:
            bar = self._bar_on(bars_by_code, pos.stock_code, t)
            price = bar.close if bar else pos.entry_price
            mv += pos.quantity * price
        return cash + mv

    # ===================================================================
    # 数据切片(防未来函数)
    # ===================================================================

    def _load_all_bars(self, code, start_d, end_d):
        """加载基准在 [start, end] 区间的全部 K 线(升序)。"""
        rows = self.repo.get_daily_bars([code], start_d, end_d)
        bars = [
            DailyBar(
                code=r["stock_code"], trade_date=date.fromisoformat(r["trade_date"]),
                open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume=r["volume"], amount=r.get("amount"),
                pre_close=r.get("pre_close"), change_pct=r.get("change_pct"),
                adjust_factor=r.get("adjust_factor") or 1.0,
                source=r.get("source") or "",
            )
            for r in rows if r["stock_code"] == code
        ]
        bars.sort(key=lambda b: b.trade_date)
        return bars

    def _slice_up_to(self, bars_by_code, code, t):
        """取 code 的 trade_date <= t 的 K 线(防未来函数,§14.1)。"""
        all_bars = bars_by_code.get(code, [])
        return [b for b in all_bars if b.trade_date <= t]

    def _bar_on(self, bars_by_code, code, t):
        all_bars = bars_by_code.get(code, [])
        for b in reversed(all_bars):
            if b.trade_date == t:
                return b
            if b.trade_date < t:
                return None
        return None

    # ===================================================================
    # 市场状态 + 空回测指标
    # ===================================================================

    def _regime(self, benchmark_bars):
        # 复用 PlanService 的降级默认(breadth=None)
        # 需要至少 60 根才能算 MA60;不足时给一个友好默认。
        if len(benchmark_bars) < 20:
            from ..strategies.market_regime import classify_regime
            return classify_regime(0)  # NEUTRAL
        return compute_market_score(
            benchmark_bars=benchmark_bars, breadth_ratio=None,
            volatility_percentile=_DEFAULT_VOLATILITY_PERCENTILE,
        )

    def _empty_metrics(self, initial_equity, start_d, end_d) -> dict:
        return {
            "trade_count": 0, "win_rate": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
            "expectancy": 0.0, "profit_factor": 0.0,
            "cumulative_return": 0.0, "annualized_return": 0.0,
            "max_drawdown": 0.0, "max_consecutive_wins": 0,
            "max_consecutive_losses": 0, "initial_equity": initial_equity,
            "final_equity": initial_equity,
            "period_days": (end_d - start_d).days + 1,
            "filled_count": 0, "total_signals": 0, "execution_rate": 0.0,
        }


# ===================================================================
# 模块级纯函数:指标统计(§14.3)+ 辅助
# ===================================================================

def _compute_metrics(*, closed_trades, initial_equity, final_equity,
                     max_drawdown, start_d, end_d, filled, total_signals) -> dict:
    """统计 §14.3 核心指标。"""
    pnls = [t.pnl for t in closed_trades]
    rs = [t.r_multiple for t in closed_trades]
    wins_r = [r for r in rs if r > 0]
    losses_r = [r for r in rs if r <= 0]
    trade_count = len(closed_trades)
    win_rate = (len(wins_r) / trade_count) if trade_count else 0.0
    avg_win_r = (sum(wins_r) / len(wins_r)) if wins_r else 0.0
    avg_loss_r = (abs(sum(losses_r) / len(losses_r)) if losses_r else 0.0)
    loss_rate = 1.0 - win_rate
    expectancy = win_rate * avg_win_r - loss_rate * avg_loss_r

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    cumulative_return = ((final_equity - initial_equity) / initial_equity
                         if initial_equity > 0 else 0.0)
    period_days = (end_d - start_d).days + 1
    if period_days > 0 and final_equity > 0 and initial_equity > 0:
        years = period_days / 365.0
        ratio = final_equity / initial_equity
        annualized = (ratio ** (1 / years) - 1) if years > 0 else 0.0
    else:
        annualized = 0.0

    # 最大连续盈亏
    max_cw, max_cl = 0, 0
    cur_w, cur_l = 0, 0
    for t in closed_trades:
        if t.pnl > 0:
            cur_w += 1; cur_l = 0
            max_cw = max(max_cw, cur_w)
        else:
            cur_l += 1; cur_w = 0
            max_cl = max(max_cl, cur_l)

    exec_rate = (filled / total_signals) if total_signals else 0.0

    return {
        "trade_count": trade_count,
        "win_rate": round(win_rate, 6),
        "avg_win_r": round(avg_win_r, 6),
        "avg_loss_r": round(avg_loss_r, 6),
        "expectancy": round(expectancy, 6),
        "profit_factor": round(profit_factor, 6) if profit_factor != float("inf") else None,
        "cumulative_return": round(cumulative_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown": round(max_drawdown, 6),
        "max_consecutive_wins": max_cw,
        "max_consecutive_losses": max_cl,
        "initial_equity": round(initial_equity, 4),
        "final_equity": round(final_equity, 4),
        "period_days": period_days,
        "filled_count": filled,
        "total_signals": total_signals,
        "execution_rate": round(exec_rate, 6),
        # 集中度(供激活门禁 §14.4 使用)
        "concentration": _concentration(closed_trades),
    }


def _concentration(trades) -> dict:
    """§14.4 集中度:单一股票 / 单一月份贡献的利润占比。

    用净利润(正)占比衡量;占比 > 50% 视为集中。
    """
    total_profit = sum(t.pnl for t in trades if t.pnl > 0)
    if total_profit <= 0:
        return {"max_stock_share": 0.0, "max_month_share": 0.0,
                "concentrated_stock": False, "concentrated_month": False}
    # 按股票聚合正利润
    by_stock: dict[str, float] = {}
    by_month: dict[str, float] = {}
    for t in trades:
        if t.pnl <= 0:
            continue
        by_stock[t.stock_code] = by_stock.get(t.stock_code, 0.0) + t.pnl
        month = (t.exit_date or "")[:7]
        by_month[month] = by_month.get(month, 0.0) + t.pnl
    max_stock = max(by_stock.values()) if by_stock else 0.0
    max_month = max(by_month.values()) if by_month else 0.0
    return {
        "max_stock_share": round(max_stock / total_profit, 6),
        "max_month_share": round(max_month / total_profit, 6),
        "concentrated_stock": (max_stock / total_profit) > 0.5,
        "concentrated_month": (max_month / total_profit) > 0.5,
    }


def _coerce_date(d) -> date:
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def _ret_over(closes: list[float], period: int):
    if len(closes) < period + 1:
        return None
    base = closes[-(period + 1)]
    if base == 0:
        return None
    return (closes[-1] - base) / base


def _is_untradable(bar) -> bool:
    """§14.2:停牌(volume=0)或一字涨跌停(open==high==low)不可成交。"""
    if bar.volume is not None and bar.volume <= 0:
        return True
    # 一字板:开盘=最高=最低(容差 0.01%)
    if bar.high > 0 and abs(bar.open - bar.high) / bar.high < _LIMIT_LOCK_THRESHOLD \
            and abs(bar.high - bar.low) / bar.high < _LIMIT_LOCK_THRESHOLD:
        return True
    return False
