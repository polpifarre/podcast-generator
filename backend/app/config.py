"""Settings, read from the .env file at the repo root (or from environment variables)."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# This file is <repo>/backend/app/config.py, so two levels up is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
MEDIA_DIR = REPO_ROOT / "media"  # generated audio (gitignored)
MEDIA_DIR.mkdir(exist_ok=True)  # a fresh clone doesn't have it yet


class Settings(BaseSettings):
    """Each field maps to an upper-case variable: `openai_model` <- OPENAI_MODEL.

    Fields without a default are required: the app won't start if one is missing.
    """

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # Required. SecretStr hides the key when printed; .get_secret_value() reveals it
    # only where it's sent to the API.
    openai_api_key: SecretStr
    openai_model: str
    elevenlabs_api_key: SecretStr
    elevenlabs_voice_a: str  # host A (Alex)
    elevenlabs_voice_b: str  # host B (Sam)

    # Empty = a SQLite file at the repo root (see db.py).
    database_url: str = ""

    # How the voices sound. No model set = ElevenLabs' default dialogue model.
    elevenlabs_dialogue_model: str | None = None
    elevenlabs_dialogue_stability: float = 0.5
    elevenlabs_similarity: float = 0.75

    # Prices in USD, only used for the cost shown on each episode.
    openai_price_input_per_1m: float = 0.75
    openai_price_output_per_1m: float = 4.50
    elevenlabs_price_per_1k_chars: float = 0.08


settings = Settings()
