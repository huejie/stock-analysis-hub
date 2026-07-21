"""AKShare 兜底模块 — 当主爬虫（东方财富/腾讯直连 API）失败时，通过 AKShare 获取数据。

用法：主爬虫返回空结果时调用对应的 fallback 函数。
"""
import logging
from datetime import date

logger = logging.getLogger("akshare_fallback")

# 延迟导入 AKShare，仅在需要时加载
_ak = None


def _get_ak():
    global _ak
    if _ak is None:
        try:
            import akshare as ak
            _ak = ak
            logger.info("AKShare %s 已加载", ak.__version__)
        except ImportError:
            logger.error("AKShare 未安装，兜底不可用")
            return None
    return _ak


# ---------------------------------------------------------------------------
# 龙虎榜汇总兜底
# ---------------------------------------------------------------------------

def fetch_lhb_summary_fallback(date_str: str) -> list[dict]:
    """当东方财富直连 API 失败时，通过 AKShare 获取龙虎榜汇总。

    Returns:
        与 fetch_lhb_data() 相同格式的 record 列表。
    """
    ak = _get_ak()
    if ak is None:
        return []

    try:
        df = ak.stock_lhb_detail_em(
            start_date=date_str.replace("-", ""),
            end_date=date_str.replace("-", ""),
        )
    except Exception as e:
        logger.warning("AKShare 龙虎榜汇总兜底失败 %s: %s", date_str, e)
        return []

    if df.empty:
        return []

    records = []
    for _, r in df.iterrows():
        records.append({
            "date": date_str,
            "stock_code": r["代码"],
            "stock_name": r["名称"],
            "close_price": r.get("收盘价"),
            "change_rate": r.get("涨跌幅"),
            "billboard_buy_amt": r.get("龙虎榜买入额"),
            "billboard_sell_amt": r.get("龙虎榜卖出额"),
            "billboard_net_amt": r.get("龙虎榜净买额"),
            "billboard_deal_amt": r.get("龙虎榜成交额"),
            "deal_net_ratio": r.get("净买额占总成交比"),
            "deal_amount_ratio": r.get("成交额占总成交比"),
            "turnover_rate": r.get("换手率"),
            "reason": r.get("上榜原因", ""),
            "d1_change": r.get("上榜后1日"),
            "d2_change": r.get("上榜后2日"),
            "d5_change": r.get("上榜后5日"),
            "d10_change": r.get("上榜后10日"),
        })
    logger.info("AKShare 兜底获取龙虎榜汇总 %s: %d 条", date_str, len(records))
    return records


# ---------------------------------------------------------------------------
# 营业部明细兜底
# ---------------------------------------------------------------------------

def fetch_trading_desk_fallback(date_str: str, stock_code: str, stock_name: str) -> list[dict]:
    """当东方财富直连 API 失败时，通过 AKShare 获取营业部买卖明细。

    注意：AKShare 返回买卖合一的表，需要拆分为 buy/side 两条记录。
    返回数量可能少于主爬虫（AKShare 默认返回前5大营业部）。

    Returns:
        与 fetch_trading_desk_details() 相同格式的 record 列表。
    """
    ak = _get_ak()
    if ak is None:
        return []

    try:
        df = ak.stock_lhb_stock_detail_em(
            symbol=stock_code,
            date=date_str.replace("-", ""),
        )
    except Exception as e:
        logger.warning("AKShare 营业部明细兜底失败 %s %s: %s", date_str, stock_code, e)
        return []

    if df.empty:
        return []

    records = []
    dept_counter: dict[str, int] = {}
    for _, r in df.iterrows():
        dept = r["交易营业部名称"]
        buy_amt = r.get("买入金额", 0) or 0
        sell_amt = r.get("卖出金额", 0) or 0

        # 判断主导方向
        if buy_amt >= sell_amt:
            side = "buy"
        else:
            side = "sell"

        idx = dept_counter.get(dept, 0)
        dept_counter[dept] = idx + 1

        records.append({
            "date": date_str,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "side": side,
            "dept_name": dept,
            "seat_index": idx,
            "buy_amt": buy_amt,
            "sell_amt": sell_amt,
            "net_amt": r.get("净额", buy_amt - sell_amt),
        })

    logger.info("AKShare 兜底获取营业部明细 %s %s: %d 条", date_str, stock_code, len(records))
    return records


# ---------------------------------------------------------------------------
# 概念板块兜底
# ---------------------------------------------------------------------------

def fetch_concept_tags_fallback(stock_code: str) -> list[str]:
    """当东方财富直连 API 失败时，通过 AKShare 获取概念板块标签。

    Returns:
        与 fetch_concept_tags() 相同格式的 tag 列表。
    """
    ak = _get_ak()
    if ak is None:
        return []

    try:
        df = ak.stock_individual_info_em(symbol=stock_code)
        tags = []
        for _, r in df.iterrows():
            item_key = r.iloc[0] if len(r) > 0 else ""
            item_val = r.iloc[1] if len(r) > 1 else ""
            if "行业" in str(item_key) and item_val:
                tags.append(str(item_val))
        return tags
    except Exception as e:
        logger.warning("AKShare 概念板块兜底失败 %s: %s", stock_code, e)
        return []
