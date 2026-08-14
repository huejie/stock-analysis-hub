"""计划生成服务(spec §9):编排 10 步生成流程 + 状态机 + 幂等键。

这是 Phase 3 的核心引擎。它把已有的纯函数策略模块(market_regime/scoring/
entry_rules/exit_rules)、指标计算(indicator_service)、仓位计算(position_sizing)
和组合风控(portfolio_service)串联成一个可审计、可重放的计划生成流程。

关键不变量(spec §9.1/§9.2/§9.3):
- **先处理持仓(spec §8.1)**:退出/减仓/持有优先于新开仓候选。
- **防未来函数**:所有指标只传入 `trade_date <= signal_date` 的 K 线。
- **幂等**:同一 (signal_date, account_snapshot, pool, strategy, data) 重复请求
  返回同一 plan_run;输入变化才创建新计划并把旧的标记 SUPERSEDED。
- **状态机**:CREATED→VALIDATING→GENERATING→READY/PARTIAL→PUBLISHED;
  数据门禁失败 → BLOCKED;引擎抛错 → FAILED。
- **发布**:只允许从 READY/PARTIAL → PUBLISHED,且不可重复发布。
"""
import hashlib
import json
import logging
from datetime import date, timedelta

from ..clock import target_trade_date_after
from ..domain import DailyBar
from ..errors import (
    PlanAlreadyPublishedError,
    PlanBlockedError,
    StrategyNotActiveError,
)
from ..position_sizing import compute_buy_quantity, effective_risk_per_trade
from ..strategies.entry_rules import evaluate_entry
from ..strategies.exit_rules import evaluate_exit
from ..strategies.market_regime import compute_market_score
from ..strategies.scoring import compute_score
from .indicator_service import compute_indicators, percentile_rank

logger = logging.getLogger(__name__)

# 默认基准(spec §8.4):沪深 300。中证 500 可配置,Phase 3 单基准足够。
DEFAULT_BENCHMARK = "000300.SH"

# Phase 3 简化默认值(production 应由 indicator_service 在全池上统计):
_DEFAULT_POOL_RETURN_PERCENTILE = 0.6   # 池内 20 日收益分位近似
_DEFAULT_EXCESS_20D = 0.01              # 20 日超额收益近似(正)
_DEFAULT_EXCESS_60D = 0.01              # 60 日超额收益近似(正)
_DEFAULT_VOLUME_RATIO = 1.5             # 量比近似(落在 1.0-2.5 区间)
_DEFAULT_AUXILIARY_BONUS = 0            # 辅助加分(热榜/龙虎榜/回测,Phase 3 默认 0)
_DEFAULT_VOLATILITY_PERCENTILE = 0.50   # 基准波动率分位(中等,不触发 M4)
_DEFAULT_MIN_SCORE = 70                 # spec §8.2 最低候选评分

# 状态(spec §9.2)
_STATUS_CREATED = "CREATED"
_STATUS_VALIDATING = "VALIDATING"
_STATUS_GENERATING = "GENERATING"
_STATUS_READY = "READY"
_STATUS_PARTIAL = "PARTIAL"
_STATUS_BLOCKED = "BLOCKED"
_STATUS_FAILED = "FAILED"
_STATUS_PUBLISHED = "PUBLISHED"
_STATUS_SUPERSEDED = "SUPERSEDED"

# 可复用的终态(幂等命中时不重新生成)
_REUSEABLE_STATUSES = (_STATUS_READY, _STATUS_PARTIAL, _STATUS_PUBLISHED)
# 允许重新生成的非终态(旧计划可被覆盖重建)
_REGENERATABLE_STATUSES = (_STATUS_BLOCKED, _STATUS_FAILED)


class PlanService:
    def __init__(self, repo, market_data_service, portfolio_service):
        self.repo = repo
        self.market_data_service = market_data_service  # check_data_health
        self.portfolio_service = portfolio_service       # compute_equity_snapshot / check_risk_limits

    # ===================================================================
    # 对外 API
    # ===================================================================

    def generate_plan(self, *, account_id: int, signal_date,
                      stock_pool_version_id: int, strategy_version_id: int,
                      force_new_version: bool = False) -> dict:
        """执行 10 步生成流程(spec §9.1)。

        signal_date 接受 ``date`` 或 ``str``(YYYY-MM-DD)。
        返回 ``{"id", "run_key", "status", "signal_date", "target_trade_date",
        "reused", "warnings"}``。
        """
        signal_date_d = _coerce_date(signal_date)
        signal_date_s = signal_date_d.isoformat()

        # ---- 步骤 1: 锁定输入 ----
        account = self.repo.get_account(account_id)
        if account is None:
            raise ValueError(f"账户 {account_id} 不存在")

        strategy = self.repo.get_strategy(strategy_version_id)
        if strategy is None:
            raise ValueError(f"策略版本 {strategy_version_id} 不存在")
        if strategy["status"] != "ACTIVE":
            # spec §11.5 STRATEGY_NOT_ACTIVE
            raise StrategyNotActiveError(
                f"策略版本 {strategy_version_id} 状态为 {strategy['status']},非 ACTIVE",
                details={"strategy_version_id": strategy_version_id,
                         "status": strategy["status"]},
            )

        pool = self.repo.get_stock_pool_version(stock_pool_version_id)
        if pool is None:
            raise ValueError(f"股票池版本 {stock_pool_version_id} 不存在")

        target_trade_date = target_trade_date_after(signal_date_d)

        # 解析策略参数(带默认)
        params = strategy.get("params_json") or {}
        min_score = int(params.get("min_score", _DEFAULT_MIN_SCORE))
        base_risk = float(params.get("risk_per_trade", account["risk_per_trade"]))
        max_risk = float(params.get("max_risk", base_risk))

        # 账户快照(幂等键 + 审计)——计算时若持仓缺行情会抛错,
        # 这里先用账户字段构造快照,组合净值在 step 4 用 portfolio_service 精算。
        account_snapshot = self._account_snapshot(account)
        account_snapshot_hash = _stable_hash(account_snapshot)

        # 基准行情(用于市场状态 + 数据快照哈希)。取到 signal_date 为止。
        benchmark_bars = self._load_bars_up_to(
            DEFAULT_BENCHMARK, signal_date_d, window=70,
        )
        data_snapshot_hash = self._data_snapshot_hash(
            benchmark_bars, pool.get("items", []), signal_date_d,
        )

        # ---- 幂等键(spec §9.3)----
        run_key = self._compute_run_key(
            signal_date=signal_date_s,
            account_snapshot_hash=account_snapshot_hash,
            pool_version_hash=pool["items_hash"],
            strategy_version_hash=strategy["params_hash"],
            data_snapshot_hash=data_snapshot_hash,
        )

        # ---- 幂等检查(spec §9.3)----
        if not force_new_version:
            existing = self.repo.get_plan_run_by_key(run_key)
            if existing and existing["status"] in _REUSEABLE_STATUSES:
                return {
                    "id": existing["id"], "run_key": run_key,
                    "status": existing["status"],
                    "signal_date": existing["signal_date"],
                    "target_trade_date": existing["target_trade_date"],
                    "reused": True, "warnings": existing.get("warnings", []),
                }
            # BLOCKED/FAILED 允许重新生成:旧记录在下面 supersede。

        # force_new_version 且 run_key 已存在时,派生一个新 run_key,
        # 以便插入全新行(而非复用旧行)。spec §9.3:不得原地覆盖。
        if force_new_version and self.repo.get_plan_run_by_key(run_key) is not None:
            run_key = self._derive_force_run_key(run_key)

        # 旧计划(同 signal_date + account)在创建新 run 后被 SUPERSEDE。
        prior_runs = self.repo.list_plan_runs(signal_date=signal_date_s)
        prior_for_account = [
            r for r in prior_runs
            if r["account_id"] == account_id
            and r["id"] is not None
            and r["run_key"] != run_key
            and r["status"] not in (_STATUS_SUPERSEDED, _STATUS_PUBLISHED)
        ]

        # ---- 创建 CREATED 记录 ----
        created = self.repo.create_plan_run(
            run_key=run_key, account_id=account_id, signal_date=signal_date_s,
            target_trade_date=target_trade_date.isoformat(),
            stock_pool_version_id=stock_pool_version_id,
            strategy_version_id=strategy_version_id,
            status=_STATUS_CREATED,
            account_snapshot_json=account_snapshot,
            data_snapshot_hash=data_snapshot_hash,
            warnings=[],
        )
        run_id = created["id"]
        warnings: list[str] = []

        # ---- 步骤 2: VALIDATING —— 数据质量门禁 ----
        self.repo.update_plan_run_status(run_id, _STATUS_VALIDATING)
        pool_name = pool["pool_name"]
        health = self.market_data_service.check_data_health(
            trade_date=signal_date_d, pool_name=pool_name,
            benchmark_codes=[DEFAULT_BENCHMARK],
        )
        # 持仓行情缺失检查(spec 场景 B):任一持仓缺 signal_date 行情 → BLOCKED。
        position_missing = self._positions_missing_on(account_id, signal_date_d)
        if position_missing:
            self.repo.update_plan_run_status(
                run_id, _STATUS_BLOCKED,
                error={"code": "POSITION_DATA_MISSING",
                       "missing": position_missing,
                       "message": "持仓在信号日缺少行情,无法生成计划"},
            )
            self._record_position_missing_issues(run_id, signal_date_s, position_missing)
            self._supersede_priors(run_id, prior_for_account)
            raise PlanBlockedError(
                f"持仓行情缺失: {', '.join(position_missing)} (signal_date={signal_date_s})",
                details={"missing": position_missing,
                         "run_id": run_id, "run_key": run_key},
            )
        if health["overall_status"] == "BLOCKED":
            self.repo.update_plan_run_status(
                run_id, _STATUS_BLOCKED,
                error={"code": "DATA_GATE_BLOCKED",
                       "health": _slim_health(health)},
            )
            self._record_health_issues(run_id, signal_date_s, health)
            self._supersede_priors(run_id, prior_for_account)
            raise PlanBlockedError(
                f"数据门禁阻断: {health['overall_status']}",
                details={"health": _slim_health(health),
                         "run_id": run_id, "run_key": run_key},
            )
        if health["overall_status"] == "PARTIAL":
            warnings.append(f"数据部分缺失(PARTIAL): 池缺失比例 "
                            f"{health['pool_missing_ratio']:.2%}")

        # ---- 步骤 3-9: GENERATING ----
        self.repo.update_plan_run_status(run_id, _STATUS_GENERATING)
        try:
            status, items = self._generate_items(
                run_id=run_id, account=account, signal_date_d=signal_date_d,
                pool=pool, benchmark_bars=benchmark_bars,
                min_score=min_score, base_risk=base_risk, max_risk=max_risk,
                warnings=warnings,
            )
        except Exception as exc:  # 引擎异常 → FAILED
            logger.exception("PlanService 生成失败 run_id=%s", run_id)
            self.repo.update_plan_run_status(
                run_id, _STATUS_FAILED,
                error={"code": "ENGINE_ERROR",
                       "message": str(exc), "type": type(exc).__name__},
            )
            self._supersede_priors(run_id, prior_for_account)
            raise

        # ---- 步骤 10: 持久化 items + 终态 ----
        for rank, item in enumerate(items, start=1):
            self.repo.create_plan_item(plan_run_id=run_id, **item)
        self.repo.update_plan_run_status(
            run_id, status,
            market_regime=self._last_regime,
            market_score=self._last_score,
            recommended_exposure=self._last_exposure,
        )

        # 旧计划 SUPERSEDE(仅在本次成功生成时)
        self._supersede_priors(run_id, prior_for_account)

        return {
            "id": run_id, "run_key": run_key, "status": status,
            "signal_date": signal_date_s,
            "target_trade_date": target_trade_date.isoformat(),
            "reused": False, "warnings": warnings,
        }

    def get_plan_detail(self, run_id: int) -> dict:
        """返回计划 + 全部明细(spec §11.4 形状)。"""
        run = self.repo.get_plan_run(run_id)
        if run is None:
            return None
        items = self.repo.get_plan_items(run_id)
        return {
            "id": run["id"], "status": run["status"],
            "signal_date": run["signal_date"],
            "target_trade_date": run["target_trade_date"],
            "market_regime": run.get("market_regime"),
            "market_score": run.get("market_score"),
            "degraded": run.get("account_snapshot", {}).get("degraded", False),
            "recommended_exposure": run.get("recommended_exposure"),
            "warnings": run.get("warnings", []),
            "items": items,
            "created_at": run.get("created_at"),
            "published_at": run.get("published_at"),
        }

    def publish_plan(self, run_id: int) -> dict:
        """READY/PARTIAL → PUBLISHED(spec §9.2)。已发布或非就绪报错。"""
        run = self.repo.get_plan_run(run_id)
        if run is None:
            raise ValueError(f"计划 {run_id} 不存在")
        if run["status"] == _STATUS_PUBLISHED:
            raise PlanAlreadyPublishedError(
                f"计划 {run_id} 已发布,不可重复发布",
                details={"run_id": run_id},
            )
        if run["status"] not in (_STATUS_READY, _STATUS_PARTIAL):
            raise PlanBlockedError(
                f"计划 {run_id} 状态为 {run['status']},不可发布(仅 READY/PARTIAL 可发布)",
                details={"run_id": run_id, "status": run["status"]},
            )
        self.repo.publish_plan_run(run_id)
        updated = self.repo.get_plan_run(run_id)
        return {
            "id": run_id, "status": _STATUS_PUBLISHED,
            "published_at": updated["published_at"],
        }

    # ===================================================================
    # 幂等键(spec §9.3)
    # ===================================================================

    def _compute_run_key(self, *, signal_date: str,
                         account_snapshot_hash: str,
                         pool_version_hash: str,
                         strategy_version_hash: str,
                         data_snapshot_hash: str) -> str:
        """SHA256(signal_date + account + pool + strategy + data)[:16]。"""
        raw = "|".join([
            signal_date,
            account_snapshot_hash,
            pool_version_hash,
            strategy_version_hash,
            data_snapshot_hash,
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _derive_force_run_key(self, base_run_key: str) -> str:
        """force_new_version 派生不冲突的 run_key(追加版本计数哈希)。

        run_key 列 UNIQUE,强制新建时必须用新 key 才能插入新行。
        派生方式:base + 当前时间戳微秒哈希前 4 位,保证唯一且可追溯。
        """
        import time
        suffix = hashlib.sha256(
            f"{base_run_key}|{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:4]
        return f"{base_run_key}-f{suffix}"

    # ===================================================================
    # 生成步骤 3-9(被 generate_plan 在 GENERATING 阶段调用)
    # ===================================================================

    def _generate_items(self, *, run_id, account, signal_date_d, pool,
                        benchmark_bars, min_score, base_risk, max_risk,
                        warnings) -> tuple[str, list[dict]]:
        """步骤 3-9。返回 (最终状态, items)。

        items 按 [持仓项..., 候选项...] 顺序(spec §8.1 先处理持仓)。
        最终状态:READY 或 PARTIAL(有非阻断 warning 时)。
        """
        items: list[dict] = []

        # ---- 步骤 3: 市场状态(spec §8.4)----
        # Phase 3 简化:breadth_ratio=None(降级,全市场宽度需 Phase 4+ 接入)。
        regime_result = compute_market_score(
            benchmark_bars=benchmark_bars,
            breadth_ratio=None,
            volatility_percentile=_DEFAULT_VOLATILITY_PERCENTILE,
        )
        # 缓存供 step 10 写入 plan_run
        self._last_regime = regime_result.regime
        self._last_score = regime_result.score
        self._last_exposure = regime_result.recommended_exposure
        if regime_result.degraded:
            warnings.append("市场宽度数据不可用,降级模式(degraded=True,新开仓风险减半)")

        # 组合净值快照(此时持仓行情已校验存在,不会抛错)
        try:
            snap = self.portfolio_service.compute_equity_snapshot(
                account["id"], signal_date_d,
            )
        except ValueError as exc:
            # 持仓缺行情理论上已在 step 2 拦截;防御性兜底
            raise
        total_equity = snap["total_equity"]
        cash = snap["cash"]

        # 账户回撤暂停(spec §8.2:回撤 >= max_drawdown_limit → 暂停新开仓,
        # 场景 D:候选变 WATCH/FORBIDDEN,但持仓仍生成退出/持有建议)。
        drawdown_paused = snap["drawdown"] >= account["max_drawdown_limit"]
        if drawdown_paused:
            warnings.append(
                f"账户回撤 {snap['drawdown']:.2%} 达到暂停阈值 "
                f"{account['max_drawdown_limit']:.2%},暂停新开仓(需人工复盘)"
            )

        # ---- 步骤 4: 持仓优先(spec §8.1)----
        # 回撤暂停不影响持仓管理(退出/减仓/持有仍正常评估)。
        positions = [p for p in self.repo.get_positions(account["id"])
                     if p["quantity"] > 0]
        for pos in positions:
            item = self._eval_position(
                pos, signal_date_d, benchmark_bars, regime_result,
            )
            if item is not None:
                items.append(item)

        # ---- 步骤 5-8: 候选评分 → 入场 → 仓位 → 风控 ----
        candidates = self._eval_candidates(
            pool=pool, signal_date_d=signal_date_d,
            benchmark_bars=benchmark_bars, regime_result=regime_result,
            min_score=min_score, account=account, snap=snap,
            total_equity=total_equity, cash=cash,
            base_risk=base_risk, max_risk=max_risk,
            drawdown_paused=drawdown_paused,
        )
        items.extend(candidates)

        # ---- 状态判定 ----
        # 有任何非阻断 warning → PARTIAL;否则 READY。
        status = _STATUS_PARTIAL if warnings else _STATUS_READY
        return status, items

    # ---------- 持仓评估(步骤 4)----------

    def _eval_position(self, pos, signal_date_d, benchmark_bars,
                       regime_result) -> dict | None:
        """对单只持仓评估退出/减仓/持有(spec §8.8)。返回 plan_item dict 或 None。"""
        code = pos["stock_code"]
        bars = self._load_bars_up_to(code, signal_date_d, window=70)
        if len(bars) < 2:
            # 数据不足以算指标 → 视为 HOLD(避免误杀),记录在 rule_misses
            return {
                "stock_code": code, "stock_name": pos.get("stock_name"),
                "action": "HOLD",
                "rule_hits_json": [],
                "rule_misses_json": ["INSUFFICIENT_BARS"],
                "invalidation_reason": "持仓 K 线不足以计算指标,维持持有",
            }
        ind = compute_indicators(bars)
        closes = [b.close for b in bars]
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
        prev_prev_close = closes[-3] if len(closes) >= 3 else prev_close
        highest_close = max(closes)
        atr = ind.atr if ind.atr is not None else 0.0

        exit_result = evaluate_exit(
            close=ind.close,
            initial_stop=pos.get("initial_stop"),
            trailing_stop=pos.get("trailing_stop"),
            ma20=ind.ma20, ma60=ind.ma60,
            prev_close=prev_close, prev_prev_close=prev_prev_close,
            entry_price=pos["average_cost"], atr=atr,
            highest_close=highest_close,
        )
        # 移动止损回写(spec §8.8:只上移不下移,compute_trailing_stop 已保证单调)
        if exit_result.new_trailing_stop is not None:
            new_ts = exit_result.new_trailing_stop
            old_ts = pos.get("trailing_stop")
            if new_ts != old_ts:
                self.repo.upsert_position(
                    account_id=pos["account_id"], stock_code=code,
                    quantity=pos["quantity"], available_quantity=pos["available_quantity"],
                    average_cost=pos["average_cost"], stock_name=pos.get("stock_name"),
                    initial_stop=pos.get("initial_stop"),
                    trailing_stop=new_ts,
                    opened_at=pos.get("opened_at"),
                )
        action = exit_result.action  # EXIT / REDUCE / HOLD
        return {
            "stock_code": code, "stock_name": pos.get("stock_name"),
            "action": action,
            "rule_hits_json": [exit_result.reason],
            "rule_misses_json": [],
            "invalidation_reason": exit_result.detail,
        }

    # ---------- 候选评估(步骤 5-8)----------

    def _eval_candidates(self, *, pool, signal_date_d, benchmark_bars,
                         regime_result, min_score, account, snap,
                         total_equity, cash, base_risk, max_risk,
                         drawdown_paused: bool = False) -> list[dict]:
        """对池内每只股票评分 → 入场 → 仓位 → 风控(spec §8.5/§8.6/§8.7)。

        两遍扫描:第一遍算各股票 20 日收益/量比/跳空;第二遍按池内分位评分。
        回撤暂停(drawdown_paused)时所有候选降级为 FORBIDDEN(spec 场景 D)。
        """
        benchmark_closes = [b.close for b in benchmark_bars]
        bm_ret_20d = _ret_over(benchmark_closes, 20)
        bm_ret_60d = _ret_over(benchmark_closes, 60)

        # 热榜在榜情况(只读 stock_records,spec §8.5 辅助信号)
        pool_codes = [it["stock_code"] for it in pool.get("items", [])]
        try:
            hotlist_info = self.repo.get_hotlist_presence(pool_codes, days=5)
        except Exception:
            hotlist_info = {}  # 热榜表缺失(独立 trading DB)时跳过

        # ---- 第一遍:加载 bars,计算原始指标(收益/量比/跳空) ----
        raw: list[dict] = []
        all_returns_20d: list[float] = []  # 用于池内分位排名
        for it in pool.get("items", []):
            code = it["stock_code"]
            bars = self._load_bars_up_to(code, signal_date_d, window=70)
            if len(bars) < 20:
                raw.append({
                    "code": code, "name": it.get("stock_name"),
                    "ind": None, "breakdown": None, "score": -1,
                    "_misses": ["INSUFFICIENT_BARS"],
                })
                continue
            ind = compute_indicators(bars)
            closes = [b.close for b in bars]
            stk_ret_20d = _ret_over(closes, 20)
            stk_ret_60d = _ret_over(closes, 60)
            # 量比 = 当日成交量 / 近 5 日平均成交量(spec §8.5)
            vols = [b.volume for b in bars]
            vol_ratio = _volume_ratio(vols)
            # 跳空检测:近 20 日是否有单日跳空 > 5%(spec §8.5 波动风险)
            gap = _has_gap(bars[-20:])
            raw.append({
                "code": code, "name": it.get("stock_name"),
                "ind": ind, "stk_ret_20d": stk_ret_20d, "stk_ret_60d": stk_ret_60d,
                "vol_ratio": vol_ratio, "has_gap": gap, "score": None,
                "_misses": [],
            })
            if stk_ret_20d is not None:
                all_returns_20d.append(stk_ret_20d)

        # ---- 第二遍:按池内分位排名 + 评分 ----
        scored: list[dict] = []
        for item in raw:
            if item["ind"] is None:
                scored.append(item)  # INSUFFICIENT_BARS,直接透传
                continue
            stk_ret_20d = item["stk_ret_20d"]
            stk_ret_60d = item["stk_ret_60d"]
            # 池内 20 日收益分位(0-1)
            if stk_ret_20d is not None and all_returns_20d:
                pool_pct = sum(1 for r in all_returns_20d if r < stk_ret_20d) / len(all_returns_20d)
            else:
                pool_pct = _DEFAULT_POOL_RETURN_PERCENTILE
            # 超额收益
            excess_20d = (stk_ret_20d - bm_ret_20d) if stk_ret_20d is not None and bm_ret_20d is not None else 0.0
            excess_60d = (stk_ret_60d - bm_ret_60d) if stk_ret_60d is not None and bm_ret_60d is not None else 0.0
            # 热榜辅助加分(spec §8.5:当日/近期热榜趋势增强,合计上限 10)
            aux = _hotlist_bonus(hotlist_info.get(item["code"].split(".")[0]))
            breakdown = compute_score(
                item["ind"],
                pool_return_percentile=pool_pct,
                excess_20d=excess_20d,
                excess_60d=excess_60d,
                volume_ratio=item["vol_ratio"],
                has_gap=item["has_gap"],
                auxiliary_bonus=aux,
            )
            item["breakdown"] = breakdown
            item["score"] = breakdown.total
            scored.append(item)

        # 按评分降序(spec §9.1 step 6)
        scored.sort(key=lambda x: x["score"], reverse=True)

        # 当前持仓数(用于额度判断)
        existing_positions = self.repo.get_positions(account["id"])
        held_codes = {p["stock_code"] for p in existing_positions if p["quantity"] > 0}
        current_count = len(held_codes)

        effective_risk = effective_risk_per_trade(
            base_risk=base_risk, consecutive_losses=0,
            regime=regime_result.regime, degraded=regime_result.degraded,
            max_risk=max_risk,
        )

        items: list[dict] = []
        cumulative_buy_value = 0.0
        rank = 0
        for cand in scored:
            code, name = cand["code"], cand["name"]
            ind, breakdown, score = cand["ind"], cand["breakdown"], cand["score"]

            # 数据不足项 → FORBIDDEN
            if ind is None or breakdown is None:
                items.append({
                    "stock_code": code, "stock_name": name,
                    "action": "FORBIDDEN", "score": None,
                    "rule_hits_json": [],
                    "rule_misses_json": cand["_misses"],
                    "invalidation_reason": "K 线数据不足,无法评估",
                })
                continue

            # 账户回撤暂停(spec §8.2 / 场景 D):新开仓一律 FORBIDDEN,
            # 评分仍记录以便审计。持仓退出/持有不受影响(在步骤 4 处理)。
            if drawdown_paused:
                items.append({
                    "stock_code": code, "stock_name": name,
                    "action": "FORBIDDEN", "score": float(score),
                    "rule_hits_json": list(breakdown.rule_hits),
                    "rule_misses_json": ["DRAWDOWN_PAUSE"],
                    "invalidation_reason": (
                        f"账户回撤达到暂停阈值,暂停新开仓(评分 {score})"
                    ),
                })
                continue

            # 组合额度判断(spec §8.6 条件 5):
            # 持仓数 + 已计划新增 + 当前候选 是否超 max_positions。
            planned_new = sum(
                1 for i in items if i["action"] == "CONDITIONAL_BUY"
                and i["stock_code"] not in held_codes
            )
            is_new_position = code not in held_codes
            portfolio_has_capacity = (
                current_count + planned_new + (1 if is_new_position else 0)
                <= account["max_positions"]
            )

            entry_result = evaluate_entry(
                regime=regime_result.regime, score=breakdown,
                ind=ind, entry_price=ind.close,
                atr=ind.atr if ind.atr is not None else 0.0,
                low_10d=ind.low_10d,
                portfolio_has_capacity=portfolio_has_capacity,
                min_score=min_score,
            )

            if entry_result.action == "CONDITIONAL_BUY":
                rank += 1
                # 步骤 7: 仓位计算(spec §8.7)
                buy = compute_buy_quantity(
                    total_equity=total_equity, cash=cash,
                    entry_price=ind.close,
                    stop_price=entry_result.stop_price,
                    effective_risk_per_trade=effective_risk,
                    max_single_position=account["max_single_position"],
                )
                buy_value = buy.buy_quantity * ind.close
                # 步骤 8a: 账户级风控(spec §8.2/§8.6 条件 5)——
                # check_risk_limits 检查总仓位/单股/行业/数量硬上限。
                risk_check = self.portfolio_service.check_risk_limits(
                    account["id"], signal_date_d,
                    proposed_buy_value=cumulative_buy_value + buy_value,
                )
                # drawdown 已在 _generate_items 单独处理(降级为 FORBIDDEN),
                # 这里只关心账户硬限制违规码。
                hard_violations = [
                    v for v in risk_check["violations"]
                    if v["code"] != "DRAWDOWN_PAUSE"
                ]
                # 步骤 8b: 组合二次校验 —— 总仓位 <= regime cap(条件上限)
                post_mv = snap["market_value"] + cumulative_buy_value + buy_value
                post_exposure = post_mv / total_equity if total_equity > 0 else 0
                if buy.buy_quantity < 100:
                    # 数量过小不可交易 → WATCH
                    items.append({
                        "stock_code": code, "stock_name": name,
                        "action": "WATCH", "score": float(score), "rank_no": rank,
                        "stop_price": entry_result.stop_price,
                        "trigger_price": (entry_result.trigger_prices.trigger_price
                                          if entry_result.trigger_prices else None),
                        "do_not_chase_price": (entry_result.trigger_prices.do_not_chase_price
                                               if entry_result.trigger_prices else None),
                        "target_2r_price": entry_result.target_2r_price,
                        "rule_hits_json": entry_result.rule_hits,
                        "rule_misses_json": entry_result.rule_misses + ["QUANTITY_BELOW_100"],
                        "invalidation_reason": "计算买入数量 < 100 股,不可交易",
                    })
                    continue
                if hard_violations:
                    # 违反账户硬限制 → FORBIDDEN
                    codes_v = [v["code"] for v in hard_violations]
                    items.append({
                        "stock_code": code, "stock_name": name,
                        "action": "FORBIDDEN", "score": float(score), "rank_no": rank,
                        "stop_price": entry_result.stop_price,
                        "trigger_price": (entry_result.trigger_prices.trigger_price
                                          if entry_result.trigger_prices else None),
                        "do_not_chase_price": (entry_result.trigger_prices.do_not_chase_price
                                               if entry_result.trigger_prices else None),
                        "target_2r_price": entry_result.target_2r_price,
                        "rule_hits_json": entry_result.rule_hits,
                        "rule_misses_json": entry_result.rule_misses + codes_v,
                        "invalidation_reason": (
                            "账户风控限制: " + "; ".join(v["message"] for v in hard_violations)
                        ),
                    })
                    continue
                if post_exposure > regime_result.recommended_exposure:
                    # 超过 regime 仓位上限 → 标记 WATCH(保留候选但不建议买入)
                    items.append({
                        "stock_code": code, "stock_name": name,
                        "action": "WATCH", "score": float(score), "rank_no": rank,
                        "stop_price": entry_result.stop_price,
                        "trigger_price": (entry_result.trigger_prices.trigger_price
                                          if entry_result.trigger_prices else None),
                        "do_not_chase_price": (entry_result.trigger_prices.do_not_chase_price
                                               if entry_result.trigger_prices else None),
                        "target_2r_price": entry_result.target_2r_price,
                        "suggested_quantity": 0,
                        "suggested_position_pct": 0.0,
                        "rule_hits_json": entry_result.rule_hits,
                        "rule_misses_json": entry_result.rule_misses
                        + ["REGIME_EXPOSURE_CAP"],
                        "invalidation_reason": (
                            f"买入后总仓位 {post_exposure:.2%} 超过 {regime_result.regime} "
                            f"上限 {regime_result.recommended_exposure:.2%}"
                        ),
                    })
                    continue
                cumulative_buy_value += buy_value
                suggested_pct = buy_value / total_equity if total_equity > 0 else 0
                risk_pct = buy.risk_amount / total_equity if total_equity > 0 else 0
                items.append({
                    "stock_code": code, "stock_name": name,
                    "action": "CONDITIONAL_BUY",
                    "score": float(score), "rank_no": rank,
                    "trigger_price": entry_result.trigger_prices.trigger_price,
                    "do_not_chase_price": entry_result.trigger_prices.do_not_chase_price,
                    "stop_price": entry_result.stop_price,
                    "target_2r_price": entry_result.target_2r_price,
                    "suggested_quantity": buy.buy_quantity,
                    "suggested_position_pct": round(suggested_pct, 6),
                    "risk_amount": round(buy.risk_amount, 2),
                    "risk_pct": round(risk_pct, 6),
                    "rule_hits_json": entry_result.rule_hits,
                    "rule_misses_json": entry_result.rule_misses,
                    "invalidation_reason": entry_result.invalidation_reason,
                })
            else:
                # WATCH / FORBIDDEN
                items.append({
                    "stock_code": code, "stock_name": name,
                    "action": entry_result.action,
                    "score": float(score),
                    "trigger_price": (entry_result.trigger_prices.trigger_price
                                      if entry_result.trigger_prices else None),
                    "do_not_chase_price": (entry_result.trigger_prices.do_not_chase_price
                                           if entry_result.trigger_prices else None),
                    "stop_price": entry_result.stop_price,
                    "rule_hits_json": entry_result.rule_hits,
                    "rule_misses_json": entry_result.rule_misses,
                    "invalidation_reason": entry_result.invalidation_reason,
                })
        return items

    # ===================================================================
    # 辅助:数据加载 / 哈希 / 快照
    # ===================================================================

    def _load_bars_up_to(self, code: str, signal_date: date,
                         window: int = 70) -> list[DailyBar]:
        """加载某股票到 signal_date 为止的最近 window 个交易日 K 线(防未来函数)。

        返回升序 DailyBar 列表。用宽窗口(约 window*2 自然日)回看以覆盖周末。
        """
        start = signal_date - timedelta(days=window * 2 + 10)
        rows = self.repo.get_daily_bars([code], start, signal_date)
        bars = [
            DailyBar(
                code=r["stock_code"],
                trade_date=date.fromisoformat(r["trade_date"]),
                open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume=r["volume"], amount=r.get("amount"),
                pre_close=r.get("pre_close"), change_pct=r.get("change_pct"),
                adjust_factor=r.get("adjust_factor") or 1.0,
                source=r.get("source") or "",
            )
            for r in rows
            if r["stock_code"] == code
        ]
        bars.sort(key=lambda b: b.trade_date)
        # 只取最近 window 根(避免历史数据膨胀拖慢指标)
        if len(bars) > window:
            bars = bars[-window:]
        return bars

    def _positions_missing_on(self, account_id: int, signal_date: date) -> list[str]:
        """返回在 signal_date 缺行情的持仓代码(场景 B)。"""
        positions = [p for p in self.repo.get_positions(account_id) if p["quantity"] > 0]
        if not positions:
            return []
        codes = [p["stock_code"] for p in positions]
        rows = self.repo.get_daily_bars(codes, signal_date, signal_date)
        have = {r["stock_code"] for r in rows
                if r["trade_date"] == signal_date.isoformat()}
        return [c for c in codes if c not in have]

    def _account_snapshot(self, account: dict) -> dict:
        """账户关键字段快照(用于幂等键 + 审计)。"""
        return {
            "account_id": account["id"],
            "cash_balance": round(account["cash_balance"], 4),
            "risk_per_trade": account["risk_per_trade"],
            "max_single_position": account["max_single_position"],
            "max_total_exposure": account["max_total_exposure"],
            "max_sector_exposure": account["max_sector_exposure"],
            "max_positions": account["max_positions"],
            "max_drawdown_limit": account["max_drawdown_limit"],
        }

    def _data_snapshot_hash(self, benchmark_bars: list[DailyBar],
                            pool_items: list[dict],
                            signal_date: date) -> str:
        """基准 + 池代码列表在 signal_date 的数据指纹(spec §9.3 canonical_data_hash)。

        用 benchmark bar 的 checksum(已存于 DB)+ 池代码稳定列表,保证:
        数据未更新 → 同 hash → 复用;数据更新 → hash 变 → 新计划。
        """
        bm_tuples = [(b.code, b.trade_date.isoformat(), round(b.close, 4))
                     for b in benchmark_bars]
        pool_codes = sorted(it["stock_code"].upper() for it in pool_items)
        payload = {
            "signal_date": signal_date.isoformat(),
            "benchmark": sorted(bm_tuples),
            "pool_codes": pool_codes,
        }
        return _stable_hash(payload)

    # ===================================================================
    # 内部:issues / supersede
    # ===================================================================

    def _record_position_missing_issues(self, run_id, signal_date_s, missing):
        for code in missing:
            self.repo.create_data_issue(
                severity="BLOCKING", issue_code="POSITION_DATA_MISSING",
                message=f"持仓 {code} 在 {signal_date_s} 缺少行情",
                stock_code=code, trade_date=signal_date_s,
                source="plan_service", run_id=run_id,
            )

    def _record_health_issues(self, run_id, signal_date_s, health):
        for issue in health.get("issues", []):
            self.repo.create_data_issue(
                severity=issue["severity"], issue_code=issue["issue_code"],
                message=issue["message"],
                stock_code=issue.get("stock_code"), trade_date=signal_date_s,
                source="market_data_service", details=issue.get("details"),
                run_id=run_id,
            )

    def _supersede_priors(self, new_run_id: int, prior_runs: list[dict]) -> None:
        for r in prior_runs:
            self.repo.supersede_plan_run(r["id"], new_run_id)


# ===================================================================
# 模块级辅助函数(纯函数,便于单测)
# ===================================================================

def _coerce_date(d) -> date:
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def _stable_hash(payload) -> str:
    """对 dict/list 计算稳定 SHA256[:16](键排序,ensure_ascii=False)。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _ret_over(closes: list[float], period: int) -> float | None:
    """最近 period 根的收益率:(last - last_n_ago) / last_n_ago。"""
    if len(closes) < period + 1:
        return None
    base = closes[-(period + 1)]
    if base == 0:
        return None
    return (closes[-1] - base) / base


def _volume_ratio(vols: list[float]) -> float:
    """量比 = 当日成交量 / 近 5 日平均成交量(spec §8.5)。

    数据不足时返回 1.0(中性)。
    """
    if len(vols) < 6:
        return 1.0
    avg_5d = sum(vols[-6:-1]) / 5
    if avg_5d == 0:
        return 1.0
    return vols[-1] / avg_5d


def _has_gap(bars: list) -> bool:
    """近 N 根 K 线是否有异常跳空(单日开盘跳空 > 5%,spec §8.5 波动风险)。"""
    if len(bars) < 2:
        return False
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        cur_open = bars[i].open
        if prev_close > 0 and abs(cur_open - prev_close) / prev_close > 0.05:
            return True
    return False


def _hotlist_bonus(info: dict | None) -> int:
    """热榜辅助加分(spec §8.5:当日/近期热榜趋势增强,合计上限 10)。

    规则:在榜天数越多、排名越靠前且上升,加分越高。
    - 在榜 1 天 +2;连续在榜每天 +1(封顶 5)
    - 排名 Top5 +2,Top3 +3
    - 排名上升(比首次上榜更靠前)+2
    """
    if not info:
        return 0
    bonus = 0
    days = info.get("on_list_days", 0)
    rank = info.get("latest_rank", 99)
    improved = info.get("rank_improved", False)
    if days >= 1:
        bonus += 2                      # 在榜基础分
        bonus += min(3, days - 1)       # 连续在榜,每天+1 封顶3
    if rank <= 5:
        bonus += 2 if rank > 3 else 3   # Top5 +2,Top3 +3
    if improved:
        bonus += 2                      # 排名上升
    return min(10, bonus)


def _slim_health(health: dict) -> dict:
    """精简 health 报告用于 error.details(避免 issues 列表膨胀)。"""
    return {
        "overall_status": health["overall_status"],
        "pool_total": health.get("pool_total"),
        "pool_missing": health.get("pool_missing"),
        "pool_missing_ratio": health.get("pool_missing_ratio"),
        "benchmark_updated": health.get("benchmark_updated"),
        "issues_count": len(health.get("issues", [])),
    }
