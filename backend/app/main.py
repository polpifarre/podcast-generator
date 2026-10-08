"""The API: what each URL does. Try it at http://localhost:8000/docs."""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MEDIA_DIR
from app.db import Episode, Profile, Session, init_db
from app.pipeline import run_episode
from app.scheduler import schedule_daily, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs when the server starts (before `yield`) and when it stops (after)."""
    await init_db()  # tables and demo profile
    scheduler.start()  # the daily episode timer
    async with Session() as db:
        profile = await db.scalar(select(Profile))
    schedule_daily(profile.schedule_time)
    yield
    scheduler.shutdown()


app = FastAPI(title="Personal Podcast Generator", lifespan=lifespan)
# Audio files: media/episode-3.mp3 is served at /media/episode-3.mp3.
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


async def get_db():
    """Gives each request its own database session, closed when the request ends."""
    async with Session() as db:
        yield db


DB = Annotated[AsyncSession, Depends(get_db)]


# --- The shape of the data sent and received. Pydantic checks it and rejects bad
# input with a 422 error that says what's wrong. -------------------------------------


class ProfileData(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # can be built from a database row

    interests: list[str] = Field(min_length=1)
    length_minutes: Literal[5, 10, 15]
    tone: Literal["casual", "analytical", "anchor"]
    schedule_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # "HH:MM"


class EpisodeData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    status: str  # pending, working, done or failed
    title: str | None
    script: list[dict] | None
    sources: list[dict] | None
    audio_path: str | None
    duration_sec: float | None
    cost_usd: float | None
    timings: dict | None
    error: str | None


# --- Profile --------------------------------------------------------------------------


@app.get("/profile", response_model=ProfileData)
async def get_profile(db: DB):
    return await db.scalar(select(Profile))


@app.put("/profile", response_model=ProfileData)
async def update_profile(data: ProfileData, db: DB):
    profile = await db.scalar(select(Profile))
    for field, value in data.model_dump().items():
        setattr(profile, field, value)
    await db.commit()
    schedule_daily(profile.schedule_time)  # move the timer if the time changed
    return profile


# --- Episodes -------------------------------------------------------------------------


@app.get("/episodes", response_model=list[EpisodeData])
async def list_episodes(db: DB):
    """All episodes, newest first."""
    return (await db.scalars(select(Episode).order_by(Episode.id.desc()))).all()


@app.get("/episodes/{episode_id}", response_model=EpisodeData)
async def get_episode(episode_id: int, db: DB):
    episode = await db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


@app.post("/episodes/generate", response_model=EpisodeData, status_code=202)
async def generate_episode(background: BackgroundTasks, db: DB):
    """Start a new episode and answer at once (202 = accepted, still working).

    Generating takes a minute or two, longer than a request should wait, so it runs in
    the background. Check its status with GET /episodes/{id}.
    """
    episode = Episode()
    db.add(episode)
    await db.commit()
    background.add_task(run_episode, episode.id)
    return episode


# --- Dashboard ------------------------------------------------------------------------


@app.get("/metrics")
async def get_metrics():
    """Usage numbers for the dashboard. To do in Phase 5."""
    return {}
