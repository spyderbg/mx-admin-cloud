"""Speaktor (Transkriptor) speech-to-text backend (``evaluate.stt_provider: "speaktor"``).

Config keys read from the ``evaluate`` block: ``poll_interval_seconds`` (default 3) and
``poll_max_attempts`` (default 40). The language is sent as a BCP-47 tag.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from src.core.languages import to_bcp47
from src.services.evaluation.models import STTProvider
from src.services.evaluation.service import SpeechToTextProviderFactory


class SpeaktorSTTProvider(STTProvider):
    """Transcribes through the Transkriptor local-file workflow."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"speaktor"`` in SpeechToTextProviderFactory."""
        SpeechToTextProviderFactory.register("speaktor", cls)

    async def transcribe(
            self,
            audio_path: Path,
            language: str,
            eval_cfg: dict[str, Any],
            api_key: str,
            client: Any,
    ) -> str:
        """Transcribe via the Transkriptor 3-step local-file workflow.

        1. POST /local_file/get_upload_url -> upload_url, public_url
        2. PUT upload_url -> upload raw audio bytes
        3. POST /local_file/initiate_transcription -> order_id
        4. Poll GET /files/{order_id}/content until status == "Completed"
        """
        base_url = "https://api.tor.app/developer"
        language = to_bcp47(language)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        started_at = time.perf_counter()
        upload_url, public_url = await self._get_upload_url(client, base_url, headers, audio_path)
        await self._upload_audio(client, upload_url, audio_path)
        order_id = await self._initiate_transcription(client, base_url, headers, public_url, language)
        transcription_text = await self._poll_transcription(client, base_url, headers, eval_cfg, order_id)
        response_duration = time.perf_counter() - started_at

        await self._log_request(
            provider="speaktor",
            model="transkriptor-stt",
            language=language,
            request_input=f"audio:{audio_path.name}",
            response_duration=response_duration,
        )
        return transcription_text

    async def _get_upload_url(
            self, client: Any, base_url: str, headers: dict[str, str], audio_path: Path
    ) -> tuple[str, str]:
        """Step 1 -- get a pre-signed upload URL. Returns (upload_url, public_url)."""
        response = await client.post(
            f"{base_url}/transcription/local_file/get_upload_url",
            headers=headers,
            json={"file_name": audio_path.name},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        upload_meta = response.json()
        return upload_meta["upload_url"], upload_meta["public_url"]

    async def _upload_audio(self, client: Any, upload_url: str, audio_path: Path) -> None:
        """Step 2 -- upload the audio file."""
        response = await client.put(upload_url, content=audio_path.read_bytes(), timeout=self.timeout_seconds)
        response.raise_for_status()

    async def _initiate_transcription(
            self, client: Any, base_url: str, headers: dict[str, str], public_url: str, language: str
    ) -> str:
        """Step 3 -- initiate transcription. Returns the order_id."""
        response = await client.post(
            f"{base_url}/transcription/local_file/initiate_transcription",
            headers=headers,
            json={
                "url": public_url,
                "language": language,
                "service": "Standard",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["order_id"]

    async def _poll_transcription(
            self, client: Any, base_url: str, headers: dict[str, str], eval_cfg: dict[str, Any], order_id: str
    ) -> str:
        """Step 4 -- poll until the transcription completes."""
        poll_interval = float(eval_cfg.get("poll_interval_seconds", 3))
        poll_max = int(eval_cfg.get("poll_max_attempts", 40))
        for _ in range(poll_max):
            await asyncio.sleep(poll_interval)
            response = await client.get(
                f"{base_url}/files/{order_id}/content",
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            content_data = response.json()
            body = content_data.get("body", content_data)
            if str(body.get("status", "")).strip() == "Completed":
                segments = body.get("content", [])
                return " ".join(
                    str(seg.get("text", "")).strip()
                    for seg in segments
                    if str(seg.get("text", "")).strip()
                )
        raise RuntimeError(
            f"Transkriptor transcription did not complete within "
            f"{poll_max * poll_interval:.0f}s (order_id={order_id})."
        )


SpeaktorSTTProvider.register()
