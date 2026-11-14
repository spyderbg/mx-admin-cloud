"""Application configuration via Pydantic Settings; all env vars and defaults live here."""

import re
from pathlib import Path
from typing import ClassVar, List, Pattern, Protocol

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ so.env values override Settings defaults
load_dotenv()


class OutputStemSource(Protocol):  # pylint: disable=too-few-public-methods
    """Structural type for values used to build output file stems."""

    filename_text: str
    language_code: str
    text: str
    request_id: str


class Settings(BaseSettings):
    """Centralized settings loaded from environment variables and the .env file."""

    # Absolute path to the backend/ directory — never overridable via env var
    BASE_DIR: ClassVar[Path] = Path(__file__).parent.parent.parent.resolve()
    SAFE_NAME_RE: ClassVar[Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]+")

    # For each field, if an env var with the same name exists, use its value; else use default
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Logging settings
    LOG_PROFILE: str = "dev"

    # Directory settings
    CONFIG_DIR: str = "config"
    CONTENT_DIR: str = "../content"
    ARCHIVE_DIR: str = "../archive"
    OUTPUT_DIR: str = "../output"
    TEMP_DIR: str = "../temp"

    # API settings
    PROJECT_NAME: str = "FastAPI Backend"
    API_V1_STR: str = "/api/v1"
    DEBUG_MODE: bool = True

    # Authentication settings
    SECRET_KEY: str = "your-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CLI_TOKEN_EXPIRE_SECONDS: int = 30
    AUTH_DISABLED: bool = True
    # "Log in with Google": allowed only when enabled AND an OAuth web client id (Google Cloud
    # console, with the frontend's origin as an authorized JavaScript origin) is set.
    GOOGLE_AUTH_ENABLED: bool = False
    GOOGLE_CLIENT_ID: str | None = None

    # Database settings
    DB_PATH: str = "data/users.db"
    DEFAULT_ADMIN_PASSWORD: str = "MarXman2025"

    # Language settings
    SUPPORTED_LANGUAGES: List[str] = ["en", "fr", "es", "bg"]

    # Provider API keys
    OPENAI_API_KEY: str | None = None
    QWEN_API_KEY: str | None = None
    ELEVENLABS_API_KEY: str | None = None
    SPEAKTOR_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    COMFYUI_API_KEY: str | None = None

    # TTS pipeline settings
    TTS_MAX_CONCURRENCY: int = 5

    # Internal API base URL used for service-to-service calls (e.g. pipeline → translate endpoint)
    API_BASE_URL: str = "http://127.0.0.1:8000"

    @property
    def google_auth_allowed(self) -> bool:
        """Return whether users may log in with Google: enabled and a client id is configured."""
        return self.GOOGLE_AUTH_ENABLED and bool((self.GOOGLE_CLIENT_ID or "").strip())

    # --- Resolved path properties ---
    # Each property: absolute env-var path used as-is; relative path resolved against BASE_DIR.
    # In Docker, set e.g. CONFIG_DIR=/app/config to bypass BASE_DIR entirely.

    @property
    def config_path(self) -> Path:
        """Return the path to the config directory."""
        p = Path(self.CONFIG_DIR)
        return p if p.is_absolute() else self.BASE_DIR / p

    @property
    def content_path(self) -> Path:
        """Return the path to the content directory."""
        p = Path(self.CONTENT_DIR)
        return p if p.is_absolute() else self.BASE_DIR / p

    @property
    def archive_path(self) -> Path:
        """Return the path to the archive directory."""
        p = Path(self.ARCHIVE_DIR)
        return p if p.is_absolute() else self.BASE_DIR / p

    @property
    def output_path(self) -> Path:
        """Return the path to the output directory."""
        p = Path(self.OUTPUT_DIR)
        return p if p.is_absolute() else self.BASE_DIR / p

    @property
    def temp_path(self) -> Path:
        """Return the path to the temp directory."""
        p = Path(self.TEMP_DIR)
        return p if p.is_absolute() else self.BASE_DIR / p

    def slugify_filename(self, value: str, fallback: str) -> str:
        """Slugify a filename."""
        text = value.strip().replace(" ", "_")
        text = self.SAFE_NAME_RE.sub("_", text)
        text = re.sub(r"_+", "_", text).strip("._")
        return text or fallback

    def raw_output_dir(self, language_code: str, provider: str) -> Path:
        """Get the raw output directory."""
        return self.output_path / f"tts_audio_{language_code}" / provider

    def images_output_dir(self, provider: str) -> Path:
        """Get the generated-images output directory for *provider*."""
        return self.output_path / "images" / provider

    def optimized_output_dir(self, optimizer_name: str, language_code: str, provider: str) -> Path:
        """Get the optimized output directory."""
        return self.output_path / f"optimized_{optimizer_name}" / f"tts_audio_{language_code}" / provider

    def normalized_output_dir(self, language_code: str, provider: str) -> Path:
        """Get the normalized output directory."""
        return self.output_path / "results" / "normalized" / language_code / provider

    def reports_output_dir(self) -> Path:
        """Get the reports' directory."""
        return self.output_path / "results" / "reports"

    def logs_output_dir(self) -> Path:
        """Get the logs directory."""
        return self.output_path / "results" / "logs"

    def requests_log_file(self) -> Path:
        """Get the requests log path."""
        return self.output_path / "results" / "requests.log"

    def scores_output_dir(self) -> Path:
        """Get the scores directory."""
        return self.output_path / "results" / "scores"

    def build_output_stem(self, request: OutputStemSource) -> str:
        """Build the output stem."""
        base = request.filename_text if request.language_code != "en" and request.filename_text else request.text
        safe_text = self.slugify_filename(base, fallback=request.request_id)
        return f"{safe_text}_{request.language_code}"


settings = Settings()
