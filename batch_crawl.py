"""批量爬取3-5月缺失数据"""
import logging
import sys
import time
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("batch")

from backend.crawler import crawl_and_save, is_trade_day
from backend.database import Database

# 已有数据的日期
EXISTING = {
    "2026-04-17", "2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23",
    "2026-04-24", "2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30",
}

# 生成3-5月所有交易日
HOLIDAYS = {"2026-04-05","2026-04-06","2026-05-01","2026-05-02"}
WORK_WEEKEND = {"2026-01-18","2026-01-19"}

trade_days = []
d = date(2026, 3, 1)
while d <= date(2026, 5, 7):
    ds = d.strftime("%Y-%m-%d")
    if ds in WORK_WEEKEND:
        trade_days.append(d)
    elif ds not in HOLIDAYS and d.weekday() < 5:
        trade_days.append(d)
    d += timedelta(days=1)

# 过滤掉已有数据
missing = [d for d in trade_days if d.strftime("%Y-%m-%d") not in EXISTING]
logger.info("需爬取 %d 天数据", len(missing))

db = Database()
success = 0
fail = 0
for i, td in enumerate(missing):
    ds = td.strftime("%Y-%m-%d")
    logger.info("[%d/%d] 爬取 %s", i+1, len(missing), ds)
    try:
        crawl_and_save(db=db, target_date=td)
        success += 1
    except Exception as e:
        logger.error("爬取 %s 失败: %s", ds, e)
        fail += 1
    # 每天间隔1秒，避免限流
    if i < len(missing) - 1:
        time.sleep(1)

logger.info("完成! 成功 %d 天, 失败 %d 天", success, fail)
