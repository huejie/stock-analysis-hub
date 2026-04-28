"""每日数据备份脚本。备份 data/stock.db 到 data/backup/，仅保留最新一份。

用法:
  手动:   python backup.py
  定时:   每晚8点自动执行（见下方 Windows 任务计划配置说明）
"""
import shutil
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "stock.db"
BACKUP_DIR = Path(__file__).parent / "data" / "backup"


def backup():
    if not DB_PATH.exists():
        print("[ERROR] 数据库文件不存在:", DB_PATH)
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # 删除旧备份
    for old in BACKUP_DIR.glob("stock_*.db"):
        old.unlink()
        print(f"[INFO] 删除旧备份: {old.name}")

    # 创建新备份（带日期时间戳）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"stock_{timestamp}.db"
    shutil.copy2(str(DB_PATH), str(backup_path))

    size_kb = backup_path.stat().st_size / 1024
    print(f"[OK] 备份完成: {backup_path.name} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    backup()
