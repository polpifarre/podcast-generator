# Personal Podcast Generator

A daily news podcast made for one listener. You pick your interests, the length and the
tone; every day the app finds news on those topics, writes a two-host conversation grounded
in the articles, and voices it with ElevenLabs.

- **Listen first:** [`samples/sample.mp3`](samples/sample.mp3), with its
  [transcript](samples/sample_transcript.md) and [sources](samples/sample_sources.md).
- **How it works and why:** [`solution.md`](solution.md).

## What you need

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js 20.19+ (or 22.12+) and [pnpm](https://pnpm.io/)
- [ffmpeg](https://ffmpeg.org/) (used to join and level the audio)
- An OpenAI API key, an ElevenLabs API key, and two ElevenLabs voice IDs (one per host)

## Setup

```bash
git clone <this repo> podcast-generator
cd podcast-generator
cp .env.example .env     # then fill in the keys, the OpenAI model and the two voice IDs
make install             # backend and website packages
```

## Run

```bash
make dev
```

| Address | What it is |
|---|---|
| <http://localhost:5173> | The listener's app: **Settings** (interests, length, tone, daily time) and **Episodes** |
| <http://localhost:5173/dashboard> | The internal dashboard (for the team; usage is mock data) |
| <http://localhost:8000/docs> | The API, with a "Try it out" button on each endpoint |

To make an episode: set your interests in **Settings** and click **Generate now**. It takes
about 2 minutes for a 5-minute episode and costs about $0.45 (OpenAI + ElevenLabs). The
**Episodes** page refreshes until it's ready.

The daily episode is made at the time set in Settings, while `make dev` is running. `Ctrl+C`
stops both servers.

## Other commands

| Command | What it does |
|---|---|
| `make test` | API tests (the pipeline is replaced by a fake: no API calls, no cost) |
| `make lint` | Code checks for the backend (ruff) and the website (eslint, TypeScript) |
| `make episode` | Makes one episode from the command line with the current settings, and prints a summary |
| `make reset-db` | Deletes the database (`podcast.db`); it's recreated, empty, at the next start |

## Project layout

```
backend/app/
  main.py        the API, and startup (tables, demo profile, daily timer)
  pipeline.py    one episode: news -> script -> voices -> audio file
  scheduler.py   the daily episode
  metrics.py     numbers for the dashboard
  db.py          the SQLite database: Profile and Episode
  config.py      settings from .env
backend/tests/   API tests
frontend/src/
  pages/         Settings, Episodes, Dashboard
  api.ts         the calls to the backend
media/           generated audio (not in git)
samples/         the sample episode
```
