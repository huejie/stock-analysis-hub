#!/usr/bin/env python
"""导出 AI 分析所需数据，供 Hermes 智能体分析使用。

用法:
    python export_ai_data.py                    # 导出今日数据（默认）
    python export_ai_data.py 2026-06-05         # 导出指定日期
    python export_ai_data.py --type stock-pick   # 导出选股数据
    python export_ai_data.py --type diagnosis    # 导出信号诊断数据

输出文件:
    data/ai_export_{type}_{date}.md

将输出文件内容复制到 Hermes 中，结合项目 prompts/ 目录下的模板进行分析。
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# 让脚本可以直接运行
sys.path.insert(0, str(Path(__file__).parent))

from backend.database import Database


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


def _parse_json_field(val: str | None) -> list:
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


def export_daily_review(db: Database, date_str: str) -> str:
    """导出每日复盘所需数据。"""
    records = db.query_by_date(date_str)
    if not records:
        return f"## {date_str} 无热榜数据\n\n请先拉取数据后再导出。"

    prev_date = db.query_prev_trade_date(date_str)
    prev_records = db.query_by_date(prev_date) if prev_date else []
    signals = db.query_lhb_signals(date_str)
    backtest = db.query_backtest(months=1)
    streaks = db.query_streak_stats(days=5, min_streak=2)

    lines = [
        f"# 每日复盘数据导出 — {date_str}",
        "",
        f"昨日交易日: {prev_date or '无'}",
        "",
        "## 一、今日热榜 Top10 变化",
        "",
    ]

    prev_map = {r["stock_code"]: r for r in prev_records}
    for r in records:
        code = r["stock_code"]
        prev = prev_map.get(code)
        tags = _parse_json_field(r.get("sector_tags"))
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

    # 龙虎榜信号
    lines.append("")
    lines.append("## 二、龙虎榜信号股")
    lines.append("")
    if not signals:
        lines.append("今日无龙虎榜信号股。")
    else:
        for s in signals:
            stype = "境外机构" if s["signal_type"] == "foreign" else "机构密集"
            tags = _parse_json_field(s.get("concept_tags"))
            tag_str = ", ".join(tags[:3]) if tags else ""
            lines.append(
                f"- {s['stock_name']}({s['stock_code']}) [{stype}] "
                f"净买入:{_fmt_amt(s.get('net_amt'))} "
                f"买入:{_fmt_amt(s.get('buy_amt'))} 卖出:{_fmt_amt(s.get('sell_amt'))} "
                f"涨跌:{_fmt_pct(s.get('change_rate'))} "
                f"板块:[{tag_str}]"
            )

    # 胜率回测
    lines.append("")
    lines.append("## 三、信号股历史胜率（近1个月）")
    lines.append("")
    total = backtest.get("total_signals", 0)
    overall_wr = backtest.get("overall_win_rate")
    lines.append(f"总信号数: {total}")
    if overall_wr is not None:
        lines.append(f"整体 D5 胜率: {overall_wr:.1%} 平均涨幅: {_fmt_pct(backtest.get('overall_avg_change'))}")
    for h in backtest.get("horizon_stats", []):
        wr = h.get("win_rate")
        if wr is not None:
            lines.append(
                f"  {h['horizon'].upper()}: 胜率{wr:.1%} "
                f"均涨{_fmt_pct(h.get('avg_change'))} "
                f"样本{h.get('total', 0)}"
            )
    for p in backtest.get("period_stats", []):
        wr = p.get("win_rate")
        if wr is not None:
            lines.append(
                f"  {p['period']}: 胜率{wr:.1%} "
                f"均涨{_fmt_pct(p.get('avg_change'))} "
                f"样本{p.get('count', 0)}"
            )

    # 连续上榜
    lines.append("")
    lines.append("## 四、近5日连续上榜趋势")
    lines.append("")
    streak_list = streaks.get("streaks", [])
    if not streak_list:
        lines.append("近5日无连续上榜股票。")
    else:
        for s in streak_list[:10]:
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


def export_stock_pick(db: Database) -> str:
    """导出智能选股所需数据。"""
    today = date.today().isoformat()
    records = db.query_by_date(today)
    signals = db.query_lhb_signals(today)
    pool = db.query_lhb_pool()
    backtest = db.query_backtest(months=3)

    lines = [
        f"# 智能选股数据导出 — {today}",
        "",
        "## 一、今日热榜候选",
        "",
    ]

    for r in records:
        tags = _parse_json_field(r.get("sector_tags"))
        tag_str = ", ".join(tags[:3]) if tags else "无"
        lines.append(
            f"#{r['rank']} {r['stock_name']}({r['stock_code']}) "
            f"热度:{r.get('heat_value', '-') and f'{r['heat_value']:.0f}w'} "
            f"涨跌:{_fmt_pct(r.get('price_change_pct'))} "
            f"板块:[{tag_str}]"
        )

    lines.append("")
    lines.append("## 二、龙虎榜信号股候选")
    lines.append("")
    if not signals:
        lines.append("今日无信号股。")
    else:
        for s in signals:
            stype = "境外机构" if s["signal_type"] == "foreign" else "机构密集"
            lines.append(
                f"- {s['stock_name']}({s['stock_code']}) [{stype}] "
                f"净买入:{_fmt_amt(s.get('net_amt'))} "
                f"涨跌:{_fmt_pct(s.get('change_rate'))}"
            )

    lines.append("")
    lines.append("## 三、股池追踪（活跃）")
    lines.append("")
    if not pool:
        lines.append("暂无活跃股池记录。")
    else:
        for p in pool:
            lines.append(
                f"- {p['stock_name']}({p['stock_code']}) "
                f"信号:[{p.get('signal_types', '')}] "
                f"入场:{p.get('entry_price', '-')} 最新:{p.get('latest_price', '-')} "
                f"跟踪{p.get('tracking_days', 0)}天 "
                f"D5:{_fmt_pct(p.get('d5_change'))} D10:{_fmt_pct(p.get('d10_change'))}"
            )

    lines.append("")
    lines.append("## 四、信号胜率数据（近3个月）")
    lines.append("")
    overall_wr = backtest.get("overall_win_rate")
    if overall_wr is not None:
        lines.append(f"整体胜率: {overall_wr:.1%} 样本: {backtest.get('total_signals', 0)}")
    for h in backtest.get("horizon_stats", []):
        wr = h.get("win_rate")
        if wr is not None:
            lines.append(f"  {h['horizon'].upper()}: 胜率{wr:.1%} 均涨{_fmt_pct(h.get('avg_change'))}")

    return "\n".join(lines)


def export_diagnosis(db: Database, period_days: int = 30) -> str:
    """导出信号诊断所需数据。"""
    today = date.today()
    start_str = (today - timedelta(days=period_days)).isoformat()
    end_str = today.isoformat()

    overall = db.query_backtest(months=max(1, period_days // 30))
    foreign = db.query_backtest(signal_type="foreign", months=max(1, period_days // 30))
    inst = db.query_backtest(signal_type="inst_dense", months=max(1, period_days // 30))

    lines = [
        f"# 信号有效性诊断数据导出",
        f"统计周期: 近{period_days}天 ({start_str} ~ {end_str})",
        "",
    ]

    for label, bt in [("总体信号", overall), ("境外机构信号", foreign), ("机构密集信号", inst)]:
        lines.append(f"## {label}")
        lines.append("")
        total = bt.get("total_signals", 0)
        wr = bt.get("overall_win_rate")
        lines.append(f"信号数: {total}")
        if wr is not None:
            lines.append(f"D5 胜率: {wr:.1%} 平均涨幅: {_fmt_pct(bt.get('overall_avg_change'))}")
        for h in bt.get("horizon_stats", []):
            hwr = h.get("win_rate")
            if hwr is not None:
                lines.append(f"  {h['horizon'].upper()}: 胜率{hwr:.1%} 均涨{_fmt_pct(h.get('avg_change'))} 样本{h.get('total', 0)}")
        lines.append("")

    # 板块统计
    sectors = overall.get("sector_stats", [])
    if sectors:
        lines.append("## 板块胜率排行")
        lines.append("")
        for s in sectors[:15]:
            swr = s.get("win_rate")
            swr_str = f"{swr:.1%}" if swr is not None else "-"
            avg = s.get("avg_change")
            avg_str = _fmt_pct(avg) if avg is not None else "-"
            lines.append(f"- {s['sector']}: 胜率{swr_str} 均涨{avg_str} 样本{s.get('count', 0)}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="导出 AI 分析所需数据")
    parser.add_argument("date", nargs="?", default=date.today().isoformat(), help="目标日期 (YYYY-MM-DD)")
    parser.add_argument("--type", choices=["daily-review", "stock-pick", "diagnosis"], default="daily-review", help="导出类型")
    parser.add_argument("--period", type=int, default=30, help="诊断统计天数 (默认30)")
    args = parser.parse_args()

    db = Database()

    if args.type == "daily-review":
        content = export_daily_review(db, args.date)
    elif args.type == "stock-pick":
        content = export_stock_pick(db)
    elif args.type == "diagnosis":
        content = export_diagnosis(db, args.period)
    else:
        content = export_daily_review(db, args.date)

    # 保存到文件
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"ai_export_{args.type}_{args.date}.md"
    out_file.write_text(content, encoding="utf-8")

    print(f"✅ 已导出到: {out_file}")
    print(f"📄 共 {len(content)} 字符")
    print()
    print("下一步：")
    print(f"  1. 打开 Hermes")
    print(f"  2. 让 Hermes 读取 {out_file}")
    print(f"  3. 结合 backend/prompts/{args.type}.md 模板进行分析")
    print(f"  4. 将分析结果通过页面「导入分析」功能保存到系统")


if __name__ == "__main__":
    main()
