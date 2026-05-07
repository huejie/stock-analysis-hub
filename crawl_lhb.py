"""东方财富龙虎榜数据爬虫入口脚本。

用法:
    python crawl_lhb.py              # 抓取今天龙虎榜
    python crawl_lhb.py 2026-04-30   # 抓取指定日期龙虎榜
"""
import logging
import sys
from datetime import date
from pathlib import Path

project_root = str(Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.lhb_crawler import crawl_lhb
from backend.database import Database


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    target_date = None
    if len(sys.argv) > 1:
        try:
            target_date = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"日期格式错误: {sys.argv[1]}，请使用 YYYY-MM-DD")
            sys.exit(1)

    db = Database()
    result = crawl_lhb(db=db, target_date=target_date)
    print(f"{result['date']} 龙虎榜共 {result['summary_count']} 条，信号股 {result['signal_count']} 只")


if __name__ == "__main__":
    main()
