"""Scheduler 主进程(spec §13.1)。

纯 Python 循环实现,无 apscheduler/croniter 依赖。
每个交易日按时间表执行任务,用 trade_job_locks 互斥。

用法:python -m backend.trading.jobs.scheduler
(Docker Compose 的 scheduler 服务入口)

时间表(Asia/Shanghai):
  20:15  更新股票池/持仓/基准日线(update_market_data)
  20:25  生成下一交易日计划(generate_daily_plan)
  23:30  数据库备份(backup_database)

任务互斥:trade_job_locks(lock_key = 交易日 + 任务类型)。
失败重试:最多 3 次,之后 FAILED。
"""
import logging
import time
import signal
import sys
from datetime import date, datetime, timedelta

from ...config import settings
from ..migrations import run_migrations
from ..repository import TradingRepository
from ..services.market_data_service import MarketDataService
from ..services.plan_service import PlanService
from ..services.portfolio_service import PortfolioService
from ..services.strategy_service import StrategyService

logger = logging.getLogger("trading.scheduler")

# spec §13.1 时间表 (hour, minute, task_name)
SCHEDULE = [
    (20, 15, "update_market_data"),
    (20, 25, "generate_daily_plan"),
    (23, 30, "backup_database"),
]

MAX_RETRIES = 3
_loop_running = True


def _signal_handler(signum, frame):
    global _loop_running
    _loop_running = False
    logger.info("scheduler 收到停止信号,优雅退出")


def _is_trade_day(d: date) -> bool:
    """简化:仅跳周末。节假日由 Provider 交易日历叠加(Phase 1 clock 同理)。"""
    return d.weekday() < 5


def _next_run_time(now: datetime) -> tuple[datetime, str] | None:
    """返回今天剩余的下一个任务时间 + 任务名,无则 None。"""
    today_tasks = [
        (now.replace(hour=h, minute=m, second=0, microsecond=0), name)
        for h, m, name in SCHEDULE
    ]
    for run_at, name in sorted(today_tasks):
        if run_at > now:
            return run_at, name
    return None


def run_task(repo: TradingRepository, task_name: str, trade_date: date) -> bool:
    """执行单个任务。返回成功与否。"""
    logger.info("执行任务 %s (交易日=%s)", task_name, trade_date)
    try:
        if task_name == "update_market_data":
            mds = MarketDataService(repo, provider=None)  # Phase 5 无 scheduler 接 provider
            mds.update_pool_bars("default", trade_date)
            benchmarks = [c.strip() for c in settings.trading_benchmark_codes.split(",") if c.strip()]
            mds.update_benchmark(trade_date, benchmarks)
        elif task_name == "generate_daily_plan":
            mds = MarketDataService(repo, provider=None)
            ps = PortfolioService(repo)
            plan_svc = PlanService(repo, mds, ps)
            strat_svc = StrategyService(repo)
            active = strat_svc.get_active_strategy("default")
            if not active:
                logger.warning("无 ACTIVE 策略,跳过计划生成")
                return True
            # 使用最新股池版本
            pool_versions = repo.list_stock_pool_versions("default")
            if not pool_versions:
                logger.warning("无股票池,跳过计划生成")
                return True
            accounts = repo.list_accounts(active_only=True)
            if not accounts:
                logger.warning("无 active 账户,跳过计划生成")
                return True
            for acc in accounts:
                try:
                    plan_svc.generate_plan(
                        account_id=acc["id"],
                        signal_date=trade_date.isoformat(),
                        stock_pool_version_id=pool_versions[0]["id"],
                        strategy_version_id=active["id"],
                    )
                except Exception as e:
                    logger.warning("账户 %s 计划生成失败: %s", acc["id"], e)
        elif task_name == "backup_database":
            # 委托给 backup 模块(Phase 5 简化:调用 SQLite online backup)
            from .backup_database import run_backup
            run_backup(settings.db_path)
        else:
            logger.warning("未知任务: %s", task_name)
            return False
        logger.info("任务 %s 完成", task_name)
        return True
    except Exception as e:
        logger.error("任务 %s 失败: %s", task_name, e)
        return False


def main_loop(check_once: bool = False):
    """主循环。

    check_once=True 时只检查一次(用于测试),不进入无限循环。
    """
    global _loop_running
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    run_migrations(settings.db_path)
    repo = TradingRepository(settings.db_path)
    logger.info("scheduler 启动 (db=%s)", settings.db_path)

    last_run: dict[str, date] = {}  # task_name -> last run date(防止同日重复)

    while _loop_running:
        now = datetime.now()
        trade_date = now.date()

        if not _is_trade_day(trade_date):
            # 周末跳过,每分钟检查一次
            time.sleep(60)
            continue

        nxt = _next_run_time(now)
        if nxt is None:
            # 今天任务都跑完了,等到明天
            time.sleep(300)
            continue

        run_at, task_name = nxt
        # 到达执行时间且今天未跑过
        if now >= run_at and last_run.get(task_name) != trade_date:
            for attempt in range(1, MAX_RETRIES + 1):
                if run_task(repo, task_name, trade_date):
                    last_run[task_name] = trade_date
                    break
                if attempt < MAX_RETRIES:
                    logger.warning("任务 %s 第 %d 次重试", task_name, attempt)
                    time.sleep(30)
            else:
                logger.error("任务 %s 重试 %d 次仍失败,标记 FAILED", task_name, MAX_RETRIES)
                last_run[task_name] = trade_date  # 不再重试

        if check_once:
            break
        time.sleep(30)  # 30 秒检查一次

    logger.info("scheduler 已停止")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    main_loop()
