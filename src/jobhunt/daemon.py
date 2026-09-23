"""The background loop.

Deliberately a loop with a sleep rather than a scheduler dependency. The job is
"run every N minutes unless it is the middle of the night", which does not need
cron semantics, and a dependency-free daemon is one fewer thing to install on a
laptop that also has to run a browser.

Quiet hours exist because a phone buzzing at 3am about a job that will still be
there at 9am is how someone ends up turning notifications off entirely.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

from .pipeline import Pipeline, RunReport

log = logging.getLogger(__name__)

#: Added to each sleep so runs do not land on the same minute every hour.
JITTER_FRACTION = 0.1


def in_quiet_hours(now: datetime, window: tuple[int, int]) -> bool:
    start, end = window
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Window crosses midnight, e.g. 22 to 07.
    return hour >= start or hour < end


class Daemon:
    def __init__(self, pipeline: Pipeline, interval_minutes: int, quiet_hours: tuple[int, int]):
        self.pipeline = pipeline
        self.interval_minutes = interval_minutes
        self.quiet_hours = quiet_hours
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self, dry_run: bool = False) -> None:
        log.info(
            "daemon started: every %d minutes, quiet %02d:00-%02d:00",
            self.interval_minutes,
            *self.quiet_hours,
        )

        while not self._stop.is_set():
            now = datetime.now()

            if in_quiet_hours(now, self.quiet_hours):
                log.info("quiet hours; skipping this cycle")
            else:
                try:
                    report: RunReport = await self.pipeline.run(dry_run=dry_run)
                    log.info("cycle: %s", report.summary())
                except Exception:  # noqa: BLE001
                    # A crashed cycle must not kill the daemon: the next one may
                    # well succeed, and an overnight loop that dies at 2am and is
                    # noticed at 9am has lost a day of postings.
                    log.exception("cycle failed; continuing")

            await self._sleep()

    async def _sleep(self) -> None:
        base = self.interval_minutes * 60
        delay = base * (1 + random.uniform(-JITTER_FRACTION, JITTER_FRACTION))
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except TimeoutError:
            pass
