"""行情数据服务 + 数据质量门禁。

职责(文档第 7、8.3、8.4 章):
- 通过 Provider 抓取行情并落库(增量)。
- 在生成计划前运行数据质量门禁。
- 数据异常时返回 BLOCKED,不"凑"结论。

Phase 1 不实现持仓检查(持仓表 Phase 2 才有)。
"""
from datetime import date, timedelta

from ..domain import DailyBar


# 默认基准(文档 13.3):沪深300 + 中证500
_DEFAULT_BENCHMARKS = ["000300.SH", "000905.SH"]


class MarketDataService:
    def __init__(self, repo, provider, max_missing_ratio: float = 0.05):
        self.repo = repo
        self.provider = provider
        # 允许通过 settings 覆盖(Phase 1 直接用默认)
        self.max_missing_ratio = max_missing_ratio

    # ---- 行情更新 ----

    def update_bars(self, codes: list[str], target_date: date) -> int:
        """抓取指定股票在 target_date 的日线并落库。

        增量策略:回看 10 个自然日覆盖修订(文档 7.5)。
        """
        if not codes or self.provider is None:
            return 0
        start = target_date - timedelta(days=10)
        bars = self.provider.get_daily_bars(codes, start, target_date)
        return self.repo.upsert_daily_bars(bars)

    def update_pool_bars(self, pool_name: str, target_date: date) -> int:
        codes = self.repo.get_latest_pool_codes(pool_name)
        return self.update_bars(codes, target_date)

    def update_benchmark(self, target_date: date,
                         benchmark_codes: list[str] | None = None) -> int:
        codes = benchmark_codes or _DEFAULT_BENCHMARKS
        if self.provider is None:
            return 0
        start = target_date - timedelta(days=10)
        bars = self.provider.get_index_bars(codes, start, target_date)
        return self.repo.upsert_daily_bars(bars)

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
        benchmarks = benchmark_codes or _DEFAULT_BENCHMARKS
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
