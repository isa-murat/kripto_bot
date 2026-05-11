"""APScheduler-driven periodic jobs.

Currently:
    - Daily report: every day at `daily_report_time` (TR), summarises broker
      state + last 24h P&L into a Telegram message.

Add new jobs by writing a coroutine and `scheduler.add_job(...)` in `start()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings
from src.engine.paper_broker import PaperBroker
from src.notify.telegram import TelegramNotifier, format_daily_report
from src.utils.logging import logger
from src.utils.time import TR_TZ


class JobScheduler:
    def __init__(
        self,
        broker: PaperBroker,
        notifier: TelegramNotifier | None,
    ):
        self._broker = broker
        self._notifier = notifier
        self._scheduler: AsyncIOScheduler | None = None

    def start(self) -> None:
        if self._scheduler is not None:
            return
        settings = get_settings()
        self._scheduler = AsyncIOScheduler(timezone=TR_TZ)

        hh, mm = (int(x) for x in settings.scheduler.daily_report_time.split(":"))
        self._scheduler.add_job(
            func=self._send_daily_report,
            trigger=CronTrigger(hour=hh, minute=mm, timezone=TR_TZ),
            id="daily_report",
            replace_existing=True,
            misfire_grace_time=600,   # 10 min — survives short outages
        )
        self._scheduler.start()
        logger.info(
            "Scheduler started | daily report at {}:{:02d} TR",
            hh, mm,
        )

    def stop(self) -> None:
        if self._scheduler is None:
            return
        self._scheduler.shutdown(wait=False)
        self._scheduler = None
        logger.info("Scheduler stopped")

    async def _send_daily_report(self) -> None:
        if self._notifier is None:
            logger.info("Daily report skipped (no notifier)")
            return
        try:
            since = datetime.now(tz=TR_TZ) - timedelta(hours=24)
            msg = format_daily_report(self._broker, since=since)
            await self._notifier.send(msg)
            logger.info("Daily report sent")
        except Exception as e:
            logger.exception("Daily report failed: {}", e)
