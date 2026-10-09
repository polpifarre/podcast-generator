"""Numbers for the internal dashboard.

Usage across many listeners is made up (mock): with one user there's nothing real to
show. Cost and time come from the episodes actually made with this app.
"""

import random
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Episode

TOPICS = ["AI", "Healthcare", "Business", "Science", "Politics", "Sports"]
AVG_EPISODE_MINUTES = 7.5  # a mix of 5-, 10- and 15-minute episodes


def mock_usage() -> dict:
    """Made-up usage for a product with about 1,300 listeners.

    A fixed seed makes the numbers the same on every load (only the dates move).
    """
    rng = random.Random(42)
    users = 1284
    today = date.today()  # noqa: DTZ011 (local date, like the rest of the app)

    per_day = []
    for i in range(30):
        day = today - timedelta(days=29 - i)
        # Most listeners get an episode each day; slow growth, quieter weekends.
        share = (0.78 + 0.004 * i) * (0.85 if day.weekday() >= 5 else 1)
        episodes = round(users * share * rng.uniform(0.95, 1.05))
        per_day.append({"date": day.isoformat(), "episodes": episodes})

    by_topic = [{"topic": t, "rate": round(rng.uniform(0.55, 0.82), 2)} for t in TOPICS]
    by_topic.sort(key=lambda t: t["rate"], reverse=True)

    episodes = sum(d["episodes"] for d in per_day)
    completion = round(sum(t["rate"] for t in by_topic) / len(by_topic), 2)
    return {
        "users": users,
        "episodes": episodes,  # last 30 days
        "completion_rate": completion,  # share of each episode listened to
        "minutes_listened": round(episodes * AVG_EPISODE_MINUTES * completion),
        "episodes_per_day": per_day,
        "completion_by_topic": by_topic,
    }


async def real_numbers(db: AsyncSession) -> dict:
    """Cost and time of the episodes made with this app (from the database)."""
    episodes = (await db.scalars(select(Episode))).all()
    done = [e for e in episodes if e.status == "done"]
    failed = sum(e.status == "failed" for e in episodes)
    if not done:
        return {
            "episodes_done": 0,
            "episodes_failed": failed,
            "avg_cost_usd": None,
            "avg_generation_sec": None,
            "cost_per_audio_minute_usd": None,
        }

    cost = sum(e.cost_usd for e in done)
    audio_minutes = sum(e.duration_sec for e in done) / 60
    generation_sec = sum(sum(e.timings.values()) for e in done)  # all stages
    return {
        "episodes_done": len(done),
        "episodes_failed": failed,
        "avg_cost_usd": round(cost / len(done), 3),
        "avg_generation_sec": round(generation_sec / len(done)),
        "cost_per_audio_minute_usd": round(cost / audio_minutes, 3),
    }
