#!/usr/bin/env python3
"""补全 stock_records 中 price_change_pct 为 NULL 的记录"""
import sqlite3
import json
import time
import httpx
from datetime import date, timedelta

DB_PATH = "data/stock.db"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _stock_prefix(code: str) -> str:
    if code.startswith(("6",)):
        return "sh"
    return "sz"


def fetch_kline_data(code: str, date_str: str) -> dict | None:
    """获取单只股票的涨跌幅数据"""
    dt = date.fromisoformat(date_str)
    start = (dt - timedelta(days=10)).strftime("%Y-%m-%d")
    prefix = _stock_prefix(code)
    symbol = f"{prefix}{code}"

    try:
        resp = httpx.get(
            TENCENT_KLINE,
            params={"param": f"{symbol},day,{start},{date_str},10,qfq"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        text = resp.text
        idx = text.find("{")
        if idx < 0:
            return None
        data = json.loads(text[idx:])
        klines = data.get("data", {}).get(symbol, {}).get("qfqday")
        if not klines:
            klines = data.get("data", {}).get(symbol, {}).get("day")

        if not klines or len(klines) < 1:
            return None

        today = klines[-1]
        close = float(today[2])

        pct = None
        price_action = f"收{close}"
        if len(klines) >= 2:
            prev_close = float(klines[-2][2])
            pct = round((close - prev_close) / prev_close * 100, 2)
            sign = "+" if pct > 0 else ""
            price_action = f"收{close} {sign}{pct}%"

        return {
            "price_change_pct": pct,
            "price_action": price_action,
        }
    except Exception as e:
        print(f"  [ERROR] {code} @ {date_str}: {e}")
        return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 查找所有 price_change_pct 为 NULL 的记录
    rows = conn.execute(
        "SELECT id, date, stock_code, stock_name FROM stock_records WHERE price_change_pct IS NULL ORDER BY date"
    ).fetchall()

    print(f"Found {len(rows)} records to backfill")

    updated = 0
    failed = 0
    for row in rows:
        rec_id = row["id"]
        date_str = row["date"]
        code = row["stock_code"]
        name = row["stock_name"]

        print(f"  Processing: {name}({code}) @ {date_str} ... ", end="", flush=True)
        data = fetch_kline_data(code, date_str)

        if data and data["price_change_pct"] is not None:
            conn.execute(
                "UPDATE stock_records SET price_change_pct = ?, price_action = ? WHERE id = ?",
                (data["price_change_pct"], data["price_action"], rec_id),
            )
            conn.commit()
            print(f"OK -> {data['price_change_pct']}%  {data['price_action']}")
            updated += 1
        else:
            print("SKIP (no data)")
            failed += 1

        time.sleep(0.3)

    conn.close()
    print(f"\nDone! Updated: {updated}, Skipped: {failed}")


if __name__ == "__main__":
    main()
