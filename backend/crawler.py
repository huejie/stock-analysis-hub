import json
import logging
import re
import time
from datetime import date, timedelta

import httpx

from backend.database import Database

logger = logging.getLogger("crawler")

# ===================== 2026年A股节假日+调休 =====================
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02",
    "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23",
    "2026-01-24", "2026-01-25", "2026-01-26", "2026-01-27",
    "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02",
    "2026-06-19", "2026-06-20",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07",
}
WORK_ON_WEEKEND_2026 = {
    "2026-01-18", "2026-01-19",
    "2026-09-27", "2026-10-10",
}

# ===================== API 配置 =====================
STOCK_API = "https://api.hunanwanzhu.com/api/wx/v2/hot/stock/buy/list"
INCOME_API = "https://api.hunanwanzhu.com/api/wx/v3/match/group/winlose/avg/allday?match_id=38"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://servicewechat.com/wxb299e10e65157301/80/page-frame.html",
    "Content-Type": "application/json",
}

TOP_N = 10


def is_trade_day(check_date: date) -> bool:
    date_str = check_date.strftime("%Y-%m-%d")
    if check_date.year != 2026:
        logger.warning("交易日历仅覆盖 2026 年，%s 使用默认工作日判断", date_str)
    if date_str in WORK_ON_WEEKEND_2026:
        return True
    if date_str in HOLIDAYS_2026:
        return False
    return check_date.weekday() < 5


def _stock_prefix(code: str) -> str:
    """根据股票代码判断沪深市场前缀。"""
    return "sz" if code[0] in "03" else "sh"


def fetch_stock_data(date_str: str) -> list[dict]:
    """爬取指定日期的热榜 Top N 股票数据。"""
    try:
        resp = httpx.get(
            STOCK_API,
            params={"stock_date": date_str, "page": 1, "page_size": 50},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.error("股票数据请求失败 %s: %s", date_str, e)
        return []

    if body.get("code") != 0:
        logger.warning("股票数据返回异常 %s: %s", date_str, body.get("message"))
        return []

    lst = body.get("data", {}).get("list", [])
    if not lst:
        return []

    # 只取前 TOP_N 条，过滤无效 stock_code
    valid_items = []
    for item in lst:
        code = item.get("stock_code", "")
        if re.fullmatch(r"\d{6}", code):
            valid_items.append(item)
        if len(valid_items) >= TOP_N:
            break

    if not valid_items:
        return []

    # 批量获取行情数据（涨跌幅、成交额）
    codes = [item["stock_code"] for item in valid_items]
    quotes = fetch_quotes(codes, date_str)

    records = []
    for idx, item in enumerate(valid_items, start=1):
        code = item["stock_code"]
        q = quotes.get(code, {})
        records.append({
            "date": date_str,
            "rank": idx,
            "stock_name": item.get("stock_name", ""),
            "stock_code": code,
            "heat_value": item.get("total_fund"),
            "sector_tags": json.dumps([], ensure_ascii=False),
            "price_change_pct": q.get("price_change_pct"),
            "turnover_amount": q.get("turnover_amount"),
            "holders_today": item.get("count"),
            "holders_yesterday": item.get("yes_count"),
            "price_action": item.get("stock_desc", "") or q.get("price_action", ""),
            "per_capital_pnl": None,
            "per_capital_position": None,
            "total_fund": item.get("total_fund"),
        })

    return records


def fetch_quotes(codes: list[str], date_str: str) -> dict[str, dict]:
    """从腾讯接口获取指定股票的行情数据（涨跌幅、成交额）。

    Args:
        codes: 股票代码列表（6位数字）
        date_str: 目标日期 YYYY-MM-DD

    Returns:
        {code: {"price_change_pct": float, "turnover_amount": float, "price_action": str}}
    """
    result: dict[str, dict] = {}
    # 往前多推几天，覆盖长假期（如清明节、国庆节）
    dt = date.fromisoformat(date_str)
    start = (dt - timedelta(days=10)).strftime("%Y-%m-%d")

    for code in codes:
        try:
            prefix = _stock_prefix(code)
            symbol = f"{prefix}{code}"
            resp = httpx.get(
                TENCENT_KLINE,
                params={"param": f"{symbol},day,{start},{date_str},10,qfq"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            text = resp.text
            idx = text.find("{")
            if idx < 0:
                continue
            data = json.loads(text[idx:])
            klines = data.get("data", {}).get(symbol, {}).get("qfqday")
            if not klines:
                klines = data.get("data", {}).get(symbol, {}).get("day")

            if len(klines) >= 1:
                # kline: [日期, 开盘, 收盘, 最高, 最低, 成交量(手)]
                today = klines[-1]
                close = float(today[2])
                vol_lots = float(today[5])  # 手
                turnover = round(vol_lots * 100 * close / 1e8, 2)  # 亿元

                # 涨跌幅需要前一日收盘
                pct = None
                price_action = f"收{close}"
                if len(klines) >= 2:
                    prev_close = float(klines[-2][2])
                    pct = round((close - prev_close) / prev_close * 100, 2)
                    sign = "+" if pct > 0 else ""
                    price_action = f"收{close} {sign}{pct}%"

                result[code] = {
                    "price_change_pct": pct,
                    "turnover_amount": turnover,
                    "price_action": price_action,
                }
        except Exception as e:
            logger.warning("行情数据获取失败 %s: %s", code, e)

        time.sleep(0.2)

    return result


def fetch_income_data() -> list[dict]:
    """爬取赛季收益数据，返回 season_daily_stats 格式的记录列表。"""
    try:
        resp = httpx.get(INCOME_API, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.error("收益数据请求失败: %s", e)
        return []

    if body.get("code") != 0:
        logger.warning("收益数据返回异常: %s", body.get("message"))
        return []

    data_list = body.get("data", {}).get("list", [])
    records = []
    for item in data_list:
        today_income = item.get("today_income", "0%")
        total_income = item.get("total_income", "0%")
        records.append({
            "date": item["stock_date"],
            "per_capital_pnl": _parse_pct(today_income),
            "per_capital_position": None,
        })

    return records


def _parse_pct(value: str) -> float | None:
    """将 '0.84%' 或 '-4.67%' 格式转为浮点数。"""
    if not value or value == "0%":
        return 0.0
    try:
        return float(value.rstrip("%"))
    except ValueError:
        return None


def crawl_and_save(db: Database | None = None, target_date: date | None = None) -> dict:
    """主函数：爬取数据并存入数据库。

    Args:
        db: 数据库实例，默认自动创建。
        target_date: 目标日期，默认为今天。

    Returns:
        {"date": str, "stock_count": int, "income_count": int, "skipped": bool}
    """
    if db is None:
        db = Database()

    if target_date is None:
        target_date = date.today()

    target_str = target_date.strftime("%Y-%m-%d")
    logger.info("===== 开始爬取 %s =====", target_str)

    if not is_trade_day(target_date):
        logger.info("%s 非交易日，跳过", target_str)
        return {"date": target_str, "stock_count": 0, "income_count": 0, "skipped": True}

    # 爬取股票数据
    stock_count = 0
    stock_records = fetch_stock_data(target_str)
    if stock_records:
        try:
            db.upsert_records(stock_records)
            stock_count = len(stock_records)
            logger.info("股票数据入库成功: %d 条", stock_count)
        except Exception as e:
            logger.error("股票数据入库失败: %s", e)
    else:
        logger.warning("未获取到股票数据")

    # 爬取收益数据（全量更新）
    income_count = 0
    income_records = fetch_income_data()
    if income_records:
        try:
            db.upsert_season_stats(income_records)
            income_count = len(income_records)
            logger.info("收益数据入库成功: %d 条", income_count)
        except Exception as e:
            logger.error("收益数据入库失败: %s", e)
    else:
        logger.warning("未获取到收益数据")

    logger.info("===== 爬取完成 %s =====", target_str)
    return {"date": target_str, "stock_count": stock_count, "income_count": income_count, "skipped": False}
