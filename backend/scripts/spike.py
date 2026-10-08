"""Day-1 throwaway spike: one topic -> news -> one LLM call -> ElevenLabs -> media/spike.mp3.

Purpose: prove that every risky external piece works end to end (news source, article
extraction, LLM script writing, text-to-speech, audio encoding) before building the real
app around them. It prints per-stage timings, tokens, TTS characters, duration and an
estimated cost, so the numbers can go straight into solution.md.

Standalone on purpose: it imports nothing from `app/`, so it keeps working while the real
backend is being built.

Usage: uv run python scripts/spike.py ["topic"] [--minutes N]   (or: make episode)

How the file is organized (top to bottom, same order as an episode is made):
    Configuration    constants + Settings (read from .env)
    Data shapes      Segment / Script (the LLM's output) and Article (an extracted page)
    Timing helper    `stage`, which times each step for the summary
    1. FETCH         Bing News RSS -> list of headlines with publisher URLs
    2. EXTRACT       download each page and pull out the article text
    3. WRITE         one OpenAI call turns the articles into a two-host script
    4. SYNTHESIZE    ElevenLabs voices the script, a few turns per request
    5. STITCH        join the clips, normalize loudness, export the MP3
    main()           runs the stages in order and prints the summary
"""

# Standard library
import argparse  # reads the command-line arguments (topic, --minutes)
import asyncio  # runs many network calls at the same time (see "async" notes below)
import io  # lets pydub read MP3 bytes from memory as if they were a file
import json  # writes media/spike.json
import time  # high-resolution timer for stage timings
from pathlib import Path  # file paths that work on any OS
from statistics import mean, pstdev  # average and spread, for turn_stats
from typing import Literal  # restricts a field to fixed values ("A" or "B")
from urllib.parse import parse_qs, quote_plus, urlparse  # build and pick apart URLs

# Third-party packages (all listed in backend/pyproject.toml)
import feedparser  # parses RSS feeds
import httpx  # async HTTP client for every call we make ourselves
import trafilatura  # extracts the main article text from a web page's HTML
from openai import AsyncOpenAI  # official OpenAI SDK, async version
from pydantic import BaseModel, SecretStr  # typed, validated data classes
from pydantic_settings import BaseSettings, SettingsConfigDict  # config from .env
from pydub import AudioSegment  # audio editing; uses the ffmpeg program underneath
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# =============================================================================
# Configuration
# =============================================================================

# This file lives at <repo>/backend/scripts/spike.py, so two levels up is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIA_DIR = REPO_ROOT / "media"  # output folder (gitignored)

# Identifies our downloader to news sites honestly instead of pretending to be a browser.
USER_AGENT = "PodcastGeneratorSpike/0.1 (+https://github.com/)"

ELEVEN_BASE = "https://api.elevenlabs.io/v1"

# The text-to-dialogue endpoint accepts about 2,000 characters per request. 1,900 leaves a
# safety margin. Longer scripts are split into several "chunks" (see chunk_for_dialogue).
DIALOGUE_MAX_CHARS = 1900  # endpoint limit is 2,000 characters per request

MAX_ARTICLES = 5  # how many articles the LLM gets
# Caps LLM input cost; news articles put the key facts at the top anyway.
MAX_CHARS_PER_ARTICLE = 4000

# Silence between two audio chunks (ms). Turns inside a chunk already have natural
# gaps, so this only needs to be short.
CHUNK_GAP_MS = 300

# Host names used in the prompt. "A" and "B" are the speaker labels in the script and map
# to ELEVENLABS_VOICE_A / ELEVENLABS_VOICE_B.
HOSTS = {"A": "Alex", "B": "Sam"}


class Settings(BaseSettings):
    """All configuration, read from the repo-root .env file (or environment variables).

    Each field maps to an upper-case variable: `openai_model` <- OPENAI_MODEL.
    Fields without a default are required: the script stops at startup if one is missing.
    Fields with a default are optional: the default applies when the variable isn't set.
    """

    # Where to read from; extra="ignore" means unknown variables in .env (e.g.
    # DATABASE_URL, used later by the app) don't cause an error here.
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # --- Required ---
    # SecretStr hides the value: printing it shows '**********'. The real key is only
    # taken out with .get_secret_value() at the exact place it's sent to the API.
    openai_api_key: SecretStr
    openai_model: str  # e.g. gpt-5.4-mini-2026-03-17; never hardcoded in the code
    elevenlabs_api_key: SecretStr
    elevenlabs_voice_a: str  # voice ID for host A (Alex, the anchor)
    elevenlabs_voice_b: str  # voice ID for host B (Sam, the explainer)

    # --- Optional: how the voices sound ---
    # Which ElevenLabs model voices the dialogue. Unset -> model_id isn't sent and
    # ElevenLabs uses its default (eleven_v3). Try "eleven_v4" in the voice experiment.
    elevenlabs_dialogue_model: str | None = None
    # Stability: 0.0 = most expressive, 0.5 = balanced, 1.0 = most consistent but flat.
    # The dialogue model only accepts discrete stability values (0.0 / 0.5 / 1.0).
    elevenlabs_dialogue_stability: float = 0.5
    # How closely the output sticks to the original voice (0-1).
    elevenlabs_similarity: float = 0.75

    # --- Optional: prices, only used for the cost estimate printed at the end (USD) ---
    openai_price_input_per_1m: float = 0.75
    openai_price_output_per_1m: float = 4.50
    elevenlabs_price_per_1k_chars: float = 0.08  # v3 / Multilingual v2 list price


# =============================================================================
# Data shapes
# =============================================================================
# Pydantic models: classes whose fields are type-checked when an object is created.
# Segment and Script also define the JSON schema the LLM must follow (see write_script),
# so the model can't return free-form text that breaks the TTS step.


class Segment(BaseModel):
    """One turn of the conversation: who speaks and what they say."""

    speaker: Literal["A", "B"]  # only "A" or "B" is valid
    text: str


class Script(BaseModel):
    """The LLM's full output for one episode."""

    title: str
    segments: list[Segment]  # the turns, in order
    sources_used: list[str]  # URLs the LLM says it used, so grounding can be checked


class Article(BaseModel):
    """One news article after extraction (stage 2)."""

    title: str
    source: str  # outlet name, e.g. "CBS News"
    url: str  # the publisher's URL
    text: str  # full article text, or the RSS snippet if extraction failed
    extracted: bool  # True = full text, False = only the snippet


# =============================================================================
# Timing helper
# =============================================================================

# Seconds per stage, filled in by `stage` and printed in the summary.
timings: dict[str, float] = {}


class stage:
    """Times a block of code. Usage:

        with stage("fetch"):
            ...work...

    `with` calls __enter__ before the block (print a header, start the clock) and
    __exit__ after it (stop the clock, store and print the time). In the real app,
    these timings become the per-stage fields on the Episode row.
    """

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        print(f"\n== {self.name} ==")
        self.t0 = time.perf_counter()

    def __exit__(self, *exc):
        timings[self.name] = time.perf_counter() - self.t0
        print(f"   ({timings[self.name]:.1f}s)")


# A note on async: functions marked `async def` can pause at each `await` while they
# wait for the network, letting other work run in the meantime. That's what lets the
# spike download 10 articles at once instead of one after another. An async function
# only runs when it's awaited (or passed to asyncio.gather / asyncio.run).


# --- 1. FETCH ---------------------------------------------------------------
# Google News RSS links are opaque and redirect to a consent wall from the EU, so
# the spike uses Bing News RSS, whose links carry the publisher URL in `url=`.


async def fetch_headlines(client: httpx.AsyncClient, topic: str) -> list[dict]:
    """Search Bing News for `topic` and return headlines with their publisher URLs."""
    # quote_plus makes the topic URL-safe ("climate change" -> "climate+change").
    # setmkt/setlang force US English; otherwise Bing picks the market from our IP
    # address and mixes in other languages.
    url = (
        f"https://www.bing.com/news/search?q={quote_plus(topic)}"
        "&format=rss&setmkt=en-US&setlang=en-US"
    )
    resp = await client.get(url)
    resp.raise_for_status()  # turn HTTP errors (4xx/5xx) into exceptions
    feed = feedparser.parse(resp.text)

    items, seen = [], set()  # `seen` drops duplicate URLs
    for e in feed.entries:
        # Bing's link is a redirect like bing.com/news/apiclick.aspx?...&url=<publisher>.
        # Pull out the real publisher URL; fall back to the link itself if it's missing.
        target = parse_qs(urlparse(e.link).query).get("url", [e.link])[0]
        if target in seen:
            continue
        seen.add(target)
        items.append(
            {
                "title": e.title,
                "url": target,
                # Bing's <News:Source> tag holds the outlet name; else use the domain.
                "source": e.get("news_source") or urlparse(target).netloc,
                # Short summary from the feed; used if the full article can't be fetched.
                "snippet": e.get("summary", ""),
            }
        )
    return items


# --- 2. EXTRACT -------------------------------------------------------------


async def extract(client: httpx.AsyncClient, item: dict) -> Article:
    """Download one article and extract its text; fall back to the RSS snippet.

    Never raises for a bad page: a paywall or a timeout costs us one article, not the
    whole episode.
    """
    text = None
    try:
        # 5 s timeout so one slow site can't hold up the episode; follow redirects
        # because many news links bounce through a tracking URL first.
        resp = await client.get(item["url"], timeout=5, follow_redirects=True)
        resp.raise_for_status()
        # trafilatura is synchronous (it would block every other download while it
        # parses), so it runs in a separate thread via asyncio.to_thread.
        text = await asyncio.to_thread(trafilatura.extract, resp.text)
    except httpx.HTTPError as e:
        # Paywalls, bot blocking (403), timeouts... note it and use the snippet instead.
        print(f"   ! {item['source']}: {type(e).__name__}, using snippet")
    return Article(
        title=item["title"],
        source=item["source"],
        url=item["url"],
        # `text or snippet`: trafilatura returns None when it finds no article body.
        text=(text or item["snippet"])[:MAX_CHARS_PER_ARTICLE],
        extracted=bool(text),
    )


# --- 3. WRITE ---------------------------------------------------------------

# The instructions for the LLM. {a}, {b} and {words} are filled in by write_script with
# .format(). Sections: who the hosts are, grounding (no invented facts), the episode
# structure, conversation rules (so it isn't two people reading paragraphs), writing
# for the ear, banned clichés, and the target length.
SYSTEM_PROMPT = """You write scripts for a daily two-host news podcast. The hosts have a real
conversation; they never take turns reading paragraphs.
Hosts: {a} (speaker "A", the anchor: states the facts, drives the show, hands off between
stories) and {b} (speaker "B", the explainer: asks the listener's question, adds context,
pushes back).

Grounding:
- Use ONLY the articles provided. Attribute facts to the outlet by name ("according to Reuters").
  Never invent numbers, names, or quotes. If an article is thin, say less about it.
- Reactions and opinions never add facts. Frame opinions as questions or perspectives.

Structure:
- Cold open: hook with the most surprising story and greet the listener by naming today's topic.
- 3-4 stories. For each: {a} states the facts in one or two short turns; {b} asks the question
  a listener would ask, or reacts; 3-6 turns of back-and-forth on why it matters; one host says
  what to watch; then a natural handoff to the next story.
- Outro: a short sign-off.

Conversation:
- Every turn responds to the previous one: answer it, question it, push back, or build on it.
  Never two independent monologues in a row.
- Vary turn length: most turns 5-25 words, explanatory turns up to about 50 words, never more
  than 350 characters. Never make all turns the same length.
- At least one real question per story, and occasional respectful disagreement.
- Reactions are short and specific to what was just said, never generic.

Write for the ear: short sentences, contractions. Spell numbers the way they're said. Expand
acronyms on first use.

Never use: "in today's fast-paced world", "let's dive in", "it's worth noting",
"without further ado", "game-changer", "buckle up", "wow", "that's crazy", "great point".

Target length: about {words} words total.
sources_used: the URLs of the articles you actually used."""


def turn_stats(segs: list[Segment]) -> str:
    """Summarize how conversational a script is: turn lengths and questions."""
    # Paragraph-style scripts show up as long, similar-length turns (low spread) with
    # few questions; conversations have short, varied turns. Printed after every run.
    words = [len(seg.text.split()) for seg in segs]
    questions = sum("?" in seg.text for seg in segs)  # True counts as 1 in sum()
    return (
        f"words/turn avg {mean(words):.0f}, spread {pstdev(words):.0f} "
        f"(min {min(words)}, max {max(words)}); questions in {questions}/{len(segs)} turns"
    )


async def write_script(
    settings: Settings, topic: str, articles: list[Article], words: int
):
    """Ask the LLM for a two-host script built only from `articles`.

    Returns (Script, input tokens, output tokens); the token counts feed the cost
    estimate.
    """
    # timeout=180: a long script can take a while. The SDK also retries connection
    # errors, rate limits (429) and server errors (5xx) twice on its own.
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=180
    )
    # Number and label every article so the model can attribute facts to the outlet
    # and report the URLs it used.
    corpus = "\n\n".join(
        f"[{i}] {a.title}\nOutlet: {a.source}\nURL: {a.url}\n{a.text}"
        for i, a in enumerate(articles, 1)
    )
    t0 = time.perf_counter()
    # responses.parse = structured output: text_format=Script turns our Pydantic model
    # into a strict JSON schema, the model must answer in that shape, and the SDK
    # validates the result into a real Script object (resp.output_parsed).
    resp = await client.responses.parse(
        model=settings.openai_model,
        input=[
            # "system" = the standing instructions; "user" = this episode's material.
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    a=HOSTS["A"], b=HOSTS["B"], words=words
                ),
            },
            {
                "role": "user",
                "content": f"Today's topic: {topic}\n\nArticles:\n\n{corpus}",
            },
        ],
        text_format=Script,
    )
    latency = time.perf_counter() - t0
    u = resp.usage  # token counts reported by the API (what we're billed for)
    # Reasoning models can spend hidden "thinking" tokens; they're billed as output.
    reasoning = getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0
    print(
        f"   model={settings.openai_model} latency={latency:.1f}s "
        f"in={u.input_tokens} out={u.output_tokens} (reasoning={reasoning})"
    )
    return resp.output_parsed, u.input_tokens, u.output_tokens


# --- 4. SYNTHESIZE ----------------------------------------------------------
# ElevenLabs' text-to-dialogue endpoint voices several turns with both hosts in one
# request, so timing and reactions sound like a real conversation. It accepts about
# 2,000 characters per request, so the script is split into chunks that are voiced one
# after another. Temporary errors are retried; anything else fails the episode with a
# clear message (in the app: shown on the episode, and you click "Generate now" again).


def _transient(e: BaseException) -> bool:
    """Is this error worth retrying? (Used by the @retry on tts_dialogue.)"""
    # 429 = rate limited, 5xx = server problem: both often succeed on a second try.
    # Other 4xx (bad request, missing permission) would just fail again, so no retry.
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code == 429 or e.response.status_code >= 500
    # Network-level failures (connection reset, timeout) are also worth retrying.
    return isinstance(e, httpx.TransportError)


def _error_detail(e: Exception) -> str:
    """Short, readable description of an HTTP error (never includes keys)."""
    # For HTTP errors, ElevenLabs puts the reason in the response body, e.g. a quota
    # or permission message.
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    return f"{type(e).__name__}: {e}"


# The @retry decorator wraps the function: if it raises an error that _transient()
# accepts, wait (1 s, 2 s, 4 s... capped at 8 s) and try again, up to 3 attempts in
# total. reraise=True: if all attempts fail, raise the original error.
@retry(
    retry=retry_if_exception(_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def tts_dialogue(
    client: httpx.AsyncClient, s: Settings, segs: list[Segment]
) -> bytes:
    """Voice several turns (one chunk) in a single ElevenLabs text-to-dialogue call.

    Returns the MP3 bytes.
    """
    voices = {"A": s.elevenlabs_voice_a, "B": s.elevenlabs_voice_b}
    body: dict = {
        # Each turn says which voice speaks it.
        "inputs": [{"text": seg.text, "voice_id": voices[seg.speaker]} for seg in segs],
        # The dialogue endpoint only takes these two voice settings.
        "settings": {
            "stability": s.elevenlabs_dialogue_stability,
            "similarity": s.elevenlabs_similarity,
        },
    }
    if s.elevenlabs_dialogue_model:  # only send model_id if one is configured
        body["model_id"] = s.elevenlabs_dialogue_model
    resp = await client.post(
        f"{ELEVEN_BASE}/text-to-dialogue",
        params={"output_format": "mp3_44100_128"},  # MP3, 44.1 kHz, 128 kbps
        json=body,
        timeout=120,  # a full chunk can take ~30 s or more to generate
    )
    resp.raise_for_status()
    return resp.content  # the audio file's raw bytes


def chunk_for_dialogue(segs: list[Segment]) -> list[list[Segment]]:
    """Group consecutive turns into chunks of at most DIALOGUE_MAX_CHARS characters.

    Greedy: keep adding turns to the current chunk until the next one wouldn't fit, then
    start a new chunk. Turns are never split, and their order is kept.
    """
    chunks, cur, size = [], [], 0
    for seg in segs:
        # `cur and ...`: never close an empty chunk. A single over-long turn still gets
        # its own chunk; the API would reject it, which is why the prompt caps turns
        # at 350 characters.
        if cur and size + len(seg.text) > DIALOGUE_MAX_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(seg)
        size += len(seg.text)
    if cur:
        chunks.append(cur)
    return chunks


async def synthesize(s: Settings, segs: list[Segment]) -> tuple[list[bytes], int]:
    """Voice the whole script. Returns (one MP3 clip per chunk, characters billed).

    Raises RuntimeError with ElevenLabs' reason if a chunk still fails after retries.
    """
    # ElevenLabs authenticates with this header on every request.
    headers = {"xi-api-key": s.elevenlabs_api_key.get_secret_value()}
    chunks = chunk_for_dialogue(segs)
    print(f"   {len(chunks)} chunk(s)")
    clips = []
    # One shared HTTP client (connection reuse) for all ElevenLabs calls.
    async with httpx.AsyncClient(headers=headers) as client:
        # One chunk after another (simplest; running them in parallel would be faster).
        for i, chunk in enumerate(chunks, 1):
            try:
                clips.append(await tts_dialogue(client, s, chunk))
            except httpx.HTTPError as e:
                raise RuntimeError(
                    f"ElevenLabs failed on chunk {i}/{len(chunks)}: {_error_detail(e)}"
                ) from e
    # ElevenLabs bills per character of text sent.
    chars = sum(len(seg.text) for seg in segs)
    return clips, chars


# --- 5. STITCH --------------------------------------------------------------


def stitch(clips: list[bytes], out: Path) -> float:
    """Join the clips into one MP3 at `out` and return its duration in seconds.

    Synchronous (pydub calls ffmpeg and blocks), so main() runs it in a thread.
    """
    gap = AudioSegment.silent(duration=CHUNK_GAP_MS)
    audio = AudioSegment.empty()
    for i, clip in enumerate(clips):
        if i:  # a gap before every clip except the first
            audio += gap
        # Decode the MP3 bytes (held in memory) into raw audio we can join.
        audio += AudioSegment.from_file(io.BytesIO(clip), format="mp3")
    # Mono, 44.1 kHz: the standard podcast format.
    audio = audio.set_channels(1).set_frame_rate(44100)
    audio.export(
        out,
        format="mp3",
        bitrate="128k",
        # Extra ffmpeg options:
        #   loudnorm: normalize perceived loudness to -16 LUFS (the usual podcast
        #     level), true peak -1.5 dB, loudness range 11, so every episode and both
        #     voices play at a consistent volume.
        #   -ar 44100: loudnorm changes the sample rate internally (to 48 kHz on
        #     output), so set it back explicitly.
        parameters=["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100"],
    )
    return len(audio) / 1000  # pydub measures length in milliseconds


# --- main -------------------------------------------------------------------


async def main(topic: str, minutes: float) -> None:
    """Run every stage for one episode, write the files and print the summary."""
    s = Settings()  # reads .env; fails here, early, if a required variable is missing
    MEDIA_DIR.mkdir(exist_ok=True)
    target_words = int(minutes * 150)  # ~150 spoken words per minute
    t_start = time.perf_counter()

    # One HTTP client for the news downloads, with our User-Agent and a 10 s default
    # timeout. `async with` closes its connections when the block ends.
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=10
    ) as client:
        with stage("fetch"):
            items = await fetch_headlines(client, topic)
            print(f"   {len(items)} headlines for {topic!r}")
        with stage("extract"):
            # Try twice as many articles as needed, all at once: some will be blocked
            # or paywalled, and we still want MAX_ARTICLES good ones.
            candidates = await asyncio.gather(
                *(extract(client, it) for it in items[: MAX_ARTICLES * 2])
            )
            # Full-text articles first (False sorts before True), keeping Bing's order
            # otherwise; then take the first MAX_ARTICLES.
            articles = sorted(candidates, key=lambda a: not a.extracted)[:MAX_ARTICLES]
            for a in articles:
                print(
                    f"   {'full' if a.extracted else 'snip'} {len(a.text):>5}c  {a.source}"
                )

    with stage("write"):
        script, tok_in, tok_out = await write_script(s, topic, articles, target_words)
        words = sum(len(seg.text.split()) for seg in script.segments)
        print(
            f"   {script.title!r}: {len(script.segments)} turns, {words}/{target_words} words"
        )
        print(f"   {turn_stats(script.segments)}")

    with stage("synthesize"):
        clips, tts_chars = await synthesize(s, script.segments)
        print(f"   {tts_chars} characters voiced")

    with stage("stitch"):
        out = MEDIA_DIR / "spike.mp3"
        # stitch() blocks while ffmpeg runs, so run it in a thread.
        duration = await asyncio.to_thread(stitch, clips, out)
        print(f"   wrote {out.relative_to(REPO_ROOT)}")

    # Save the script and which articles were used, for listening notes and fact-checks.
    # Article text is left out to keep the file small.
    (MEDIA_DIR / "spike.json").write_text(
        json.dumps(
            {
                "topic": topic,
                "script": script.model_dump(),
                "articles": [a.model_dump(exclude={"text"}) for a in articles],
            },
            indent=2,
        )
    )

    # Cost estimate: OpenAI bills per token (prices are per million), ElevenLabs per
    # character (price per thousand).
    llm_cost = (
        tok_in * s.openai_price_input_per_1m + tok_out * s.openai_price_output_per_1m
    ) / 1e6
    tts_cost = tts_chars / 1000 * s.elevenlabs_price_per_1k_chars
    total = time.perf_counter() - t_start
    print("\n== summary ==")
    for name, secs in timings.items():
        print(f"   {name:<11} {secs:6.1f}s")
    print(f"   {'total':<11} {total:6.1f}s")
    print(f"   tokens      in={tok_in} out={tok_out}")
    print(f"   tts         {tts_chars} chars in {len(clips)} chunk(s)")
    print(f"   duration    {duration:.0f}s ({duration / 60:.1f} min), {words} words")
    print(
        f"   est. cost   ${llm_cost + tts_cost:.3f} (llm ${llm_cost:.4f} + tts ${tts_cost:.3f})"
    )


# Runs only when the file is executed directly (not when it's imported).
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # nargs="?": the topic is optional and defaults to "artificial intelligence".
    parser.add_argument("topic", nargs="?", default="artificial intelligence")
    parser.add_argument("--minutes", type=float, default=3)
    args = parser.parse_args()
    # asyncio.run starts the event loop that executes the async code until main ends.
    asyncio.run(main(args.topic, args.minutes))
