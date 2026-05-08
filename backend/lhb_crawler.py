import json
import logging
import re
from datetime import date

import httpx

from backend.database import Database

logger = logging.getLogger("lhb_crawler")

LHB_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CONCEPT_API = "https://push2.eastmoney.com/api/qt/stock/get"
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

    # 3. 识别信号股
    signals = identify_signals(target_str, summary_records, all_desk_records)
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

    logger.info("===== 龙虎榜抓取完成 %s =====", target_str)
    return {"date": target_str, "summary_count": len(summary_records), "signal_count": len(signals)}
