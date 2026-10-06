# Personal Podcast Generator — Solution

> Draft. Filled so far: one decision (3), integration quirks (4) and real numbers (6). The other
> sections are written as the phases are completed.

## 3. Decisions and trade-offs

### Schema management: `create_all`, not migrations (yet)

Tables are created on startup with SQLAlchemy's `create_all`, which creates missing tables but never
alters existing ones. After a schema change, `make reset-db` wipes the local SQLite file and the Postgres
volume, and the next start recreates everything.

- **Why:** during the build the schema changes in almost every phase, there's no data worth keeping, and
  reviewers always start from an empty database. Writing a migration for every change would add work for
  no benefit.
- **Trade-off:** `create_all` can't evolve a database that holds real data. A production deployment needs
  versioned migrations (Alembic), so schema changes keep existing user data. Moving over is cheap: one
  auto-generated initial migration from the finished models, plus `alembic upgrade head` before the app
  starts. That's an optional Phase 9 step.

## 4. Integration quirks

Real-world problems found while integrating external services, and how each is handled. This list
grows as new integrations are added.

| # | Quirk | How it was found | How it's handled |
|---|---|---|---|
| 1 | **Google News RSS links don't lead to the article.** Item links are opaque IDs (`news.google.com/rss/articles/CBMi…`) and, from the EU, redirect to `consent.google.com`. There's no publisher URL, so no article text | Following a feed link with curl during the spike: `302 → consent.google.com` | Switched per-topic search to **Bing News RSS**, whose links carry the publisher URL in a `url=` parameter, plus the outlet name and a snippet |
| 2 | **Bing picks the market from the caller's IP.** From Spain it returned `mkt=es-es` and mixed Spanish results into an English topic | Inspecting the first Bing feed | Always send `setmkt=en-US&setlang=en-US` (to become a per-profile language setting) |
| 3 | **Bing News RSS is unofficial.** Microsoft retired the official Bing Search APIs in August 2025, so this feed can change or be rate-limited without notice | Checking for an official API | Kept behind the `NewsProvider` interface, with outlet RSS and Hacker News as independent sources |
| 4 | **Search results include sponsored and low-value pages** (a sponsored USA Today story, a White House fact sheet, a law-firm newsletter) | Reading the spike's sources | Ranking by outlet quality and source diversity (Phase 3) |
| 5 | **Paywalls and bot blocking.** NYT and the Baltimore Sun returned HTTP errors to our fetcher | The spike's extract stage | Fetch 2× the articles needed and fall back to the RSS snippet; a blocked article never fails the episode |
| 6 | **The ElevenLabs key lacks the `voices_read` permission**, so voices and models can't be listed at runtime | Provided key's permissions | Voice and model IDs come only from config; the dialogue endpoint is probed with one real request and falls back to per-segment TTS if it fails |
| 7 | **The text-to-dialogue endpoint takes about 2,000 characters per request**, much less than an episode script | ElevenLabs API docs | Pack consecutive turns into chunks of ≤1,900 characters; prompt keeps turns under 350 characters |
| 8 | **The dialogue model only takes three stability values** (0.0, 0.5, 1.0) and silently rounds anything else | ElevenLabs model docs | A separate `ELEVENLABS_DIALOGUE_STABILITY` setting, so per-segment stability (e.g. 0.4) isn't silently changed |
| 9 | **ffmpeg's `loudnorm` filter changes the sample rate.** The first episode came out at 48 kHz instead of 44.1 kHz | `ffprobe` on the output | Set the output rate explicitly (`-ar 44100`) after the filter |
| 10 | **`pydub` breaks on Python 3.13+**: it depends on `audioop`, which 3.13 removed. It's also unmaintained since 2021 | Dependency research | Pinned to Python 3.12; fallback plan is calling ffmpeg directly |

## 5. What makes the episode engaging

_Listening notes from the Phase 1 spike: to be added._

## 6. Real numbers

### Phase 1 spike (2026-10-06)

One run of `make episode` (`backend/scripts/spike.py`): topic "artificial intelligence", target 3 minutes,
model `gpt-5.4-mini-2026-03-17`, ElevenLabs text-to-dialogue endpoint with the endpoint's default model.

| Stage | Time | Notes |
|---|---:|---|
| Fetch (Bing News RSS) | 0.5 s | 12 headlines |
| Extract (httpx + trafilatura) | 1.4 s | 7 tried concurrently, 5 full-text, 2 blocked (NYT, Baltimore Sun) |
| Write (1 LLM call) | 8.7 s | 4,183 input / 976 output tokens, 0 reasoning tokens |
| Synthesize (ElevenLabs dialogue) | 66.2 s | 3,578 chars in 2 sequential chunks (endpoint limit is 2,000 chars/request) |
| Stitch (pydub + ffmpeg loudnorm) | 6.3 s | |
| **Total** | **83.2 s** | |

| Cost item | Estimate |
|---|---:|
| LLM ($0.75 / 1M in, $4.50 / 1M out) | $0.0075 |
| TTS ($0.10 / 1k chars) | $0.358 |
| **Per episode** | **$0.365** |

Output: 238 s (4.0 min), 590 words, 15 turns, 4 sources cited.

What the numbers say:

- **TTS is basically the whole bill and most of the wait.** It's about 98% of the cost and 80% of the
  wall-clock time. The LLM step costs under a cent, so a second LLM stage (curate → write) adds almost
  nothing. Character count is the lever that matters, which is why `MAX_TTS_CHARS_PER_EPISODE` exists.
  See unit economics below.
- **Synthesis runs one chunk at a time.** The dialogue chunks are independent, so running them in parallel
  should cut synthesis time roughly by the number of chunks. The trade-off is less prosodic continuity at
  chunk boundaries.
- **Length is off by 31%** (590 words for a 450-word target). One prompt instruction isn't enough length
  control, which supports the plan's "reject outside ±20% and regenerate" rule.
- **Source quality needs ranking.** Two of the five articles were a White House fact sheet and a law-firm
  newsletter. Ranking for outlet quality and source diversity matters more than fetch speed, since fetch is
  already under 2 s.

### Unit economics

Measured rates from the spike: **about 15 TTS characters per second of audio**, so **about $0.09 per
minute of audio**, almost all of it TTS.

| Scenario | Per episode | Per user per month (daily episode) |
|---|---:|---:|
| 10 min, current TTS model | ~$0.92 | **~$28** |
| 5 min, current TTS model | ~$0.46 | ~$14 |
| 10 min, Flash/Turbo-class TTS (about half the price per character) | ~$0.47 | ~$14 |
| 5 min, Flash/Turbo-class TTS | ~$0.24 | ~$7 |

- **Cost per *listened* minute is higher than cost per generated minute.** If listeners finish 60% of an
  episode on average, each listened minute costs about $0.15. That's why completion rate belongs on the
  dashboard next to cost.
- **Levers, biggest first:**
  1. Episode length (cost scales linearly with characters).
  2. A cheaper TTS model, if it passes the voice tuning experiment.
  3. Synthesizing shared story segments once for every listener who follows the same topics. Only
     greetings and transitions are personal.
  4. Caching, so a retry never pays twice for the same audio.
- **Caveats:** one run (n = 1), list pay-as-you-go prices, and ElevenLabs subscription credits make the
  effective per-character price lower. Treat these numbers as orders of magnitude.
