"""ComfyUI image provider for a self-hosted ComfyUI server (provider package).

Importing this package registers ``ComfyUIImageProvider`` with ``TextToImageProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_image.providers.comfyui_image.provider import ComfyUIImageProvider

__all__ = ["ComfyUIImageProvider"]
