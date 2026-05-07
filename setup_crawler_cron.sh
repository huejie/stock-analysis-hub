#!/usr/bin/env bash
# 顽主杯数据爬虫 cron 定时任务安装脚本
# 每天 20:00 自动执行爬虫
#
# 用法:
#   bash setup_crawler_cron.sh          # 安装
#   bash setup_crawler_cron.sh remove   # 卸载

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$PROJECT_DIR/crawl.py"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
CRON_MARKER="stock-crawler"
CRON_CMD="cd $PROJECT_DIR && $VENV_PYTHON $ENTRY >> $PROJECT_DIR/data/crawler.log 2>&1"
CRON_ENTRY="0 20 * * * $CRON_CMD  # $CRON_MARKER"

if [ "$1" = "remove" ]; then
    echo "正在移除爬虫 cron 任务..."
    (crontab -l 2>/dev/null | grep -v "$CRON_MARKER") | crontab -
    echo "已移除。"
    exit 0
fi

# 检查是否已存在
if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    echo "cron 任务已存在，跳过安装。"
    echo "如需重新安装，先运行: bash $0 remove"
    exit 0
fi

# 检查 Python 路径
if [ ! -f "$VENV_PYTHON" ]; then
    echo "警告: 未找到 .venv/bin/python，请先创建虚拟环境并安装依赖"
    echo "  cd $PROJECT_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 添加 cron 任务
(crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
echo "安装成功！"
echo ""
echo "当前 cron 任务:"
crontab -l | grep "$CRON_MARKER" || true
echo ""
echo "日志文件: $PROJECT_DIR/data/crawler.log"
echo "手动测试: $VENV_PYTHON $ENTRY"
echo "卸载命令: bash $0 remove"
