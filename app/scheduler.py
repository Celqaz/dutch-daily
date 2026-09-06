"""Daily scheduler: sleep until the configured delivery time, then run the job."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config

log = logging.getLogger("dutchdaily.scheduler")


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_s, _, minute_s = value.partition(":")
    return int(hour_s), int(minute_s or "0")


def seconds_until_next(delivery_time: str, tz_name: str) -> float:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    hour, minute = _parse_hhmm(delivery_time)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_daily(job, cfg: Config) -> None:
    """Run ``job()`` once per day at cfg.delivery_time in cfg.timezone, forever."""
    while True:
        wait = seconds_until_next(cfg.delivery_time, cfg.timezone)
        log.info(
            "Next Dutch lesson scheduled for %s %s (in %.0f s).",
            cfg.delivery_time,
            cfg.timezone,
            wait,
        )
        time.sleep(wait)
        try:
            job()
        except Exception:  # noqa: BLE001 - keep the scheduler alive
            log.exception("Daily job failed; will retry tomorrow.")
