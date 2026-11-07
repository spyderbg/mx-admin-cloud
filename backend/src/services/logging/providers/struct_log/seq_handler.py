"""Seq handler that keeps sending after another logging provider reconfigures stdlib logging.

``StructLogProvider`` swaps this class in for ``seqlog.structured_logging.SeqLogHandler``, so
logging configs keep naming the seqlog class. It is imported only through
``logging.config.dictConfig``, after the provider has checked that seqlog is installed.
"""

from __future__ import annotations

import logging
from typing import Any

from seqlog.structured_logging import SeqLogHandler


class SeqHandler(SeqLogHandler):
    """``SeqLogHandler`` whose sender thread survives ``logging.config.dictConfig``.

    Every ``StructLogProvider`` applies its handlers with ``dictConfig``, which first closes
    every handler that already exists, including the Seq handlers of the providers configured
    before it, and the provider then re-attaches them. A closed ``SeqLogHandler`` has stopped
    its sender thread for good, so records logged afterwards sit in its queue and never reach
    Seq. ``close()`` here only sends the pending batch; ``stop_sending()`` stops the thread
    once the provider has replaced the handler. The thread is a daemon and ends with the
    process.

    ``auto_flush_timeout`` defaults to one second: seqlog's default (``None``) holds records
    until a full batch of ``batch_size`` has been logged, so a single event never arrives.
    """

    def __init__(
            self,
            server_url: str,
            api_key: str | None = None,
            batch_size: int = 10,
            auto_flush_timeout: float | None = 1.0,
            json_encoder_class: Any = None,
    ) -> None:
        super().__init__(
            server_url,
            api_key=api_key,
            batch_size=batch_size,
            auto_flush_timeout=auto_flush_timeout,
            json_encoder_class=json_encoder_class,
        )

    def close(self) -> None:
        """Send the pending batch but keep the sender thread running (see the class docstring)."""
        try:
            self.flush()
        finally:
            logging.Handler.close(self)

    def stop_sending(self) -> None:
        """Send the pending batch, then stop the sender thread and close the HTTP session."""
        try:
            self.flush()
        finally:
            SeqLogHandler.close(self)
