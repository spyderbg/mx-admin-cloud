"""Application configuration via Pydantic Settings; all env vars and defaults live here."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, List, Pattern, Protocol

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environment variable that names the env file explicitly; a missing file is an error.
ENV_FILE_VAR = "MX_ADMIN_ENV_FILE"
# Directory and file name below ${XDG_CONFIG_HOME:-~/.config}.
APP_CONFIG_DIR_NAME = "mx-admin-cloud"
APP_ENV_FILE_NAME = "app.env"
# Repository root (main checkout or feature worktree): backend/src/core/config.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]


class MissingEnvFileError(FileNotFoundError):
    """Raised when MX_ADMIN_ENV_FILE names a file that does not exist."""


def _repo_root() -> Path:
    """Return the repository root that holds the legacy `.env` (patched in tests)."""
    return REPO_ROOT


def _home(environ: Mapping[str, str]) -> Path:
    """Return the mapping's HOME, or the user's home directory when it has none."""
    home = environ.get("HOME", "").strip()
    return Path(home) if home else Path.home()


def _expand_user(value: str, environ: Mapping[str, str]) -> Path:
    """Expand a leading `~` with the mapping's HOME (Path.expanduser reads os.environ only).

    `~user` forms are left to Path.expanduser. With os.environ the result equals Path.expanduser.
    """
    if value == "~" or value.startswith(("~/", "~" + os.sep)):
        return _home(environ) / value[2:]
    return Path(value).expanduser()


def _xdg_config_home(environ: Mapping[str, str]) -> Path:
    """Return ${XDG_CONFIG_HOME:-~/.config}; a relative XDG_CONFIG_HOME is ignored (XDG spec)."""
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg and _expand_user(xdg, environ).is_absolute():
        return _expand_user(xdg, environ)
    return _home(environ) / ".config"


def resolve_env_file(environ: Mapping[str, str] | None = None) -> Path | None:
    """Return the env file to load, or None.

    Order: MX_ADMIN_ENV_FILE (must exist, no fallback), then
    ${XDG_CONFIG_HOME:-~/.config}/mx-admin-cloud/app.env, then the legacy repository-root
    `.env`. None of these depend on the current working directory. Only file existence is
    checked here; contents are never read or reported. `environ` supplies every variable the
    lookup reads, including the HOME used to expand `~`.
    """
    env = os.environ if environ is None else environ
    explicit = env.get(ENV_FILE_VAR, "").strip()
    if explicit:
        # An explicit relative path is the caller's choice; it is made absolute once, here.
        path = _expand_user(explicit, env).absolute()
        if not path.is_file():
            raise MissingEnvFileError(
                f"{ENV_FILE_VAR} is set to '{path}', but that file does not exist. "
                f"Create the file or unset {ENV_FILE_VAR} to use the default lookup."
            )
        return path
    app_env = _xdg_config_home(env) / APP_CONFIG_DIR_NAME / APP_ENV_FILE_NAME
    if app_env.is_file():
        return app_env
    legacy = _repo_root() / ".env"
    if legacy.is_file():
        return legacy
    return None


def load_environment(environ: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the env file and load it into os.environ without overriding existing variables."""
    path = resolve_env_file(environ)
    if path is not None:
        load_dotenv(path, override=False)
    return path


# Resolved once at import; both load_dotenv() and Settings.env_file use this path.
ENV_FILE: Path | None = load_environment()


class OutputStemSource(Protocol):  # pylint: disable=too-few-public-methods
    """Structural type for values used to build output file stems."""

    filename_text: str
    language_code: str
    text: str
    request_id: str


class Settings(BaseSettings):
    """Centralized settings loaded from environment variables and the resolved env file.

    Precedence: process environment, then the env file (`ENV_FILE`), then the defaults below.
    """

    # Absolute path to the backend/ directory — never overridable via env var
    BASE_DIR: ClassVar[Path] = Path(__file__).parent.parent.parent.resolve()
    SAFE_NAME_RE: ClassVar[Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]+")

    # For each field, if an env var with the same name exists, use its value; else use default
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
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
