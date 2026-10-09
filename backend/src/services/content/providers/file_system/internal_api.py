"""Internal HTTP callers for the file_system content provider (respect the HTTP boundary rule).

Translation, TTS, image generation and voice optimization are delegated to the internal
/internal/* endpoints rather than calling other services in-process.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path

import httpx

from src.core.config import settings
from src.security.tokens import create_internal_token
from src.services.content.models import ContentProvider
from src.services.provider_strategy import ProviderSelectionStrategy


def _extension_for_content_type(content_type: str | None) -> str:
    """Map an image response content type onto a supported file extension."""
    if content_type and content_type.split(";", 1)[0].strip().lower() in ("image/jpeg", "image/jpg"):
        return ".jpg"
    return ".png"


class _GeneratedAudioBytes(bytes):
    """Audio payload carrying the provider reported by the internal TTS API."""

    provider: str
    parameters: dict[str, object]

    def __new__(cls, content: bytes, provider: str, parameters: dict[str, object]):
        instance = super().__new__(cls, content)
        instance.provider = provider
        instance.parameters = parameters
        return instance


class FileSystemInternalApiMixin(ContentProvider):  # pylint: disable=abstract-method
    """Calls to the internal translation, TTS and image-generation HTTP endpoints."""

    @classmethod
    def _translate_word(
            cls,
            text: str,
            target_lang_code: str | None = None,
            category: str | None = None,
            input_lang_code: str | None = None,
            *,
            require_success: bool = False,
    ) -> str:
        """Translate text via the internal /internal/translators/translate endpoint.

        The endpoint answers 200 with ``success: false`` and the untranslated input when
        no translator produced a result. With *require_success* that case raises instead
        of silently returning the source text.
        """
        if not text or not text.strip():
            return text.strip()

        token = create_internal_token("translation-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/translators/translate",
            json={
                "text": text.strip(),
                "target_lang_code": target_lang_code,
                "input_lang_code": input_lang_code,
                "category": category,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        if response.is_error:
            raise RuntimeError(cls._extract_error_detail(response))
        payload = response.json()
        if require_success and not payload.get("success", False):
            errors = payload.get("errors") or []
            if errors:
                raise RuntimeError("; ".join(errors))
            raise RuntimeError(f"No active translator produced a translation for '{target_lang_code}'.")
        translated_text = payload["lang_text"]["text"]
        return translated_text or text.strip()

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str:
        """Return the internal endpoint's JSON 'detail' field, falling back to raw response text."""
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        return str(detail) if detail else response.text

    @classmethod
    def _generate_audio(
            cls,
            word: str,
            lang: str,
            configuration_name: str | None = None,
            voice_settings: dict[str, float] | None = None,
    ) -> bytes:
        """Generate TTS audio via the internal /internal/generators/generate-word endpoint.

        With *configuration_name* that configuration generates, enabled or not; otherwise
        the active ones do. *voice_settings* (speed, stability, similarity) override the
        configuration's own for this request.
        """
        if not word or not word.strip():
            raise ValueError("Word must be non-empty for TTS.")

        body: dict[str, object] = {"word": word.strip(), "lang": lang}
        if configuration_name:
            body["configuration_name"] = configuration_name
        if voice_settings:
            body["settings"] = voice_settings
        token = create_internal_token("tts-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/generators/generate-word",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
        if response.is_error:
            raise RuntimeError(cls._extract_error_detail(response))
        encoded_parameters = response.headers.get("X-TTS-Parameters", "")
        try:
            parameters = json.loads(base64.urlsafe_b64decode(encoded_parameters).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}
        return _GeneratedAudioBytes(
            response.content,
            response.headers.get("X-TTS-Provider", "unknown"),
            parameters,
        )

    @classmethod
    def _active_voice_optimizers(cls) -> list[str]:
        """Return the names of the enabled voice optimizers, in priority order.

        Uses the internal /internal/optimizers/ listing, which includes disabled configs too.
        """
        token = create_internal_token("voice-optimization-service", timedelta(seconds=30))
        response = httpx.get(
            f"{settings.API_BASE_URL}/internal/optimizers/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        if response.is_error:
            raise RuntimeError(cls._extract_error_detail(response))
        return [str(entry["name"]) for entry in response.json() if entry.get("enabled")]

    @staticmethod
    def _voice_optimization_strategy() -> ProviderSelectionStrategy:
        """Return how optimizers are dispatched: every active one, or until one succeeds.

        Read from the internal configuration API, like the optimizer listing. When it can't
        be read, every active optimizer runs, which is the voice optimization service's default.
        """
        token = create_internal_token("configuration-service", timedelta(seconds=30))
        try:
            response = httpx.get(
                f"{settings.API_BASE_URL}/internal/configurations/voice_optimization/strategy",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            return ProviderSelectionStrategy(response.json().get("strategy"))
        except (httpx.HTTPError, ValueError, AttributeError):
            return ProviderSelectionStrategy.ALL_PROVIDERS

    @classmethod
    def _optimize_audio_file(cls, optimizer: str, raw_path: Path, output_path: Path) -> None:
        """Optimize *raw_path* into *output_path* via /internal/optimizers/{optimizer}/optimize.

        That endpoint reads and writes the given paths itself, so the voice_optimization
        service must see the same content filesystem as this provider.
        """
        token = create_internal_token("voice-optimization-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/optimizers/{optimizer}/optimize",
            json={"raw_path": str(raw_path), "output_path": str(output_path)},
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        if response.is_error:
            raise RuntimeError(cls._extract_error_detail(response))

    @classmethod
    def _generate_image(
            cls,
            prompt: str,
            filename_text: str | None = None,
            configuration_name: str | None = None,
    ) -> tuple[bytes, str]:
        """Generate an image via the internal /internal/image-generators/generate-image endpoint.

        Returns the raw bytes plus the file extension implied by the response
        content type, so whichever image provider is active (and however it
        encodes the result) decides the format. *configuration_name* picks one active image
        configuration; without it the first active one that succeeds is used.
        """
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must be non-empty for image generation.")

        token = create_internal_token("image-service", timedelta(seconds=30))
        response = httpx.post(
            f"{settings.API_BASE_URL}/internal/image-generators/generate-image",
            json={"prompt": prompt.strip(), "filename_text": filename_text, "provider": configuration_name},
            headers={"Authorization": f"Bearer {token}"},
            # Local render backends (e.g. ComfyUI) can legitimately take minutes.
            timeout=httpx.Timeout(600.0, connect=10.0),
        )
        if response.is_error:
            raise RuntimeError(cls._extract_error_detail(response))
        return response.content, _extension_for_content_type(response.headers.get("content-type"))
