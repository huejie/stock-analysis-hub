"""行情数据服务 + 数据质量门禁。

职责(文档第 7、8.3、8.4 章):
- 通过 Provider 抓取行情并落库(增量)。
- 在生成计划前运行数据质量门禁。
- 数据异常时返回 BLOCKED,不"凑"结论。

Phase 1 不实现持仓检查(持仓表 Phase 2 才有)。
"""
from datetime import date, timedelta

from ..domain import DailyBar
from ..errors import (
    CalendarUnavailableError,
    EmptyProviderResultError,
    ProviderUnavailableError,
)
from ..providers.base import ProviderError, ProviderUnavailable


# 默认基准(文档 13.3):沪深300 + 中证500
_DEFAULT_BENCHMARKS = ["000300.SH", "000905.SH"]


class MarketDataService:
    def __init__(self, repo, provider, max_missing_ratio: float = 0.05):
        self.repo = repo
        self.provider = provider
        # 允许通过 settings 覆盖(Phase 1 直接用默认)
        self.max_missing_ratio = max_missing_ratio

    # ---- 行情更新 ----

    def _require_provider(self):
        if self.provider is None:
            raise ProviderUnavailableError("行情 Provider 尚未配置")
        return self.provider

    def update_bars_range(
        self, codes: list[str], start: date, end: date
    ) -> int:
        if not codes:
            return 0
        provider = self._require_provider()
        bars = provider.get_daily_bars(codes, start, end)
        if not bars:
            raise EmptyProviderResultError(
                "非空股票请求未返回任何行情",
                details={
                    "codes": codes,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
        return self.repo.upsert_daily_bars(bars)

    def update_bars(self, codes: list[str], target_date: date) -> int:
        """抓取指定股票在 target_date 的日线并落库。

        增量策略:回看 10 个自然日覆盖修订(文档 7.5)。
        """
        return self.update_bars_range(
            codes, target_date - timedelta(days=10), target_date
        )

    def update_pool_bars(self, pool_name: str, target_date: date) -> int:
        codes = self.repo.get_latest_pool_codes(pool_name)
        return self.update_bars(codes, target_date)

    def update_benchmark(self, target_date: date,
                         benchmark_codes: list[str] | None = None) -> int:
        codes = (
            _DEFAULT_BENCHMARKS
            if benchmark_codes is None
            else benchmark_codes
        )
        if not codes:
            return 0
        provider = self._require_provider()
        start = target_date - timedelta(days=10)
        bars = provider.get_index_bars(codes, start, target_date)
        if not bars:
            raise EmptyProviderResultError(
                "非空指数请求未返回任何行情",
                details={
                    "codes": codes,
                    "start": start.isoformat(),
                    "end": target_date.isoformat(),
                },
            )
        return self.repo.upsert_daily_bars(bars)

    def refresh_trade_calendar(self, start: date, end: date) -> int:
        provider = self._require_provider()
        try:
            get_with_source = getattr(
                provider, "get_trade_calendar_with_source", None
            )
            if callable(get_with_source):
                days, source = get_with_source(start, end)
            else:
                days = provider.get_trade_calendar(start, end)
                source = getattr(provider, "name", "composite")
        except (ProviderError, ProviderUnavailable) as exc:
            raise CalendarUnavailableError(
                "所有 Provider 均无法提供交易日历",
                details={"start": start.isoformat(), "end": end.isoformat()},
            ) from exc
        if not days:
            raise CalendarUnavailableError(
                "交易日历返回空结果",
                details={"start": start.isoformat(), "end": end.isoformat()},
            )
        expected_days = (end - start).days + 1
        expected_dates = {
            start + timedelta(days=offset) for offset in range(expected_days)
        }
        actual_dates = {day.date for day in days}
        if len(days) != expected_days or actual_dates != expected_dates:
            raise CalendarUnavailableError(
                "交易日历日期范围不完整",
                details={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "expected_days": expected_days,
                    "actual_days": len(days),
                },
            )
        return self.repo.upsert_trade_calendar(days, source=source)

    # ---- 数据质量门禁(文档 7.4) ----

    def check_data_health(self, *, trade_date: date,
                          pool_name: str = "default",
                          benchmark_codes: list[str] | None = None) -> dict:
        """运行数据质量门禁,返回健康报告。

        判定优先级:
        1. 任一基准缺失 -> BLOCKED
        2. 任一持仓缺失 -> BLOCKED(Phase 1 无持仓表,跳过)
        3. 池缺失比例 > 5% -> BLOCKED
        4. 池缺失比例 == 5%(不超过)且 > 0 -> PARTIAL
        5. 全部就绪 -> OK
        """
        benchmarks = (
            _DEFAULT_BENCHMARKS
            if benchmark_codes is None
            else list(benchmark_codes)
        )
        target_str = trade_date.isoformat()

        # 基准检查
        benchmark_updated: dict[str, bool] = {}
        issues: list[dict] = []
        for code in benchmarks:
            latest = self.repo.get_latest_bar_date(code)
            updated = latest == trade_date
            benchmark_updated[code] = updated
            if not updated:
                issues.append({
                    "severity": "BLOCKING",
                    "issue_code": "BENCHMARK_STALE",
                    "message": f"基准 {code} 未更新到 {target_str}(最新: {latest})",
                    "stock_code": code,
                    "details": {"expected": target_str, "actual": latest.isoformat() if latest else None},
                })

        # 池缺失检查
        pool_missing = self.repo.pool_missing_codes(pool_name, trade_date)
        pool_codes = self.repo.get_latest_pool_codes(pool_name)
        pool_total = len(pool_codes)
        pool_available = pool_total - len(pool_missing)
        missing_ratio = (len(pool_missing) / pool_total) if pool_total else 0.0

        for code in pool_missing:
            # > max_missing_ratio 为 BLOCKING,== 或 < 为 WARNING
            severity = "BLOCKING" if missing_ratio > self.max_missing_ratio else "WARNING"
            issues.append({
                "severity": severity,
                "issue_code": "POOL_DATA_MISSING",
                "message": f"候选 {code} 在 {target_str} 缺失行情",
                "stock_code": code,
                "details": {},
            })

        # 综合判定
        has_blocking = any(i["severity"] == "BLOCKING" for i in issues)
        has_warning = any(i["severity"] == "WARNING" for i in issues)

        if pool_total == 0:
            overall = "BLOCKED"
            issues.append({
                "severity": "BLOCKING", "issue_code": "POOL_EMPTY",
                "message": f"股票池 {pool_name} 为空", "details": {},
            })
        elif has_blocking:
            overall = "BLOCKED"
        elif has_warning:
            overall = "PARTIAL"
        else:
            overall = "OK"

        return {
            "trade_date": target_str,
            "overall_status": overall,
            "benchmark_codes": benchmarks,
            "benchmark_updated": benchmark_updated,
            "pool_total": pool_total,
            "pool_available": pool_available,
            "pool_missing": pool_missing,
            "pool_missing_ratio": round(missing_ratio, 4),
            "issues": issues,
            "generated_at": date.today().isoformat(),
        }
