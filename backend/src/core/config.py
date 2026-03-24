"""Application configuration via Pydantic Settings; all env vars and defaults live here."""

from pathlib import Path
from typing import ClassVar, List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ so Settings defaults are overridden by .env values
load_dotenv()


class Settings(BaseSettings):
    """Centralised settings loaded from environment variables and the .env file."""

    # Absolute path to the backend/ directory — never overridable via env var
    BASE_DIR: ClassVar[Path] = Path(__file__).parent.parent.parent.resolve()

    # For each field, if an env var with the same name exists, use its value; else use default
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Logging settings
    LOG_PROFILE: str = "dev"

    # Directories settings
    CONFIG_DIR: str = "config"
    CONTENT_DIR: str = "../content"
    ARCHIVE_DIR: str = "../archive"
    OUTPUT_DIR: str = "../output"

    # API settings
    PROJECT_NAME: str = "FastAPI Backend"
    API_V1_STR: str = "/api/v1"
    DEBUG_MODE: bool = True

    # Authentication settings
    SECRET_KEY: str = "your-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CLI_TOKEN_EXPIRE_SECONDS: int = 30
    AUTH_DISABLED: bool = False

    # Database settings
    DB_PATH: str = "data/users.db"
    DEFAULT_ADMIN_PASSWORD: str = "MarXman2025"

    # Language settings
    SUPPORTED_LANGUAGES: List[str] = ["en", "fr", "es", "bg"]

    # Provider API keys
    OPENAI_API_KEY: str | None = None
    ELEVENLABS_API_KEY: str | None = None
    SPEAKTOR_API_KEY: str | None = None

    # TTS pipeline settings
    TTS_MAX_CONCURRENCY: int = 5


settings = Settings()
