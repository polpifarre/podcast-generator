"""The database: one SQLite file with two tables, Profile and Episode."""

from datetime import datetime

from sqlalchemy import JSON, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import REPO_ROOT, settings

# Switching to Postgres later = a different DATABASE_URL (plus the asyncpg driver).
url = settings.database_url or f"sqlite+aiosqlite:///{REPO_ROOT / 'podcast.db'}"
engine = create_async_engine(url)
# Session() opens a conversation with the database. expire_on_commit=False keeps an
# object's values readable after it's saved.
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """What the listener wants. There's only one (no login)."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    interests: Mapped[list[str]] = mapped_column(JSON)
    length_minutes: Mapped[int]  # 5, 10 or 15
    tone: Mapped[str]  # casual, analytical or anchor
    schedule_time: Mapped[str]  # "HH:MM", when the daily episode is made


class Episode(Base):
    """One generated episode. Empty fields fill in as the pipeline runs."""

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Local time: the app runs on one machine, and SQLite doesn't store time zones.
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    status: Mapped[str] = mapped_column(
        default="pending"
    )  # pending/working/done/failed
    title: Mapped[str | None]
    script: Mapped[list | None] = mapped_column(JSON)  # [{speaker, text}, ...]
    sources: Mapped[list | None] = mapped_column(JSON)  # [{title, outlet, url}, ...]
    audio_path: Mapped[str | None]  # e.g. "media/episode-3.mp3"
    duration_sec: Mapped[float | None]
    cost_usd: Mapped[float | None]
    timings: Mapped[dict | None] = mapped_column(JSON)  # seconds per stage
    error: Mapped[str | None]


async def init_db() -> None:
    """Create the tables and the demo profile if they don't exist yet."""
    async with engine.begin() as conn:
        # Only creates missing tables; after changing a table, run `make reset-db`.
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        if await db.scalar(select(Profile)) is None:
            db.add(
                Profile(
                    interests=["artificial intelligence"],
                    length_minutes=5,
                    tone="casual",
                    schedule_time="07:00",
                )
            )
        # A restart (e.g. `make dev` reloading after a code change) stops any episode
        # being generated. Mark those as failed so they don't show "working" forever.
        await db.execute(
            update(Episode)
            .where(Episode.status.in_(["pending", "working"]))
            .values(status="failed", error="Interrupted: the server restarted")
        )
        await db.commit()
