from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ so Settings defaults are overridden by .env values
load_dotenv()


class Settings(BaseSettings):
    # For each field, if an env var with the same name exists, use its value; else use default
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "FastAPI Backend"
    API_V1_STR: str = "/api/v1"

    SECRET_KEY: str = "your-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CLI_TOKEN_EXPIRE_SECONDS: int = 30

    DB_PATH: str = "data/users.db"
    DEFAULT_ADMIN_PASSWORD: str = "MarXman2025"

    CONTENT_DIR: str = "../content"
    ARCHIVE_DIR: str = "../archive"

    SUPPORTED_LANGUAGES: List[str] = ["en", "fr", "es", "bg"]
    OPENAI_API_KEY: str | None = None

    AUTH_DISABLED: bool = False

    ELEVENLABS_API_KEY: str | None = None
    SPEAKTOR_API_KEY: str | None = None
    INTERNAL_SERVICE_TOKEN: str = "change-me-in-production"
    TTS_CONFIG_DIR: str = "config"
    TTS_OUTPUT_DIR: str = "output"
    TTS_MAX_CONCURRENCY: int = 5
    TTS_LOG_PROFILE: str = "dev"


settings = Settings()
