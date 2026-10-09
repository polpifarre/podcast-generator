# Personal Podcast Generator — Solution

## 1. What it does

A daily news podcast for one listener. In the app you choose your interests, the episode
length (5, 10 or 15 minutes), the tone (casual, analytical or news anchor) and the time of
day for the daily episode. At that time, or when you click **Generate now**, the backend
searches the news for each interest, writes a conversation between two hosts based on those
articles, voices it with ElevenLabs, and adds it to the Episodes page with its transcript,
sources, cost and how long it took to make. An internal dashboard at `/dashboard` shows
usage (mocked, as the assignment allows) next to real cost and time.

The sample episode is [`samples/sample.mp3`](samples/sample.mp3). To run the app:
`make install`, then `make dev` (details in the [README](README.md)).

I kept the scope to what the assignment asks for: a UI to set interests and customize the
podcast, news pulled from the web, a daily schedule, an engaging audio episode, and a usage
dashboard. What I left out on purpose is in §7.

## 2. Architecture

```
Browser ──> Vite dev server (:5173) ──> FastAPI (:8000) ──> SQLite (podcast.db)
  React: Settings, Episodes,               │   Profile, Episode
  /dashboard                               ├── APScheduler: daily, at the profile's time
                                           ├── /media: the MP3 files
                                           └── background task ─> pipeline (in a thread)
```

One episode, from "Generate now" or the daily timer:

```
POST /episodes/generate -> 202 + Episode(pending)      the page polls every 3 s
  pipeline, in a thread:                                status: working
    1. news     Bing News RSS per interest -> article text (trafilatura), else the RSS snippet
    2. script   one OpenAI call, structured output: title, turns {speaker, text}, sources used
    3. voices   ElevenLabs text-to-dialogue, about 1,900 characters per request, in order
    4. audio    join, loudness to -16 LUFS, MP3 128 kbps mono (pydub + ffmpeg)
  saved on the Episode: script, sources, audio, length, cost, seconds per stage
                                                        status: done | failed (+ readable error)
```

## 3. Key decisions and trade-offs

- **News: Bing News RSS per interest, plus trafilatura for the article text.** No keys, it's
  searchable by topic, and it gives the publisher's URL. News APIs mostly return snippets,
  scraping outlet homepages breaks easily, and Google News links hide the publisher (§4).
- **Script: one LLM call with structured output.** One call gave grounded scripts that sound
  good, so I didn't add a second step to pick stories first or to check facts. The grounding
  comes from the prompt and the numbered articles.
- **Format: two hosts talking instead of one narrator.** Much more engaging, for the same
  price, since ElevenLabs charges per character.
- **Voices: ElevenLabs text-to-dialogue, chunks voiced in order, up to 3 tries each.**
  Voicing both hosts in one request makes the turn-taking sound natural. I didn't add a
  fallback or parallel chunks: if an episode fails, the user clicks again.
- **Background work: `POST` returns 202, the work runs as a background task, and the page
  polls.** No timeouts or double clicks, and nothing extra to install, unlike a job queue
  (Celery + Redis). If the server restarts in the middle of an episode, that episode is lost;
  it's marked as failed at startup.
- **Pipeline code: plain functions run in a thread, not `async` everywhere.** Easy to read
  top to bottom, and the server stays responsive. Articles download one at a time, which is
  fine now that a slow site can't hold it up (§4).
- **Schedule: APScheduler inside the backend.** Nothing to set up, unlike cron, Celery beat
  or a cloud scheduler. The downside is that there's no episode if the backend isn't running
  at that time. It makes one episode per day and skips the day if one was already made.
- **Database: SQLite, with tables created at startup and `make reset-db`.** Nothing to set
  up, and the same SQLAlchemy code runs on Postgres with a different `DATABASE_URL`. Real
  user data would need migrations (Alembic).
- **Frontend: React with TanStack Query, a small hand-written `api.ts` and plain CSS.** It's a
  few API calls and two tabs, so I skipped a generated client, a router and a UI library.
  Each setting is saved as soon as it changes.
- **Dashboard: its own address (`/dashboard`), with mocked usage next to real cost and
  time.** It's for a different audience than the listener's app. In production it would be
  a separate internal tool with a staff login; with no login here, a separate app wouldn't
  protect anything.
- **Auth: none, one demo profile.** It's not part of the assignment, and every table could
  get a user id later.

## 4. Integration quirks

- **Google News RSS links don't lead to the article.** They're opaque IDs that redirect to a
  consent page from the EU, which I found by following a link from the feed. I switched to
  Bing News RSS, whose links include the publisher's URL.
- **Bing picks the market from the caller's IP.** From Spain it mixed Spanish results into
  English topics, so the app always sends `setmkt=en-US&setlang=en-US`.
- **Bing News RSS is unofficial**, since Microsoft retired the Bing Search APIs in 2025. I kept
  it as the only source anyway: if it breaks, only the news step changes.
- **Paywalls and bot blocking.** Usually 2 or 3 sites out of 5 refuse the download. The app
  fetches twice as many articles as it needs, prefers the ones with full text, and otherwise
  uses the RSS snippet, so a blocked article never fails the episode.
- **A site that never answers held an episode up for 2 minutes**, because trafilatura waits
  30 s and tries 3 times. One episode spent 4 of its 5 minutes on news: the seconds per stage
  stored on the episode showed it, and timing each site found the cause. I cut the download
  limit to 5 s: same articles, and news takes about 25 s.
- **The LLM's source list isn't always right.** Once it got a URL slightly wrong, so a story it
  told was missing from the sources, and in the sample it lists an article the episode
  doesn't use. It now returns article numbers instead of URLs, which fixed the first case;
  the second needs a check against the transcript (§7).
- **One LLM answer stops at about 2,100 words**, whatever length or number of stories I ask
  for, and also with "high" verbosity. 15-minute episodes came out at 12:47–13:35. I accepted
  it for now; the fix is in §7.
- **Text-to-dialogue accepts about 2,000 characters per request.** Turns are packed into
  chunks of up to 1,900 characters, and the prompt keeps each turn under 350.
- **The provided ElevenLabs key can't list voices or models**, so voice and model IDs come
  from `.env`.
- **ffmpeg's loudness filter changes the sample rate** to 48 kHz (`ffprobe` showed it), so the
  app sets it back to 44.1 kHz after the filter.
- **pydub doesn't work on Python 3.13+**, because it needs `audioop`, which was removed. The
  backend is pinned to Python 3.12.

## 5. What makes the episode engaging

I listened to every episode and wrote a few lines about it: the settings, the main problems
and where they happen, what works, three facts checked against the articles, and what to
change next. Then I changed one thing for the next episode.

The first episode sounded like an interview. One host asked and the other answered, in 36
short turns in under four minutes. The start was abrupt and the hosts kept repeating the
name of the outlet. I changed the prompt so that:

- the intro opens with the most surprising story, welcomes the listener and says what's
  coming;
- the hosts take turns leading the stories. The host leading explains the story in one or
  two longer turns, the other reacts or asks what a listener would ask, they go back and
  forth two to four times, and one of them says what to watch next before moving on;
- both hosts explain and ask, every turn answers the previous one, and some clichés are
  banned ("let's dive in", "game-changer");
- they only use the articles provided, name each outlet once, and never make up numbers,
  names or quotes;
- the text is written to be heard: short sentences, contractions, numbers written the way
  they're said, and acronyms spelled out the first time.

The next episode had 26 turns instead of 36, lasted 4:53 for a 5-minute target, and the
problems from my notes were gone. The three facts I checked matched the articles.

Getting the length right took some measuring. The first prompt wrote about 600 words
whatever the target was. The hosts speak at about 160 words per minute, and each story
comes out at 220–270 words no matter what I ask for, so the prompt sets the number of
stories for each length (3, 5 or 8): longer episodes cover more stories. 5-minute episodes
now come out at 5 to 6.5 minutes, 15-minute ones at about 13 (§4), and I haven't measured
10-minute ones.

For the voices I used ElevenLabs text-to-dialogue, which voices both hosts in the same
request, so the pauses and reactions between turns sound natural. I compared `eleven_v3`
and `eleven_v4` and kept v4 because it sounded better, at the same list price. Stability is
0.5 (balanced), and every episode is normalized to −16 LUFS, the usual podcast loudness.

The tone setting changes the writing. Casual uses everyday words and light jokes, analytical
explains why things happened and what the numbers mean, and news anchor uses polished
sentences and transitions like "Turning now to...". The voices and their settings are the
same for every tone, so you notice the difference more in the transcript than in the audio
(§7).

## 6. Real numbers

From 13 episodes made with the app on October 8 and 9, 2026. Costs use list prices: OpenAI
$0.75 / $4.50 per million input / output tokens, and ElevenLabs $0.08 per 1,000 characters.

| Length setting | Audio | Cost | Time to make | News | Script | Voices | Audio |
|---|---|---|---|---|---|---|---|
| 5 min (7 episodes) | 4:48–6:35 | $0.38–0.54 | 95–141 s | 8–34 s | 6–10 s | 56–102 s | 8–11 s |
| 15 min (2 episodes) | 12:47–13:35 | $1.04–1.08 | 250–263 s | 34–41 s | 15–20 s | 178–180 s | 21–23 s |

- ElevenLabs is 98% of the cost. The OpenAI call costs 1–2 cents per episode. Every episode
  comes out at about 16 characters per second of audio, which is about $0.08 per minute of
  audio.
- Voicing is most of the wait (60–75% of it). The chunks are voiced one after another; in
  parallel it would take about as long as the slowest chunk (§7).

Unit economics for a daily 5-minute episode (5.6 minutes of audio on average):

| ElevenLabs price per 1,000 characters | Per episode | Per listener per month |
|---|---:|---:|
| $0.08: Eleven v4 and v3 list price (used in the app) | ~$0.45 | ~$13.50 |
| $0.04: Eleven v4 Turbo and Flash list price | ~$0.23 | ~$7 |
| $0.022: Eleven v4 promotional price, until October 12 | ~$0.13 | ~$4 |

I chose `eleven_v4` for how it sounds. Its promotional price ends on October 12, so all the
numbers above use the list price. The cost of a minute that's actually listened to is
higher: with the dashboard's 73% completion rate, about $0.11. The biggest savings would
come from, in order: shorter episodes; a cheaper voice model, if it still sounds good;
voicing a story once for every listener who follows that topic (only the greetings and
transitions are personal); and caching, so a retry never pays twice.

### What I'd check every Monday

| Number | Warning sign | What I'd do |
|---|---|---|
| Completion rate by topic | One topic drops: people stop listening early | Read a few of that topic's transcripts: weak scripts, or thin news for that topic |
| Cost per audio minute | It goes up (it should stay flat) | Check whether scripts run longer than their target, or whether a provider changed its price (ElevenLabs is most of the cost) |
| Failed episodes, time to make one | A jump | A news site or an API is misbehaving; the seconds per stage on each episode show which step |
| Episodes per day vs listeners | Episodes fall while listeners don't | The daily schedule isn't running for some listeners |

## 7. Scope cuts and what I'd do next

- **Write long episodes in two parts** (two LLM calls), so 15 minutes means 15. One call
  works for 5-minute episodes; 15-minute ones are about 13.
- **Make the tone audible** with different voice settings, or voices, per tone. It needs
  more paid listening tests, and the sample uses one tone.
- **Voice the chunks in parallel and cache the audio.** Doing it in order is simpler,
  although voicing is the main wait.
- **A cloud scheduler or an always-on server**, since the timer inside the backend only runs
  while the backend runs.
- **User accounts, a staff login and a separate internal dashboard.** There's one demo user,
  and it's not part of the assignment.
- **Real listening analytics** (play, pause, position) for the completion rate. The
  assignment allows mock data, and there's one listener.
- **A private podcast feed (RSS)**, so episodes show up in Apple Podcasts or Spotify. The
  website covers the assignment.
- **More news sources, ranked by outlet quality.** One source covers any topic, but some
  results are sponsored or thin.
- **An automatic fact check** (a second LLM pass), which could also correct the source list.
  I checked every claim in the sample by hand: 15 of 16 match the articles, and one has a
  small slip in a job title ([`samples/sample_sources.md`](samples/sample_sources.md)).
- **Postgres and migrations.** SQLite needs no setup, and there's no real data to keep.
- **Save each episode's settings (length, tone) with it, and delete episodes.** Small, but
  not needed for one listener.

## 8. How I used AI tools

I used Claude Code, Anthropic's coding agent, inside VS Code for most of the coding. For each
part I described what I wanted, reviewed the plan it proposed, and had it write the code,
run the tests and the linters, and look things up in the providers' documentation, like
ElevenLabs' prices and the OpenAI SDK's options.

I made the product decisions and did the listening. The prompt changes came from my notes
on each episode, and I went through every file to understand what each part does and why.

The AI got things wrong a few times, and the numbers stored on each episode are what
caught it. After a quick 5-second test it said downloading the articles one by one was fast
enough, but a real episode spent 4 minutes on the news; timing each site found two that
never answered. It assumed the hosts speak 150 words per minute, and my episodes measured
160. And the 15-minute length took three attempts without fully getting there (§4).
