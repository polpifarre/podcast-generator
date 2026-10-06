"""Day-1 throwaway spike: one topic -> news -> one LLM call -> ElevenLabs -> media/spike.mp3.

Standalone on purpose (does not import `app`). Prints per-stage timings, tokens,
TTS characters, duration and an estimated cost.

Usage: uv run python scripts/spike.py ["topic"] [--minutes N]
"""

import argparse
import asyncio
import io
import json
import time
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, quote_plus, urlparse

import feedparser
import httpx
import trafilatura
from openai import AsyncOpenAI
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydub import AudioSegment
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIA_DIR = REPO_ROOT / "media"
USER_AGENT = "PodcastGeneratorSpike/0.1 (+https://github.com/)"
ELEVEN_BASE = "https://api.elevenlabs.io/v1"
DIALOGUE_MAX_CHARS = 1900  # endpoint limit is 2,000 characters per request
MAX_ARTICLES = 5
MAX_CHARS_PER_ARTICLE = 4000
HOSTS = {"A": "Alex", "B": "Sam"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    openai_api_key: SecretStr
    openai_model: str
    elevenlabs_api_key: SecretStr
    elevenlabs_voice_a: str
    elevenlabs_voice_b: str
    # Unset -> omit model_id and let ElevenLabs use its endpoint default.
    elevenlabs_model: str | None = None
    elevenlabs_dialogue_model: str | None = None
    elevenlabs_stability: float = 0.4
    elevenlabs_similarity: float = 0.75
    elevenlabs_style: float = 0.2
    elevenlabs_speed: float = 1.05
    # The dialogue model only accepts discrete stability values (0.0 / 0.5 / 1.0).
    elevenlabs_dialogue_stability: float = 0.5
    openai_price_input_per_1m: float = 0.75
    openai_price_output_per_1m: float = 4.50
    elevenlabs_price_per_1k_chars: float = 0.10


class Segment(BaseModel):
    speaker: Literal["A", "B"]
    text: str


class Script(BaseModel):
    title: str
    segments: list[Segment]
    sources_used: list[str]


class Article(BaseModel):
    title: str
    source: str
    url: str
    text: str
    extracted: bool


timings: dict[str, float] = {}


class stage:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        print(f"\n== {self.name} ==")
        self.t0 = time.perf_counter()

    def __exit__(self, *exc):
        timings[self.name] = time.perf_counter() - self.t0
        print(f"   ({timings[self.name]:.1f}s)")


# --- 1. FETCH ---------------------------------------------------------------
# Google News RSS links are opaque and redirect to a consent wall from the EU, so
# the spike uses Bing News RSS, whose links carry the publisher URL in `url=`.


async def fetch_headlines(client: httpx.AsyncClient, topic: str) -> list[dict]:
    url = (
        f"https://www.bing.com/news/search?q={quote_plus(topic)}"
        "&format=rss&setmkt=en-US&setlang=en-US"
    )
    resp = await client.get(url)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    items, seen = [], set()
    for e in feed.entries:
        target = parse_qs(urlparse(e.link).query).get("url", [e.link])[0]
        if target in seen:
            continue
        seen.add(target)
        items.append(
            {
                "title": e.title,
                "url": target,
                "source": e.get("news_source") or urlparse(target).netloc,
                "snippet": e.get("summary", ""),
            }
        )
    return items


# --- 2. EXTRACT -------------------------------------------------------------


async def extract(client: httpx.AsyncClient, item: dict) -> Article:
    text = None
    try:
        resp = await client.get(item["url"], timeout=5, follow_redirects=True)
        resp.raise_for_status()
        text = await asyncio.to_thread(trafilatura.extract, resp.text)
    except httpx.HTTPError as e:
        print(f"   ! {item['source']}: {type(e).__name__}, using snippet")
    return Article(
        title=item["title"],
        source=item["source"],
        url=item["url"],
        text=(text or item["snippet"])[:MAX_CHARS_PER_ARTICLE],
        extracted=bool(text),
    )


# --- 3. WRITE ---------------------------------------------------------------

SYSTEM_PROMPT = """You write scripts for a daily two-host news podcast.
Hosts: {a} (speaker "A", the anchor: drives the show, introduces stories) and
{b} (speaker "B", the explainer: adds context, asks the listener's question, pushes back).

Rules:
- Use ONLY the articles provided. Attribute facts to the outlet by name ("according to Reuters").
  Never invent numbers, names, or quotes. If an article is thin, say less about it.
- Structure: a cold open that hooks with the most surprising story and greets the listener
  by naming today's topic; then 3-4 stories, each covering what happened, why it matters,
  and what to watch, with a natural transition; then a short outro with a sign-off.
- Write for the ear: short sentences, contractions, real back-and-forth, occasional short
  reactions. Spell numbers the way they're said. Expand acronyms on first use.
- Keep each turn under 350 characters. Alternate speakers often.
- Never use: "in today's fast-paced world", "let's dive in", "it's worth noting",
  "without further ado", "game-changer", "buckle up".
- Target length: about {words} words total.
- sources_used: the URLs of the articles you actually used."""


async def write_script(
    settings: Settings, topic: str, articles: list[Article], words: int
):
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), timeout=180
    )
    corpus = "\n\n".join(
        f"[{i}] {a.title}\nOutlet: {a.source}\nURL: {a.url}\n{a.text}"
        for i, a in enumerate(articles, 1)
    )
    t0 = time.perf_counter()
    resp = await client.responses.parse(
        model=settings.openai_model,
        input=[
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
    u = resp.usage
    reasoning = getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0
    print(
        f"   model={settings.openai_model} latency={latency:.1f}s "
        f"in={u.input_tokens} out={u.output_tokens} (reasoning={reasoning})"
    )
    return resp.output_parsed, u.input_tokens, u.output_tokens


# --- 4. SYNTHESIZE ----------------------------------------------------------


def _transient(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code == 429 or e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


def _error_detail(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}: {e.response.text[:200]}"
    return f"{type(e).__name__}: {e}"


async def tts_dialogue(
    client: httpx.AsyncClient, s: Settings, segs: list[Segment]
) -> bytes:
    voices = {"A": s.elevenlabs_voice_a, "B": s.elevenlabs_voice_b}
    body: dict = {
        "inputs": [{"text": seg.text, "voice_id": voices[seg.speaker]} for seg in segs],
        "settings": {
            "stability": s.elevenlabs_dialogue_stability,
            "similarity": s.elevenlabs_similarity,
        },
    }
    if s.elevenlabs_dialogue_model:
        body["model_id"] = s.elevenlabs_dialogue_model
    resp = await client.post(
        f"{ELEVEN_BASE}/text-to-dialogue",
        params={"output_format": "mp3_44100_128"},
        json=body,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content


@retry(
    retry=retry_if_exception(_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def tts_segment(client: httpx.AsyncClient, s: Settings, seg: Segment) -> bytes:
    voice = s.elevenlabs_voice_a if seg.speaker == "A" else s.elevenlabs_voice_b
    body: dict = {
        "text": seg.text,
        "voice_settings": {
            "stability": s.elevenlabs_stability,
            "similarity_boost": s.elevenlabs_similarity,
            "style": s.elevenlabs_style,
            "speed": s.elevenlabs_speed,
            "use_speaker_boost": True,
        },
    }
    if s.elevenlabs_model:
        body["model_id"] = s.elevenlabs_model
    resp = await client.post(
        f"{ELEVEN_BASE}/text-to-speech/{voice}",
        params={"output_format": "mp3_44100_128"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def chunk_for_dialogue(segs: list[Segment]) -> list[list[Segment]]:
    chunks, cur, size = [], [], 0
    for seg in segs:
        if cur and size + len(seg.text) > DIALOGUE_MAX_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(seg)
        size += len(seg.text)
    if cur:
        chunks.append(cur)
    return chunks


async def synthesize(s: Settings, segs: list[Segment]) -> tuple[list[bytes], str, int]:
    """Return (audio clips, path used, tts chars billed). Each clip is one dialogue
    chunk or one segment. A failed segment is skipped, never the whole episode."""
    headers = {"xi-api-key": s.elevenlabs_api_key.get_secret_value()}
    chars = 0
    sem = asyncio.Semaphore(3)

    async def per_segment(seg: Segment) -> bytes | None:
        nonlocal chars
        async with sem:
            try:
                audio = await tts_segment(client, s, seg)
                chars += len(seg.text)
                return audio
            except httpx.HTTPError as e:
                print(f"   ! segment skipped ({_error_detail(e)})")
                return None

    async with httpx.AsyncClient(headers=headers) as client:
        chunks = chunk_for_dialogue(segs)
        try:  # Try the dialogue endpoint exactly once before committing to it.
            first = await tts_dialogue(client, s, chunks[0])
        except httpx.HTTPError as e:
            print(
                f"   dialogue endpoint failed -> per-segment TTS ({_error_detail(e)})"
            )
            clips = await asyncio.gather(*(per_segment(seg) for seg in segs))
            return [c for c in clips if c], "per-segment", chars

        print(f"   using dialogue endpoint ({len(chunks)} chunk(s))")
        chars += sum(len(seg.text) for seg in chunks[0])
        clips = [first]
        for chunk in chunks[1:]:
            try:
                clips.append(await tts_dialogue(client, s, chunk))
                chars += sum(len(seg.text) for seg in chunk)
            except httpx.HTTPError as e:
                print(
                    f"   ! dialogue chunk failed, per-segment for it ({_error_detail(e)})"
                )
                fallback = await asyncio.gather(*(per_segment(seg) for seg in chunk))
                clips.extend(c for c in fallback if c)
        return clips, "dialogue", chars


# --- 5. STITCH --------------------------------------------------------------


def stitch(clips: list[bytes], path: str, out: Path) -> float:
    # Dialogue chunks already contain natural turn-taking; per-segment clips need gaps.
    gap = AudioSegment.silent(duration=300 if path == "dialogue" else 500)
    audio = AudioSegment.empty()
    for i, clip in enumerate(clips):
        if i:
            audio += gap
        audio += AudioSegment.from_file(io.BytesIO(clip), format="mp3")
    audio = audio.set_channels(1).set_frame_rate(44100)
    audio.export(
        out,
        format="mp3",
        bitrate="128k",
        parameters=["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100"],
    )
    return len(audio) / 1000


# --- main -------------------------------------------------------------------


async def main(topic: str, minutes: float) -> None:
    s = Settings()
    MEDIA_DIR.mkdir(exist_ok=True)
    target_words = int(minutes * 150)
    t_start = time.perf_counter()

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=10
    ) as client:
        with stage("fetch"):
            items = await fetch_headlines(client, topic)
            print(f"   {len(items)} headlines for {topic!r}")
        with stage("extract"):
            candidates = await asyncio.gather(
                *(extract(client, it) for it in items[: MAX_ARTICLES * 2])
            )
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

    with stage("synthesize"):
        clips, tts_path, tts_chars = await synthesize(s, script.segments)
        print(f"   path={tts_path} chars={tts_chars} clips={len(clips)}")

    with stage("stitch"):
        out = MEDIA_DIR / "spike.mp3"
        duration = await asyncio.to_thread(stitch, clips, tts_path, out)
        print(f"   wrote {out.relative_to(REPO_ROOT)}")

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
    print(f"   tts         {tts_chars} chars via {tts_path}")
    print(f"   duration    {duration:.0f}s ({duration / 60:.1f} min), {words} words")
    print(
        f"   est. cost   ${llm_cost + tts_cost:.3f} (llm ${llm_cost:.4f} + tts ${tts_cost:.3f})"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="?", default="artificial intelligence")
    parser.add_argument("--minutes", type=float, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.topic, args.minutes))
