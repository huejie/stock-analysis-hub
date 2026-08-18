"""Trading scheduler process lifecycle and polling loop."""

import logging
import signal
import time

from ...config import settings
from ..migrations import run_migrations
from ..providers.factory import get_provider
from ..repository import TradingRepository
from ..services.data_job_service import DataJobService
from ..services.market_data_service import MarketDataService
from .runner import SchedulerRunner


logger = logging.getLogger("trading.scheduler")
_loop_running = True


def _signal_handler(signum, frame):
    global _loop_running
    _loop_running = False
    logger.info("scheduler 收到停止信号，优雅退出")


def build_runner() -> SchedulerRunner | None:
    if not getattr(settings, "trading_enabled", True):
        return None
    run_migrations(settings.db_path)
    repo = TradingRepository(settings.db_path)
    provider = get_provider(settings)
    market_data_service = MarketDataService(
        repo,
        provider,
        max_missing_ratio=getattr(
            settings, "trading_data_max_missing_ratio", 0.05
        ),
    )
    data_job_service = DataJobService(
        repo,
        market_data_service,
        settings,
    )
    return SchedulerRunner(
        repo,
        data_job_service,
        provider,
        settings,
    )


def main_loop(check_once: bool = False):
    if not getattr(settings, "trading_enabled", True):
        logger.info("scheduler 未启用，退出")
        return
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    runner = build_runner()
    if runner is None:
        return
    logger.info("scheduler 启动 (db=%s)", settings.db_path)

    while _loop_running:
        summary = runner.tick()
        logger.info("scheduler tick: %s", summary)
        if check_once:
            break
        time.sleep(
            getattr(settings, "trading_scheduler_poll_seconds", 30)
        )

    logger.info("scheduler 已停止")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    main_loop()
