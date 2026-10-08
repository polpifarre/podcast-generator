"""Test setup, run before the app is imported: fake keys and a throwaway database."""

import os
import tempfile

# Environment variables win over the .env file, so the tests never see the real keys
# and can't call OpenAI or ElevenLabs by accident.
os.environ.update(
    OPENAI_API_KEY="test",
    OPENAI_MODEL="test",
    ELEVENLABS_API_KEY="test",
    ELEVENLABS_VOICE_A="voice-a",
    ELEVENLABS_VOICE_B="voice-b",
    DATABASE_URL=f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db",
)
