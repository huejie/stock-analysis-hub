"""AI 分析引擎 — 组装数据、渲染 prompt、调用 LLM、解析结果。

三种分析模式：
1. daily_review  — 每日复盘 + 交易建议
2. stock_pick    — 智能选股助手
3. signal_diagnosis — 信号有效性诊断
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from backend.database import Database
from backend.llm_client import LLMClient

logger = logging.getLogger("ai_engine")

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """加载 prompt 模板文件。"""
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt 模板不存在: {path}")
    return path.read_text(encoding="utf-8")


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    prefix = "+" if val >= 0 else ""
    return f"{prefix}{val:.2f}%"


def _fmt_amt(val: float | None) -> str:
    if val is None:
        return "-"
    if abs(val) >= 1e8:
        return f"{val / 1e8:.2f}亿"
    if abs(val) >= 1e4:
        return f"{val / 1e4:.2f}万"
    return f"{val:.0f}元"


class AIEngine:
    """AI 分析引擎。"""

    def __init__(self, db: Database, llm: LLMClient) -> None:
        self.db = db
        self.llm = llm

    # ================================================================
    # 数据格式化方法
    # ================================================================

    def _format_hot_list(self, records: list[dict],
                         prev_records: list[dict]) -> str:
        """格式化热榜 Top10 变化数据。"""
        prev_map = {r["stock_code"]: r for r in prev_records}
        lines = []
        for r in records:
            code = r["stock_code"]
            prev = prev_map.get(code)
            tags_raw = r.get("sector_tags", "[]")
            try:
                tags = json.loads(tags_raw) if tags_raw else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            tag_str = ", ".join(tags[:3]) if tags else "无"

            change_str = _fmt_pct(r.get("price_change_pct"))
            heat = r.get("heat_value")
            heat_str = f"{heat:.0f}w" if heat else "-"

            if prev:
                rank_diff = prev["rank"] - r["rank"]
                prev_info = f"昨日#{prev['rank']}(变动{rank_diff:+d})"
            else:
                prev_info = "🆕 今日新进"

            lines.append(
                f"#{r['rank']} {r['stock_name']}({code}) "
                f"热度:{heat_str} 涨跌:{change_str} "
                f"板块:[{tag_str}] {prev_info}"
            )
        return "\n".join(lines)

    def _format_lhb_signals(self, signals: list[dict]) -> str:
        """格式化龙虎榜信号股数据。"""
        if not signals:
            return "今日无龙虎榜信号股。"

        lines = []
        for s in signals:
            stype = "境外机构" if s["signal_type"] == "foreign" else "机构密集"
            tags_raw = s.get("concept_tags", "[]")
            try:
                tags = json.loads(tags_raw) if tags_raw else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            tag_str = ", ".join(tags[:3]) if tags else ""

            net = s.get("net_amt")
            buy = s.get("buy_amt")
            sell = s.get("sell_amt")

            lines.append(
                f"- {s['stock_name']}({s['stock_code']}) [{stype}] "
                f"净买入:{_fmt_amt(net)} 买入:{_fmt_amt(buy)} 卖出:{_fmt_amt(sell)} "
                f"涨跌:{_fmt_pct(s.get('change_rate'))} "
                f"板块:[{tag_str}]"
            )
        return "\n".join(lines)

    def _format_backtest(self, backtest: dict) -> str:
        """格式化胜率回测数据。"""
        lines = []
        total = backtest.get("total_signals", 0)
        overall_wr = backtest.get("overall_win_rate")
        overall_avg = backtest.get("overall_avg_change")

        lines.append(f"总信号数: {total}")
        if overall_wr is not None:
            lines.append(f"整体 D5 胜率: {overall_wr:.1%} 平均涨幅: {_fmt_pct(overall_avg)}")

        # 按持仓周期
        for h in backtest.get("horizon_stats", []):
            wr = h.get("win_rate")
            avg = h.get("avg_change")
            total_h = h.get("total", 0)
            if wr is not None:
                lines.append(f"  {h['horizon'].upper()}: 胜率{wr:.1%} 均涨{_fmt_pct(avg)} (样本{total_h})")

        # 按月度
        for p in backtest.get("period_stats", []):
            wr = p.get("win_rate")
            if wr is not None:
                lines.append(
                    f"  {p['period']}: 胜率{wr:.1%} 均涨{_fmt_pct(p.get('avg_change'))} "
                    f"样本{p.get('count', 0)}"
                )

        return "\n".join(lines)

    def _format_streaks(self, streak_data: dict) -> str:
        """格式化连续上榜数据。"""
        streaks = streak_data.get("streaks", [])
        if not streaks:
            return "近5日无连续上榜股票。"

        lines = []
        for s in streaks[:10]:  # 最多展示10条
            trend_map = {"rising": "📈上升", "stable": "➡️稳定", "falling": "📉下降"}
            trend = trend_map.get(s.get("rank_trend", ""), "")
            horse = " 🐴黑马" if s.get("is_dark_horse") else ""
            tags = s.get("sector_tags", [])
            tag_str = ", ".join(tags[:2]) if tags else ""

            lines.append(
                f"- {s['stock_name']}({s['stock_code']}) "
                f"连板{s['streak_days']}天 {trend}{horse} "
                f"排名:{'→'.join(str(r) for r in s.get('ranks', [])[:5])} "
                f"板块:[{tag_str}]"
            )
        return "\n".join(lines)

    def _format_pool_summary(self, pool_data: list[dict]) -> str:
        """格式化股池追踪摘要（用于选股候选池）。"""
        if not pool_data:
            return ""

        lines = []
        for p in pool_data:
            d5 = p.get("d5_change")
            d10 = p.get("d10_change")
            tracking = p.get("tracking_days", 0)
            signals = p.get("signal_types", "")
            latest = p.get("latest_price")

            lines.append(
                f"- {p['stock_name']}({p['stock_code']}) "
                f"信号:[{signals}] 入场价:{p.get('entry_price', '-')} "
                f"最新价:{latest} 跟踪{tracking}天 "
                f"D5:{_fmt_pct(d5)} D10:{_fmt_pct(d10)}"
            )
        return "\n".join(lines)

    # ================================================================
    # 每日复盘
    # ================================================================

    async def generate_daily_review(self, date_str: str) -> dict:
        """生成每日 AI 复盘报告。"""
        logger.info("开始生成 AI 复盘: %s", date_str)

        # 1. 收集数据
        records = self.db.query_by_date(date_str)
        if not records:
            return {"error": f"{date_str} 无热榜数据，请先拉取数据"}

        prev_date = self.db.query_prev_trade_date(date_str)
        prev_records = self.db.query_by_date(prev_date) if prev_date else []
        signals = self.db.query_lhb_signals(date_str)
        backtest = self.db.query_backtest(months=1)
        streaks = self.db.query_streak_stats(days=5, min_streak=2)

        # 2. 格式化数据
        hot_list_text = self._format_hot_list(records, prev_records)
        lhb_text = self._format_lhb_signals(signals)
        backtest_text = self._format_backtest(backtest)
        streak_text = self._format_streaks(streaks)

        # 3. 加载并渲染 prompt
        system_prompt = _load_prompt("daily_review_system.md")
        user_template = _load_prompt("daily_review.md")
        user_prompt = user_template.format(
            date=date_str,
            hot_list_data=hot_list_text,
            lhb_signal_data=lhb_text,
            backtest_data=backtest_text,
            streak_data=streak_text,
        )

        # 4. 调用 LLM
        input_summary = f"hot_list:{len(records)} signals:{len(signals)} streaks:{len(streaks.get('streaks', []))}"
        result = await self.llm.chat(system_prompt, user_prompt)
        content = result["content"]

        # 5. 缓存到数据库
        self.db.save_ai_analysis(
            analysis_type="daily_review",
            date_str=date_str,
            input_summary=input_summary,
            response=content,
            model_name=result["model"],
            tokens_used=result["tokens_used"],
            duration_ms=result["duration_ms"],
        )

        logger.info("AI 复盘生成完成: %s, 耗时 %dms", date_str, result["duration_ms"])

        return {
            "date": date_str,
            "generated_at": date.today().isoformat(),
            "model": result["model"],
            "tokens_used": result["tokens_used"],
            "duration_ms": result["duration_ms"],
            "content": content,
        }

    def get_cached_daily_review(self, date_str: str) -> dict | None:
        """读取缓存的每日复盘。"""
        row = self.db.query_ai_analysis("daily_review", date_str)
        if not row:
            return None
        return {
            "date": date_str,
            "generated_at": row.get("created_at", ""),
            "model": row.get("model_name", ""),
            "tokens_used": row.get("tokens_used", 0),
            "duration_ms": row.get("duration_ms", 0),
            "content": row.get("response", ""),
        }

    # ================================================================
    # 智能选股
    # ================================================================

    async def stock_pick(self, strategy: str, custom_prompt: str = "",
                         sector_filter: list[str] | None = None,
                         max_results: int = 5) -> dict:
        """AI 智能选股。"""
        logger.info("AI 选股: strategy=%s, filter=%s", strategy, sector_filter)

        today = date.today().isoformat()

        # 1. 构建候选池
        records = self.db.query_by_date(today)
        signals = self.db.query_lhb_signals(today)
        pool = self.db.query_lhb_pool()
        streaks = self.db.query_streak_stats(days=5, min_streak=1)

        # 板块过滤
        if sector_filter:
            def match_sectors(r: dict) -> bool:
                tags_raw = r.get("sector_tags") or r.get("concept_tags") or "[]"
                try:
                    tags = json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    tags = []
                return any(sf in str(tags) for sf in sector_filter)

            records = [r for r in records if match_sectors(r)]
            signals = [s for s in signals if match_sectors(s)]

        # 2. 格式化候选池
        candidate_text = "### 热榜候选\n" + self._format_hot_list(records, [])
        if signals:
            candidate_text += "\n\n### 信号股候选\n" + self._format_lhb_signals(signals)
        if pool:
            candidate_text += "\n\n### 股池追踪（活跃）\n" + self._format_pool_summary(pool)

        backtest = self.db.query_backtest(months=3)
        backtest_text = self._format_backtest(backtest)
        streak_text = self._format_streaks(streaks)

        # 3. 策略映射
        strategy_map = {
            "aggressive": "偏好短线爆发力强的股票，敢于追高，关注连板股和强势信号",
            "balanced": "偏好信号确认+趋势配合的股票，兼顾安全边际，适合稳健操作",
            "conservative": "偏好低位放量、信号刚触发的股票，低吸为主，注重风控",
        }
        strategy_desc = strategy_map.get(strategy, strategy)
        user_preference = custom_prompt if custom_prompt else strategy_desc

        # 4. 加载 prompt 并调用
        system_prompt = _load_prompt("stock_pick_system.md")
        user_template = _load_prompt("stock_pick.md")
        user_prompt = user_template.format(
            user_preference=user_preference,
            candidate_stocks=candidate_text,
            backtest_data=backtest_text,
            streak_data=streak_text,
            max_results=max_results,
        )

        input_summary = f"strategy:{strategy} candidates:{len(records)+len(signals)}"
        result = await self.llm.chat(system_prompt, user_prompt)
        content = result["content"]

        # 5. 缓存
        self.db.save_ai_analysis(
            analysis_type="stock_pick",
            date_str=today,
            input_summary=input_summary,
            response=content,
            model_name=result["model"],
            tokens_used=result["tokens_used"],
            duration_ms=result["duration_ms"],
        )

        return {
            "strategy": strategy,
            "date": today,
            "generated_at": date.today().isoformat(),
            "model": result["model"],
            "content": content,
            "duration_ms": result["duration_ms"],
        }

    # ================================================================
    # 信号诊断
    # ================================================================

    async def signal_diagnosis(self, period_days: int = 30) -> dict:
        """AI 信号有效性诊断。"""
        logger.info("AI 信号诊断: period=%d days", period_days)

        today = date.today()
        end_str = today.isoformat()
        start_str = (today - timedelta(days=period_days)).isoformat()

        # 1. 按信号类型统计
        overall_backtest = self.db.query_backtest(months=max(1, period_days // 30))
        foreign_backtest = self.db.query_backtest(signal_type="foreign", months=max(1, period_days // 30))
        inst_backtest = self.db.query_backtest(signal_type="inst_dense", months=max(1, period_days // 30))

        # 2. 热榜情绪上下文
        streaks = self.db.query_streak_stats(days=5, min_streak=1)

        # 3. 格式化
        stats_text = f"### 总体信号统计\n{self._format_backtest(overall_backtest)}\n\n"
        stats_text += f"### 境外机构信号\n{self._format_backtest(foreign_backtest)}\n\n"
        stats_text += f"### 机构密集信号\n{self._format_backtest(inst_backtest)}\n"

        streak_text = self._format_streaks(streaks)

        # 4. 加载 prompt 并调用
        system_prompt = _load_prompt("signal_diagnosis_system.md")
        user_template = _load_prompt("signal_diagnosis.md")
        user_prompt = user_template.format(
            period=f"近{period_days}天",
            start_date=start_str,
            end_date=end_str,
            type_stats=stats_text,
            sector_stats=self._format_sector_from_backtest(overall_backtest),
            horizon_stats=self._format_horizon_from_backtest(overall_backtest),
            market_context=streak_text,
        )

        input_summary = f"period:{period_days}d total:{overall_backtest.get('total_signals', 0)}"
        result = await self.llm.chat(system_prompt, user_prompt)
        content = result["content"]

        # 5. 缓存
        self.db.save_ai_analysis(
            analysis_type="signal_diagnosis",
            date_str=end_str,
            input_summary=input_summary,
            response=content,
            model_name=result["model"],
            tokens_used=result["tokens_used"],
            duration_ms=result["duration_ms"],
        )

        return {
            "period_days": period_days,
            "start_date": start_str,
            "end_date": end_str,
            "generated_at": end_str,
            "model": result["model"],
            "content": content,
            "duration_ms": result["duration_ms"],
            "backtest_summary": {
                "overall_win_rate": overall_backtest.get("overall_win_rate"),
                "total_signals": overall_backtest.get("total_signals", 0),
                "foreign_win_rate": foreign_backtest.get("overall_win_rate"),
                "inst_win_rate": inst_backtest.get("overall_win_rate"),
            },
        }

    def get_cached_diagnosis(self, date_str: str) -> dict | None:
        """读取缓存的信号诊断。"""
        row = self.db.query_ai_analysis("signal_diagnosis", date_str)
        if not row:
            return None
        return {
            "date": date_str,
            "generated_at": row.get("created_at", ""),
            "model": row.get("model_name", ""),
            "content": row.get("response", ""),
            "duration_ms": row.get("duration_ms", 0),
        }

    # ================================================================
    # 辅助方法
    # ================================================================

    @staticmethod
    def _format_sector_from_backtest(backtest: dict) -> str:
        """从回测结果中提取板块统计。"""
        sectors = backtest.get("sector_stats", [])
        if not sectors:
            return "暂无板块统计数据。"
        lines = []
        for s in sectors[:15]:
            wr = s.get("win_rate")
            wr_str = f"{wr:.1%}" if wr is not None else "-"
            avg = s.get("avg_change")
            avg_str = _fmt_pct(avg) if avg is not None else "-"
            lines.append(f"- {s['sector']}: 胜率{wr_str} 均涨{avg_str} 样本{s.get('count', 0)}")
        return "\n".join(lines)

    @staticmethod
    def _format_horizon_from_backtest(backtest: dict) -> str:
        """从回测结果中提取持仓周期统计。"""
        horizons = backtest.get("horizon_stats", [])
        if not horizons:
            return "暂无持仓周期数据。"
        lines = []
        for h in horizons:
            wr = h.get("win_rate")
            wr_str = f"{wr:.1%}" if wr is not None else "-"
            avg = h.get("avg_change")
            avg_str = _fmt_pct(avg) if avg is not None else "-"
            lines.append(f"- {h['horizon'].upper()}: 胜率{wr_str} 均涨{avg_str} 样本{h.get('total', 0)}")
        return "\n".join(lines)
