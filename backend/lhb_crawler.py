import json
import logging
import re
import time
from datetime import date, timedelta

import httpx

from backend.database import Database

logger = logging.getLogger("lhb_crawler")

LHB_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CONCEPT_API = "https://push2.eastmoney.com/api/qt/stock/get"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# 境外机构营业部名称关键词（实际名称含"股份有限公司"等，关键词需足够短才能匹配）
FOREIGN_INSTITUTIONS = [
    "国泰海通证券股份有限公司总部",
    "中信证券股份有限公司上海分公司",
    "瑞银证券有限责任公司上海花园石桥路",
    "摩根大通证券",
    "高盛(中国)证券",
    "高盛（中国）证券",
]

INST_SEAT_NAME = "机构专用"

# 机构密集阈值：买+卖共10席，机构席位 >= 此值
INST_THRESHOLD = 6


def fetch_lhb_data(date_str: str) -> list[dict]:
    """抓取指定日期的龙虎榜汇总数据。"""
    try:
        resp = httpx.get(
            LHB_API,
            params={
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": "ALL",
                "pageNumber": 1,
                "pageSize": 500,
                "sortColumns": "SECURITY_CODE",
                "sortTypes": 1,
                "source": "WEB",
                "client": "WEB",
                "filter": f"(TRADE_DATE='{date_str}')",
            },
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.error("龙虎榜数据请求失败 %s: %s", date_str, e)
        return []

    if not body.get("success"):
        logger.warning("龙虎榜数据返回异常 %s: %s", date_str, body.get("message"))
        return []

    rows = (body.get("result") or {}).get("data", [])
    if not rows:
        return []

    records = []
    for item in rows:
        records.append({
            "date": date_str,
            "stock_code": item.get("SECURITY_CODE", ""),
            "stock_name": item.get("SECURITY_NAME_ABBR", ""),
            "close_price": item.get("CLOSE_PRICE"),
            "change_rate": item.get("CHANGE_RATE"),
            "billboard_buy_amt": item.get("BILLBOARD_BUY_AMT"),
            "billboard_sell_amt": item.get("BILLBOARD_SELL_AMT"),
            "billboard_net_amt": item.get("BILLBOARD_NET_AMT"),
            "billboard_deal_amt": item.get("BILLBOARD_DEAL_AMT"),
            "deal_net_ratio": item.get("DEAL_NET_RATIO"),
            "deal_amount_ratio": item.get("DEAL_AMOUNT_RATIO"),
            "turnover_rate": item.get("TURNOVERRATE"),
            "reason": item.get("EXPLANATION", ""),
            "d1_change": item.get("D1_CLOSE_ADJCHRATE"),
            "d2_change": item.get("D2_CLOSE_ADJCHRATE"),
            "d5_change": item.get("D5_CLOSE_ADJCHRATE"),
            "d10_change": item.get("D10_CLOSE_ADJCHRATE"),
        })

    return records


def fetch_trading_desk_details(date_str: str, stock_code: str, stock_name: str) -> list[dict]:
    """抓取指定股票在指定日期的买卖营业部明细。"""
    records = []
    for side, report_name in [("buy", "RPT_BILLBOARD_DAILYDETAILSBUY"), ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL")]:
        try:
            resp = httpx.get(
                LHB_API,
                params={
                    "reportName": report_name,
                    "columns": "ALL",
                    "pageNumber": 1,
                    "pageSize": 50,
                    "sortColumns": "BUY" if side == "buy" else "SELL",
                    "sortTypes": -1,
                    "source": "WEB",
                    "client": "WEB",
                    "filter": f'(TRADE_DATE=\'{date_str}\')(SECURITY_CODE="{stock_code}")',
                },
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            logger.warning("营业部明细请求失败 %s %s %s: %s", date_str, stock_code, side, e)
            continue

        result = body.get("result") or {}
        for item in result.get("data", []):
            records.append({
                "date": date_str,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "side": side,
                "dept_name": item.get("OPERATEDEPT_NAME", ""),
                "buy_amt": item.get("BUY"),
                "sell_amt": item.get("SELL"),
                "net_amt": item.get("NET"),
            })

    return records


def fetch_concept_tags(stock_code: str) -> list[str]:
    """获取个股所属概念板块标签。"""
    prefix = "1" if stock_code[0] in "6" else "0"
    try:
        resp = httpx.get(
            CONCEPT_API,
            params={"secid": f"{prefix}.{stock_code}", "fields": "f127,f129"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data")
        if not data:
            return []
        industry = data.get("f127", "")
        concepts = data.get("f129", "")
        tags = []
        if industry:
            tags.append(industry)
        if concepts:
            tags.extend(c.strip() for c in concepts.split(",") if c.strip())
        return tags
    except Exception as e:
        logger.warning("概念板块获取失败 %s: %s", stock_code, e)
        return []


def is_foreign_institution(dept_name: str) -> bool:
    """判断营业部是否为境外机构。"""
    return any(kw in dept_name for kw in FOREIGN_INSTITUTIONS)


def is_institutional_seat(dept_name: str) -> bool:
    """判断是否为机构专用席位。"""
    return INST_SEAT_NAME in dept_name


def identify_signals(date_str: str, summary_records: list[dict],
                     trading_desk_records: list[dict]) -> list[dict]:
    """从营业部明细中识别信号股。"""
    # 按股票分组营业部数据
    desk_map: dict[str, list[dict]] = {}
    for rec in trading_desk_records:
        code = rec["stock_code"]
        desk_map.setdefault(code, []).append(rec)

    # 汇总数据映射
    summary_map = {r["stock_code"]: r for r in summary_records}

    signals = []
    processed = set()

    for code, desks in desk_map.items():
        summary = summary_map.get(code, {})

        # 1. 境外机构信号
        foreign_depts = [d for d in desks if is_foreign_institution(d["dept_name"])]
        if foreign_depts:
            key = (code, "foreign")
            if key not in processed:
                processed.add(key)
                buy = sum(d.get("buy_amt") or 0 for d in foreign_depts)
                sell = sum(d.get("sell_amt") or 0 for d in foreign_depts)
                signals.append({
                    "date": date_str,
                    "stock_code": code,
                    "stock_name": foreign_depts[0]["stock_name"],
                    "signal_type": "foreign",
                    "close_price": summary.get("close_price"),
                    "change_rate": summary.get("change_rate"),
                    "buy_amt": buy,
                    "sell_amt": sell,
                    "net_amt": buy - sell,
                    "inst_count": None,
                    "concept_tags": None,
                })

        # 2. 机构密集信号（龙虎榜标准：买方前5 + 卖方前5 = 10席）
        top_buy = sorted(
            [d for d in desks if d["side"] == "buy"],
            key=lambda d: d.get("buy_amt") or 0, reverse=True,
        )[:5]
        top_sell = sorted(
            [d for d in desks if d["side"] == "sell"],
            key=lambda d: d.get("sell_amt") or 0, reverse=True,
        )[:5]
        top10 = top_buy + top_sell
        inst_count = sum(1 for d in top10 if is_institutional_seat(d["dept_name"]))

        if inst_count >= INST_THRESHOLD:
            key = (code, "inst_dense")
            if key not in processed:
                processed.add(key)
                inst_depts = [d for d in top10 if is_institutional_seat(d["dept_name"])]
                buy = sum(d.get("buy_amt") or 0 for d in inst_depts)
                sell = sum(d.get("sell_amt") or 0 for d in inst_depts)
                signals.append({
                    "date": date_str,
                    "stock_code": code,
                    "stock_name": desks[0]["stock_name"],
                    "signal_type": "inst_dense",
                    "close_price": summary.get("close_price"),
                    "change_rate": summary.get("change_rate"),
                    "buy_amt": buy,
                    "sell_amt": sell,
                    "net_amt": buy - sell,
                    "inst_count": inst_count,
                    "concept_tags": None,
                })

    return signals


def crawl_lhb(db: Database | None = None, target_date: date | None = None) -> dict:
    """主函数：抓取龙虎榜数据并存入数据库。

    Returns:
        {"date": str, "summary_count": int, "signal_count": int}
    """
    if db is None:
        db = Database()

    if target_date is None:
        target_date = date.today()

    target_str = target_date.strftime("%Y-%m-%d")
    logger.info("===== 开始抓取龙虎榜 %s =====", target_str)

    # 1. 抓取日汇总
    summary_records = fetch_lhb_data(target_str)
    if summary_records:
        try:
            db.upsert_lhb_records(summary_records)
            logger.info("龙虎榜汇总入库: %d 条", len(summary_records))
        except Exception as e:
            logger.error("龙虎榜汇总入库失败: %s", e)
    else:
        logger.warning("未获取到龙虎榜汇总数据 %s", target_str)

    # 2. 抓取每只股票的营业部明细
    all_desk_records = []
    unique_stocks = {r["stock_code"]: r["stock_name"] for r in summary_records}
    for code, name in unique_stocks.items():
        desks = fetch_trading_desk_details(target_str, code, name)
        all_desk_records.extend(desks)
        logger.info("  %s %s: %d 条营业部记录", code, name, len(desks))

    if all_desk_records:
        try:
            db.upsert_lhb_trading_desk(all_desk_records)
            logger.info("营业部明细入库: %d 条", len(all_desk_records))
        except Exception as e:
            logger.error("营业部明细入库失败: %s", e)

    # 3. 识别信号股（排除 ST 股票）
    non_st_stocks = {r["stock_code"] for r in summary_records if "ST" not in r["stock_name"].upper()}
    filtered_desks = [d for d in all_desk_records if d["stock_code"] in non_st_stocks]
    filtered_summary = [r for r in summary_records if r["stock_code"] in non_st_stocks]
    signals = identify_signals(target_str, filtered_summary, filtered_desks)
    logger.info("识别信号股: %d 只（境外机构=%d, 机构密集=%d）",
                len(signals),
                sum(1 for s in signals if s["signal_type"] == "foreign"),
                sum(1 for s in signals if s["signal_type"] == "inst_dense"))

    # 4. 获取信号股的概念板块标签
    for signal in signals:
        tags = fetch_concept_tags(signal["stock_code"])
        signal["concept_tags"] = json.dumps(tags, ensure_ascii=False) if tags else "[]"

    if signals:
        try:
            db.upsert_lhb_signals(signals)
            logger.info("信号股入库: %d 条", len(signals))
        except Exception as e:
            logger.error("信号股入库失败: %s", e)

    # 5. 自动更新股池
    try:
        pool_result = update_lhb_pool(db)
        logger.info("股池自动更新: 更新 %d 只, 跳过 %d 只", pool_result["updated"], pool_result["skipped"])
    except Exception as e:
        logger.error("股池自动更新失败: %s", e)

    logger.info("===== 龙虎榜抓取完成 %s =====", target_str)
    return {"date": target_str, "summary_count": len(summary_records), "signal_count": len(signals)}


def _stock_prefix(code: str) -> str:
    return "sz" if code[0] in "03" else "sh"


def fetch_kline_range(stock_code: str, start_date: str, end_date: str) -> list[dict]:
    """获取指定股票从 start_date 到 end_date 的日 K 线数据（前复权）。

    Returns:
        [{date: str, close: float}, ...] 按 date 升序
    """
    prefix = _stock_prefix(stock_code)
    symbol = f"{prefix}{stock_code}"
    try:
        resp = httpx.get(
            TENCENT_KLINE,
            params={"param": f"{symbol},day,{start_date},{end_date},320,qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        text = resp.text
        idx = text.find("{")
        if idx < 0:
            return []
        data = json.loads(text[idx:])
        klines = data.get("data", {}).get(symbol, {}).get("qfqday")
        if not klines:
            klines = data.get("data", {}).get(symbol, {}).get("day")
        if not klines:
            return []
        # kline: [日期, 开盘, 收盘, 最高, 最低, 成交量(手)]
        return [{"date": k[0], "close": float(k[2])} for k in klines]
    except Exception as e:
        logger.warning("K线获取失败 %s: %s", stock_code, e)
        return []


def update_lhb_pool(db: Database | None = None, max_items: int = 500) -> dict:
    """更新龙虎榜股池：从 lhb_signals 同步入选记录，并计算后续涨跌幅。

    Args:
        max_items: 单次最多处理的跟踪记录数，避免超时

    Returns:
        {"updated": int, "skipped": int, "remaining": int}
    """
    if db is None:
        db = Database()

    # 1. 从 lhb_signals 同步最近 30 天的入选记录到 lhb_pool（增量）
    thirty_days_ago = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    all_signals = db.query_lhb_signals()  # 全量，但下面只取近30天（已排除ST）
    pool_records = []
    for s in all_signals:
        if s["date"] < thirty_days_ago:
            continue
        # 跳过非A股（转债、基金等）
        code = s["stock_code"]
        if not code.startswith(("00", "30", "60", "68")):
            continue
        tags = s.get("concept_tags")
        if isinstance(tags, list):
            tags = json.dumps(tags, ensure_ascii=False)
        pool_records.append({
            "signal_date": s["date"],
            "stock_code": code,
            "stock_name": s["stock_name"],
            "signal_type": s["signal_type"],
            "entry_price": s.get("close_price"),
            "concept_tags": tags or "[]",
            "d1_change": None,
            "d3_change": None,
            "d5_change": None,
            "d10_change": None,
            "d20_change": None,
            "d30_change": None,
            "latest_price": None,
            "latest_date": None,
            "tracking_days": 0,
        })
    if pool_records:
        db.upsert_lhb_pool(pool_records)
        logger.info("股池同步: %d 条近30天信号入库", len(pool_records))

    # 2. 获取所有未完成跟踪的记录（只处理近30天内的）
    tracking = db.query_lhb_pool_tracking()
    # 只保留近30天的记录，避免处理历史数据
    tracking = [t for t in tracking if t["signal_date"] >= thirty_days_ago]
    # 限制单次处理数量，避免超时
    remaining = len(tracking)
    tracking = tracking[:max_items]
    if not tracking:
        logger.info("股池更新: 无需跟踪的记录")
        return {"updated": 0, "skipped": 0, "remaining": 0}

    today_str = date.today().strftime("%Y-%m-%d")
    updated = 0
    skipped = 0

    for item in tracking:
        signal_date = item["signal_date"]
        stock_code = item["stock_code"]

        # 获取从入选日到今天的 K 线
        start = signal_date
        klines = fetch_kline_range(stock_code, start, today_str)
        if not klines:
            skipped += 1
            continue

        # 入选日收盘价（第一天）
        entry_price = item.get("entry_price") or (klines[0]["close"] if klines else None)
        if not entry_price:
            skipped += 1
            continue

        # 入选日之后的交易日（排除入选日当天）
        trading_days = []
        for k in klines:
            if k["date"] > signal_date:
                trading_days.append(k)

        tracking_days = len(trading_days)
        latest = trading_days[-1] if trading_days else klines[-1]
        latest_price = latest["close"]
        latest_date = latest["date"]

        # 计算累计涨跌幅（使用闭包捕获 entry_price）
        _ep = entry_price
        def cum_change(idx: int, _entry=_ep, _tdays=trading_days) -> float | None:
            if idx < len(_tdays):
                return round((_tdays[idx]["close"] - _entry) / _entry * 100, 2)
            return None

        record = {
            "signal_date": signal_date,
            "stock_code": stock_code,
            "stock_name": item["stock_name"],
            "signal_type": item["signal_type"],
            "entry_price": entry_price,
            "concept_tags": item.get("concept_tags", "[]"),
            "d1_change": cum_change(0),
            "d3_change": cum_change(2),
            "d5_change": cum_change(4),
            "d10_change": cum_change(9),
            "d20_change": cum_change(19),
            "d30_change": cum_change(29),
            "latest_price": latest_price,
            "latest_date": latest_date,
            "tracking_days": tracking_days,
        }
        db.upsert_lhb_pool([record])
        updated += 1

        time.sleep(0.15)  # 缩短间隔，避免请求过快

    remaining = remaining - updated - skipped
    logger.info("股池更新完成: 更新 %d 只, 跳过 %d 只, 剩余 %d 只", updated, skipped, remaining)
    return {"updated": updated, "skipped": skipped, "remaining": remaining}
