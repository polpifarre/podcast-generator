"""One episode: news -> script -> voices -> audio file.

Plain (non-async) code that reads top to bottom, like the spike did. make_episode() does
the work; run_episode() runs it in a background thread and saves the result on the
Episode row. "Generate now", the daily schedule and `make episode` all use run_episode().
"""

import asyncio
import io
import time
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, quote_plus, urlparse

import feedparser
import httpx
import trafilatura
from openai import OpenAI
from pydantic import BaseModel
from pydub import AudioSegment
from sqlalchemy import select
from tenacity import retry, stop_after_attempt, wait_exponential
from trafilatura.settings import use_config

from app.config import MEDIA_DIR, REPO_ROOT, settings
from app.db import Episode, Profile, Session, engine, init_db

TOTAL_ARTICLES = 6  # shared between the interests
MAX_CHARS_PER_ARTICLE = 4000  # news puts the key facts first; this caps the LLM cost
WORDS_PER_MINUTE = 150  # used to turn the episode length into a word target
CHUNK_MAX_CHARS = 1900  # ElevenLabs takes about 2,000 characters per request

# trafilatura waits up to 30 s for a site and tries 3 times: about 2 minutes for a site
# that never answers (Episode 1 lost 4 minutes to two of them). With 5 s: about 20 s.
DOWNLOAD_CONFIG = use_config()
DOWNLOAD_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "5")


# The shape the LLM must answer in (structured output): a title, the turns, the sources.
class Segment(BaseModel):
    speaker: Literal["A", "B"]
    text: str


class Script(BaseModel):
    title: str
    segments: list[Segment]
    sources_used: list[str]


PROMPT = """You write scripts for a daily two-host news podcast. The hosts have a real
conversation; they never take turns reading paragraphs.
Hosts: Alex (speaker "A", the anchor: states the facts, drives the show, hands off between
stories) and Sam (speaker "B", the explainer: asks the listener's question, adds context,
pushes back).

Grounding:
- Use ONLY the articles provided. Attribute facts to the outlet by name ("according to Reuters").
  Never invent numbers, names, or quotes. If an article is thin, say less about it.
- Reactions and opinions never add facts. Frame opinions as questions or perspectives.

Structure:
- Cold open: hook with the most surprising story and greet the listener by naming today's topic.
- 3-4 stories. For each: Alex states the facts in one or two short turns; Sam asks the question
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


# --- 1. News: Bing News RSS per interest, then the article text ---------------------


def get_articles(interests: list[str]) -> list[dict]:
    """About 6 articles in total, shared evenly between the interests."""
    per_interest = max(1, TOTAL_ARTICLES // len(interests))  # 3 interests -> 2 each
    articles = []
    for topic in interests:
        # setmkt/setlang: US English results, whatever country we run from.
        rss = httpx.get(
            f"https://www.bing.com/news/search?q={quote_plus(topic)}"
            "&format=rss&setmkt=en-US&setlang=en-US",
            timeout=10,
        )
        rss.raise_for_status()
        found = []
        # Try twice as many as needed, because some sites block downloads.
        for e in feedparser.parse(rss.text).entries[: per_interest * 2]:
            # Bing's link is a redirect; the publisher's URL is in its `url=` part.
            url = parse_qs(urlparse(e.link).query).get("url", [e.link])[0]
            # fetch_url returns None if the site blocks us, and then extract does too.
            text = trafilatura.extract(
                trafilatura.fetch_url(url, config=DOWNLOAD_CONFIG)
            )
            found.append(
                {
                    "title": e.title,
                    "outlet": e.get("news_source") or urlparse(url).netloc,
                    "url": url,
                    # Blocked or unreadable page: use the short RSS summary instead.
                    "text": (text or e.get("summary", ""))[:MAX_CHARS_PER_ARTICLE],
                    "full_text": bool(text),
                }
            )
        # Full articles first (False sorts before True), then keep what we need.
        found.sort(key=lambda a: not a["full_text"])
        articles += found[:per_interest]

    # Without articles the LLM would have nothing true to say, so stop here.
    if not articles:
        raise RuntimeError(f"No news found for: {', '.join(interests)}")
    return articles


# --- 2. Script: one OpenAI call -----------------------------------------------------


def write_script(articles: list[dict], interests: list[str], minutes: int):
    """Returns (Script, input tokens, output tokens); the tokens give the cost."""
    corpus = "\n\n".join(
        f"[{i}] {a['title']}\nOutlet: {a['outlet']}\nURL: {a['url']}\n{a['text']}"
        for i, a in enumerate(articles, 1)
    )
    # A long script can take a couple of minutes; the SDK also retries on its own.
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=180)
    response = client.responses.parse(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": PROMPT.format(words=minutes * WORDS_PER_MINUTE),
            },
            {
                "role": "user",
                "content": f"Today's topics: {', '.join(interests)}\n\n"
                f"Articles:\n\n{corpus}",
            },
        ],
        text_format=Script,  # the answer must match the Script shape above
    )
    usage = response.usage
    return response.output_parsed, usage.input_tokens, usage.output_tokens


# --- 3. Voices: ElevenLabs text-to-dialogue, a few turns per request ---------------


def split_into_chunks(segments: list[Segment]) -> list[list[Segment]]:
    """Group the turns, in order, into chunks of at most CHUNK_MAX_CHARS characters."""
    chunks = [[]]
    length = 0
    for seg in segments:
        if chunks[-1] and length + len(seg.text) > CHUNK_MAX_CHARS:
            chunks.append([])
            length = 0
        chunks[-1].append(seg)
        length += len(seg.text)
    return chunks


# Try each chunk up to 3 times, waiting a few seconds in between: ElevenLabs sometimes
# fails temporarily (busy or rate-limited), and one failed chunk would waste the episode.
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def voice_chunk(chunk: list[Segment]) -> bytes:
    """Voice a few turns, with both hosts, in one request. Returns MP3 bytes."""
    voices = {"A": settings.elevenlabs_voice_a, "B": settings.elevenlabs_voice_b}
    body = {
        "inputs": [{"text": s.text, "voice_id": voices[s.speaker]} for s in chunk],
        "settings": {
            "stability": settings.elevenlabs_dialogue_stability,
            "similarity": settings.elevenlabs_similarity,
        },
    }
    if settings.elevenlabs_dialogue_model:  # otherwise ElevenLabs uses its default
        body["model_id"] = settings.elevenlabs_dialogue_model
    r = httpx.post(
        "https://api.elevenlabs.io/v1/text-to-dialogue",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
        json=body,
        timeout=120,  # a chunk can take 30 seconds or more
    )
    # ElevenLabs explains what went wrong (quota, bad voice ID...) in the response.
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
    return r.content


# --- 4. Audio: join the chunks into one MP3 -----------------------------------------


def stitch(clips: list[bytes], audio_file: Path) -> float:
    """Join the clips into one MP3 file and return its length in seconds."""
    audio = AudioSegment.empty()
    for i, clip in enumerate(clips):
        if i > 0:
            audio += AudioSegment.silent(duration=300)  # short pause between chunks
        audio += AudioSegment.from_file(io.BytesIO(clip), format="mp3")
    audio.export(
        audio_file,
        format="mp3",
        bitrate="128k",
        # loudnorm: every episode at the same loudness (-16 LUFS, the podcast
        # standard). It changes the sample rate, so -ar sets it back; -ac 1 = mono.
        parameters=["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "1"],
    )
    return len(audio) / 1000  # pydub counts milliseconds


# --- All stages ---------------------------------------------------------------------


def make_episode(interests: list[str], minutes: int, audio_file: Path) -> dict:
    """Run every stage and return what to save on the Episode. Raises if a stage fails."""
    timings = {}  # seconds per stage, to see where the time goes

    start = time.perf_counter()
    articles = get_articles(interests)
    timings["news"] = round(time.perf_counter() - start, 1)

    start = time.perf_counter()
    script, tokens_in, tokens_out = write_script(articles, interests, minutes)
    timings["script"] = round(time.perf_counter() - start, 1)

    start = time.perf_counter()
    clips = [voice_chunk(chunk) for chunk in split_into_chunks(script.segments)]
    timings["voices"] = round(time.perf_counter() - start, 1)

    start = time.perf_counter()
    duration = stitch(clips, audio_file)
    timings["audio"] = round(time.perf_counter() - start, 1)

    # OpenAI charges per token (price per million), ElevenLabs per character
    # (price per thousand).
    characters = sum(len(s.text) for s in script.segments)
    cost = (
        tokens_in * settings.openai_price_input_per_1m
        + tokens_out * settings.openai_price_output_per_1m
    ) / 1_000_000 + characters / 1000 * settings.elevenlabs_price_per_1k_chars

    return {
        "title": script.title,
        "script": [s.model_dump() for s in script.segments],
        # Only the articles the LLM says it used.
        "sources": [
            {"title": a["title"], "outlet": a["outlet"], "url": a["url"]}
            for a in articles
            if a["url"] in script.sources_used
        ],
        "audio_path": str(audio_file.relative_to(REPO_ROOT)),  # "media/episode-3.mp3"
        "duration_sec": round(duration),
        "cost_usd": round(cost, 3),
        "timings": timings,
    }


async def run_episode(episode_id: int) -> None:
    """Make the episode and save the result, or the error, on its row. Never raises."""
    async with Session() as db:
        episode = await db.get(Episode, episode_id)
        profile = await db.scalar(select(Profile))
        episode.status = "working"
        await db.commit()

        try:
            # make_episode spends a minute or two waiting on the network. Running it in
            # a separate thread keeps the server free to answer other requests.
            result = await asyncio.to_thread(
                make_episode,
                profile.interests,
                profile.length_minutes,
                MEDIA_DIR / f"episode-{episode_id}.mp3",
            )
        except Exception as e:  # noqa: BLE001 (on purpose: any failure goes on the episode)
            episode.status = "failed"
            episode.error = f"{type(e).__name__}: {e}"
        else:
            # The result's keys are Episode fields: title, script, sources...
            for field, value in result.items():
                setattr(episode, field, value)
            episode.status = "done"
        await db.commit()


async def main() -> None:
    """`make episode`: one episode with the profile's settings, then a summary."""
    await init_db()
    async with Session() as db:
        episode = Episode()
        db.add(episode)
        await db.commit()

    await run_episode(episode.id)

    async with Session() as db:
        episode = await db.get(Episode, episode.id)
        profile = await db.scalar(select(Profile))
    await engine.dispose()  # close the database so the program can exit

    if episode.status == "failed":
        print(f"Failed: {episode.error}")
        return
    words = sum(len(s["text"].split()) for s in episode.script)
    target = profile.length_minutes * WORDS_PER_MINUTE
    print(f"{episode.title!r} -> {episode.audio_path}")
    print(f"  {episode.duration_sec / 60:.1f} min, {len(episode.script)} turns")
    print(f"  {words} words (target {target})")
    print(f"  cost ${episode.cost_usd}, seconds per stage {episode.timings}")
    print(f"  sources: {', '.join(s['outlet'] for s in episode.sources)}")


if __name__ == "__main__":
    asyncio.run(main())
