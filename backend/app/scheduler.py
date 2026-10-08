"""The daily episode: a timer inside the backend that goes off at the profile's time.

It calls run_episode(), the same function as "Generate now". It only works while the
backend is running: if the computer is off at that time, there's no episode that day.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db import Episode, Session
from app.pipeline import run_episode

log = logging.getLogger("uvicorn.error")  # prints in the `make dev` terminal
scheduler = AsyncIOScheduler()  # uses the computer's time zone


async def daily_episode() -> None:
    """Start today's episode, unless one was already made (or is being made) today."""
    # Local time on purpose, like created_at (db.py).
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)  # noqa: DTZ005
    async with Session() as db:
        # A failed episode doesn't count, so a failed attempt doesn't block the day.
        made_today = await db.scalar(
            select(Episode).where(
                Episode.created_at >= midnight, Episode.status != "failed"
            )
        )
        if made_today is not None:
            log.info("Daily episode skipped: one was already made today")
            return
        episode = Episode()
        db.add(episode)
        await db.commit()

    log.info("Daily episode started: #%d", episode.id)
    await run_episode(episode.id)


def schedule_daily(time: str) -> None:
    """Run daily_episode every day at `time` ("HH:MM"), replacing any earlier time."""
    hour, minute = time.split(":")
    job = scheduler.add_job(
        daily_episode,
        "cron",
        hour=int(hour),
        minute=int(minute),
        id="daily",
        replace_existing=True,
    )
    log.info("Next daily episode: %s", f"{job.next_run_time:%Y-%m-%d %H:%M}")
