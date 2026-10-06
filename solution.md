# Personal Podcast Generator — Solution

> Draft. Only the numbers section is filled in so far.

## 5. Real numbers

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
  nothing. A 10-minute episode would cost roughly $1.20 in TTS. Character count is the lever that matters,
  which is why `MAX_TTS_CHARS_PER_EPISODE` exists.
- **Synthesis runs one chunk at a time.** The dialogue chunks are independent, so running them in parallel
  should cut synthesis time roughly by the number of chunks. The trade-off is less prosodic continuity at
  chunk boundaries.
- **Length is off by 31%** (590 words for a 450-word target). One prompt instruction isn't enough length
  control, which supports the plan's "reject outside ±20% and regenerate" rule.
- **Source quality needs ranking.** Two of the five articles were a White House fact sheet and a law-firm
  newsletter. Ranking for outlet quality and source diversity matters more than fetch speed, since fetch is
  already under 2 s.

_Listening notes: to be added._
