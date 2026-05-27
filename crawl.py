"""顽主杯数据爬虫入口脚本，供 cron 调用。

用法:
    python crawl.py              # 爬取今天数据
    python crawl.py 2026-04-30   # 爬取指定日期数据
    python crawl.py --backfill   # 补全历史缺失的涨跌幅数据
"""
import logging
import sys
from datetime import date
from pathlib import Path

# 确保项目根目录在 sys.path 中
project_root = str(Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.crawler import crawl_and_save, backfill_missing_quotes
from backend.database import Database
from backend.lhb_crawler import update_lhb_pool


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db = Database()

    if "--backfill" in sys.argv:
        backfill_missing_quotes(db=db)
        return

    target_date = None
    if len(sys.argv) > 1:
        try:
            target_date = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"日期格式错误: {sys.argv[1]}，请使用 YYYY-MM-DD")
            sys.exit(1)

    crawl_and_save(db=db, target_date=target_date)

    # 每次爬取后同步更新龙虎榜股池价格
    try:
        pool_result = update_lhb_pool(db)
        if pool_result["updated"] > 0:
            logging.info("股池价格更新: %d 条", pool_result["updated"])
    except Exception as e:
        logging.warning("股池价格更新失败: %s", e)


if __name__ == "__main__":
    main()
