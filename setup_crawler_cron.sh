#!/usr/bin/env bash
# 顽主杯数据爬虫 cron 定时任务安装脚本
# 每天 19:00 自动执行爬虫
#
# 用法:
#   bash setup_crawler_cron.sh          # 安装
#   bash setup_crawler_cron.sh remove   # 卸载

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

# 顽主杯爬虫：每天 19:00
CRAWL_MARKER="stock-crawler"
CRAWL_CMD="cd $PROJECT_DIR && $VENV_PYTHON $PROJECT_DIR/crawl.py >> $PROJECT_DIR/data/crawler.log 2>&1"
CRAWL_ENTRY="0 19 * * * $CRAWL_CMD  # $CRAWL_MARKER"

# 龙虎榜爬虫：每天 18:00
LHB_MARKER="stock-lhb-crawler"
LHB_CMD="cd $PROJECT_DIR && $VENV_PYTHON $PROJECT_DIR/crawl_lhb.py >> $PROJECT_DIR/data/crawler.log 2>&1"
LHB_ENTRY="0 18 * * * $LHB_CMD  # $LHB_MARKER"

if [ "$1" = "remove" ]; then
    echo "正在移除爬虫 cron 任务..."
    (crontab -l 2>/dev/null | grep -v -e "$CRAWL_MARKER" -e "$LHB_MARKER") | crontab -
    echo "已移除。"
    exit 0
fi

# 检查是否已存在
existing=$(crontab -l 2>/dev/null || true)
skip_crawl=false
skip_lhb=false
if echo "$existing" | grep -q "$CRAWL_MARKER"; then
    echo "顽主杯 cron 任务已存在，跳过。"
    skip_crawl=true
fi
if echo "$existing" | grep -q "$LHB_MARKER"; then
    echo "龙虎榜 cron 任务已存在，跳过。"
    skip_lhb=true
fi
if $skip_crawl && $skip_lhb; then
    echo "所有 cron 任务均已存在。如需重新安装，先运行: bash $0 remove"
    exit 0
fi

# 检查 Python 路径
if [ ! -f "$VENV_PYTHON" ]; then
    echo "警告: 未找到 .venv/bin/python，请先创建虚拟环境并安装依赖"
    echo "  cd $PROJECT_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 添加 cron 任务
if ! $skip_crawl; then
    (crontab -l 2>/dev/null; echo "$CRAWL_ENTRY") | crontab -
    echo "顽主杯 cron 已安装（每天 19:00）"
fi
if ! $skip_lhb; then
    (crontab -l 2>/dev/null; echo "$LHB_ENTRY") | crontab -
    echo "龙虎榜 cron 已安装（每天 18:00）"
fi
echo ""
echo "当前 cron 任务:"
crontab -l | grep -e "$CRAWL_MARKER" -e "$LHB_MARKER" || true
echo ""
echo "日志文件: $PROJECT_DIR/data/crawler.log"
echo "卸载命令: bash $0 remove"
