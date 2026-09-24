"""定时调度模块。

基于 APScheduler 的 CronTrigger 实现每日定时运行。云服务器部署时
也可以直接用系统 cron 调用 `python -m wechat_agent.main run`，二选一。
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable, Optional

from .config import ScheduleConfig

logger = logging.getLogger(__name__)


def run_daily(
    job: Callable[[], None],
    config: ScheduleConfig,
    block: bool = True,
) -> Optional[object]:
    """用 APScheduler 每日定时执行 job。

    :param job: 无参可调用对象（如 lambda: pipeline.run()）
    :param config: 调度配置
    :param block: True 则阻塞运行（守护进程模式）
    :return: scheduler 实例（block=False 时）
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("需要安装 APScheduler：pip install apscheduler")
        raise

    hour, _, minute = config.daily_time.partition(":")
    trigger = CronTrigger(hour=int(hour), minute=int(minute), timezone=config.timezone)

    if block:
        scheduler: object = BlockingScheduler(timezone=config.timezone)
    else:
        scheduler = BackgroundScheduler(timezone=config.timezone)

    scheduler.add_job(job, trigger, id="daily_publish", replace_existing=True)
    logger.info(
        "scheduler starting: daily %s (%s), block=%s",
        config.daily_time, config.timezone, block,
    )
    try:
        # BlockingScheduler.start() 本身会阻塞；BackgroundScheduler 会立即返回。
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
    return scheduler


def next_run_time(config: ScheduleConfig) -> str:
    """计算下次运行时间（用于日志/通知）。"""
    try:
        from apscheduler.triggers.cron import CronTrigger

        hour, _, minute = config.daily_time.partition(":")
        trigger = CronTrigger(hour=int(hour), minute=int(minute), timezone=config.timezone)
        nxt = trigger.get_next_fire_time(None, datetime.now(ZoneInfo(config.timezone)))
        return nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "unknown"
    except Exception:  # noqa: BLE001
        return f"{config.daily_time} ({config.timezone})"


