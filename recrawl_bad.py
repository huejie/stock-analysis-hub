"""重爬涨跌数据缺失的日期"""
import logging
import time
from datetime import date

from backend.crawler import crawl_and_save
from backend.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BAD_DATES = [
    "2026-03-31",
    "2026-04-07",
    "2026-04-08",
    "2026-04-10",
    "2026-04-17",
    "2026-04-20",
    "2026-04-21",
    "2026-04-22",
    "2026-04-23",
    "2026-04-24",
    "2026-04-28",
    "2026-04-30",
]

db = Database()
for i, ds in enumerate(BAD_DATES):
    logging.info("[%d/%d] 重爬 %s", i + 1, len(BAD_DATES), ds)
    crawl_and_save(db=db, target_date=date.fromisoformat(ds))
    if i < len(BAD_DATES) - 1:
        time.sleep(2)

logging.info("全部重爬完成!")
