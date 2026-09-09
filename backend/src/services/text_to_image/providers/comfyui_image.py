"""ComfyUI image provider for a self-hosted ComfyUI server.

Flow (ComfyUI's standard HTTP API):

1. ``POST {base_url}/prompt`` with an API-format workflow graph -> ``prompt_id``
2. poll ``GET {base_url}/history/{prompt_id}`` until the job reports outputs
3. ``GET {base_url}/view?filename=...`` to download the produced image

The workflow is config, not code: supply it inline (``workflow``) or point at a
JSON file next to the config (``workflow_file``). Placeholders such as
``{prompt}``, ``{seed}``, ``{steps}`` and ``{cfg}`` are substituted with typed
values, so a workflow exported from the ComfyUI UI works unmodified.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import TextToImageProviderFactory
from src.services.text_to_image.templating import render_template

logger = logging.getLogger(__name__)

_MAX_SEED = 2 ** 32 - 1
_DEFAULT_POLL_INTERVAL = 2.0
_DEFAULT_POLL_ATTEMPTS = 150


class ComfyUIImageProvider(TextToImageProvider):
    """Image provider backed by a self-hosted ComfyUI server."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        gen = self.generate_config

        base_url = str(gen.get("base_url", "") or "").strip().rstrip("/")
        if not base_url:
            raise ValueError(
                f"ComfyUI config requires 'generate.base_url' (file='{self.config_path}').",
            )
        self._base_url = base_url
        self._workflow = self._load_workflow(gen)
        self._sleep = asyncio.sleep

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"comfyui"``."""
        TextToImageProviderFactory.register("comfyui", cls)

    def api_key_env_name(self) -> str:
        """Optional credential, used only by the configured header template."""
        return "COMFYUI_API_KEY"

    def _load_workflow(self, gen: dict[str, Any]) -> dict[str, Any]:
        """Load and validate the workflow graph from config or disk."""
        inline = gen.get("workflow")
        workflow_file = gen.get("workflow_file")

        if inline is not None and workflow_file:
            raise ValueError(
                f"ComfyUI config sets both 'workflow' and 'workflow_file'; pick one "
                f"(file='{self.config_path}').",
            )

        if inline is not None:
            workflow = inline
        elif workflow_file:
            # Workflow paths are relative to the config file's directory. Runtime-
            # uploaded configs have no real path on disk, so they must inline it.
            path = Path(str(workflow_file))
            if not path.is_absolute():
                path = self.config_path.parent / path
            if not path.is_file():
                raise ValueError(
                    f"ComfyUI workflow file not found: '{path}' (file='{self.config_path}').",
                )
            try:
                workflow = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"ComfyUI workflow file '{path}' is not readable JSON: {exc}") from exc
        else:
            raise ValueError(
                f"ComfyUI config requires 'generate.workflow' or 'generate.workflow_file' "
                f"(file='{self.config_path}').",
            )

        if not isinstance(workflow, dict) or not workflow:
            raise ValueError(
                f"ComfyUI workflow must be a non-empty API-format object "
                f"(file='{self.config_path}').",
            )
        if "{prompt}" not in json.dumps(workflow):
            raise ValueError(
                f"ComfyUI workflow has no '{{prompt}}' placeholder, so the prompt would be "
                f"ignored (file='{self.config_path}').",
            )
        return workflow

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _resolve_seed(self, request: ImageRequest) -> int:
        if request.seed is not None:
            return int(request.seed)
        configured = self.generate_config.get("seed")
        if configured is not None and str(configured).strip() != "":
            try:
                return int(configured)
            except (TypeError, ValueError):
                logger.warning("Ignoring non-integer ComfyUI seed %r", configured)
        return random.randrange(0, _MAX_SEED)

    def _build_context(self, request: ImageRequest) -> dict[str, Any]:
        from src.core.config import settings  # pylint: disable=import-outside-toplevel

        gen = self.generate_config
        width, height = 1024, 1024
        size = self.resolve_size(request)
        if size and "x" in str(size):
            parts = str(size).lower().split("x")
            try:
                width, height = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        width = int(gen.get("width", width) or width)
        height = int(gen.get("height", height) or height)

        stem = request.filename_text.strip() or request.prompt.strip()[:60]
        return {
            "prompt": self.build_prompt(request),
            "negative_prompt": self.resolve_negative_prompt(request) or "",
            "seed": self._resolve_seed(request),
            "width": width,
            "height": height,
            "steps": int(gen.get("steps", 20) or 20),
            "cfg": float(gen.get("cfg", 7.0) or 7.0),
            "checkpoint": str(gen.get("checkpoint", "") or ""),
            "filename_prefix": str(gen.get("filename_prefix") or settings.slugify_filename(stem, "image")),
            "api_key": self.resolve_api_key(),
        }

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        """Submit the workflow, wait for the render, and download the image."""
        gen = self.generate_config
        context = self._build_context(request)
        graph = render_template(self._workflow, context)
        headers = render_template(gen.get("headers", {}) or {}, context)
        timeout = self.traffic_policy.timeout_seconds

        submit = await client.post(
            f"{self._base_url}/prompt",
            json={"prompt": graph, "client_id": uuid4().hex},
            headers=headers,
            timeout=timeout,
        )
        submit.raise_for_status()
        prompt_id = submit.json().get("prompt_id")
        if not prompt_id:
            raise NonRetryableError("ComfyUI did not return a prompt_id.")

        image_ref = await self._await_output(client, headers, prompt_id, gen, timeout)

        view = await client.get(
            f"{self._base_url}/view",
            params={
                "filename": image_ref.get("filename", ""),
                "subfolder": image_ref.get("subfolder", ""),
                "type": image_ref.get("type", "output"),
            },
            headers=headers,
            timeout=timeout,
        )
        view.raise_for_status()

        return ImagePayload(
            data=view.content,
            # The Save node decides the format, so trust the response header.
            mime_type=view.headers.get("content-type"),
            width=context["width"],
            height=context["height"],
        )

    async def _await_output(
            self,
            client: Any,
            headers: dict[str, Any],
            prompt_id: str,
            gen: dict[str, Any],
            timeout: float,
    ) -> dict[str, Any]:
        """Poll the history endpoint until the job produces an image."""
        interval = float(gen.get("poll_interval_seconds", _DEFAULT_POLL_INTERVAL) or _DEFAULT_POLL_INTERVAL)
        attempts = int(gen.get("poll_max_attempts", _DEFAULT_POLL_ATTEMPTS) or _DEFAULT_POLL_ATTEMPTS)

        for _ in range(max(1, attempts)):
            await self._sleep(interval)
            history = await client.get(
                f"{self._base_url}/history/{prompt_id}",
                headers=headers,
                timeout=timeout,
            )
            history.raise_for_status()
            entry = (history.json() or {}).get(prompt_id)
            if not entry:
                continue  # queued but not yet recorded

            status = entry.get("status") or {}
            if str(status.get("status_str", "")).lower() == "error":
                raise NonRetryableError(f"ComfyUI job {prompt_id} failed: {self._error_text(status)}")

            images = self._extract_output_images(entry)
            if images:
                return images[0]

        # Never resubmit: the job may still be rendering on the server.
        raise NonRetryableError(
            f"ComfyUI job {prompt_id} did not produce an image after {attempts} polls.",
        )

    @staticmethod
    def _extract_output_images(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect image references from a history entry, preferring saved outputs."""
        saved: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []
        for node_output in (entry.get("outputs") or {}).values():
            for image in (node_output or {}).get("images") or []:
                if not isinstance(image, dict) or not image.get("filename"):
                    continue
                if str(image.get("type", "")).lower() == "output":
                    saved.append(image)
                else:
                    others.append(image)
        return saved or others

    @staticmethod
    def _error_text(status: dict[str, Any]) -> str:
        messages: list[str] = []
        for item in status.get("messages") or []:
            if isinstance(item, (list, tuple)) and item:
                messages.append(str(item[-1]))
            else:
                messages.append(str(item))
        return "; ".join(messages) if messages else "unknown error"


ComfyUIImageProvider.register()
