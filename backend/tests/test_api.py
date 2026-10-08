"""API tests. The pipeline is replaced by a fake, so nothing calls OpenAI or ElevenLabs."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import pipeline
from app.db import Episode, Session
from app.main import app
from app.scheduler import daily_episode, scheduler

# What a successful make_episode() returns.
FAKE_RESULT = {
    "title": "Test episode",
    "script": [{"speaker": "A", "text": "Hello."}, {"speaker": "B", "text": "Hi!"}],
    "sources": [{"title": "A story", "outlet": "Example News", "url": "https://x.com"}],
    "audio_path": "media/episode-1.mp3",
    "duration_sec": 3,
    "cost_usd": 0.01,
    "timings": {"news": 1.0, "script": 1.0, "voices": 1.0, "audio": 1.0},
}


@pytest.fixture(scope="module")
def client():
    # `with` runs the app's startup: tables and demo profile.
    with TestClient(app) as c:
        yield c


def test_demo_profile_exists(client):
    r = client.get("/profile")
    assert r.status_code == 200
    assert r.json()["interests"]


def test_update_profile(client):
    new = {
        "interests": ["space", "football"],
        "length_minutes": 10,
        "tone": "analytical",
        "schedule_time": "06:30",
    }
    assert client.put("/profile", json=new).json() == new
    assert client.get("/profile").json() == new


def test_update_profile_rejects_bad_values(client):
    bad = {
        "interests": ["space"],
        "length_minutes": 7,  # only 5, 10 or 15
        "tone": "casual",
        "schedule_time": "6:30pm",  # must be HH:MM
    }
    assert client.put("/profile", json=bad).status_code == 422


def test_generate_episode(client, monkeypatch):
    calls = []

    def fake_make_episode(interests, minutes, tone, audio_file):
        calls.append((interests, minutes, tone))
        return FAKE_RESULT

    monkeypatch.setattr(pipeline, "make_episode", fake_make_episode)
    profile = client.get("/profile").json()

    r = client.post("/episodes/generate")
    assert r.status_code == 202
    assert r.json()["status"] == "pending"

    # The test client waits for the background task, so the episode is finished now.
    episode = client.get(f"/episodes/{r.json()['id']}").json()
    assert episode["status"] == "done"
    assert episode["title"] == "Test episode"
    assert episode["cost_usd"] == 0.01
    assert calls == [(profile["interests"], profile["length_minutes"], profile["tone"])]
    assert client.get("/episodes").json()[0]["id"] == episode["id"]  # newest first


def test_failed_episode_shows_error(client, monkeypatch):
    def broken_make_episode(interests, minutes, tone, audio_file):
        raise RuntimeError("ElevenLabs error 401: invalid key")

    monkeypatch.setattr(pipeline, "make_episode", broken_make_episode)

    r = client.post("/episodes/generate")
    episode = client.get(f"/episodes/{r.json()['id']}").json()
    assert episode["status"] == "failed"
    assert "ElevenLabs error 401" in episode["error"]


def test_missing_episode(client):
    assert client.get("/episodes/999999").status_code == 404


def test_saving_the_time_moves_the_daily_episode(client):
    profile = client.get("/profile").json()
    client.put("/profile", json={**profile, "schedule_time": "06:45"})
    next_run = scheduler.get_job("daily").next_run_time
    assert (next_run.hour, next_run.minute) == (6, 45)


def test_daily_episode_once_a_day(client, monkeypatch):
    monkeypatch.setattr(pipeline, "make_episode", lambda *args: FAKE_RESULT)

    async def delete_all_episodes():
        async with Session() as db:
            await db.execute(delete(Episode))
            await db.commit()

    # client.portal runs these in the app's event loop, like the real timer would.
    client.portal.call(delete_all_episodes)
    client.portal.call(daily_episode)  # no episode today: makes one
    client.portal.call(daily_episode)  # one already today: skips
    assert [e["status"] for e in client.get("/episodes").json()] == ["done"]
