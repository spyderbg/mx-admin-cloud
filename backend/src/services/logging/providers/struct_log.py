"""Structlog-based logging provider.

Configures stdlib "dictConfig", structlog processors, and the
dedicated request logger channel from a single "service_provider"
config envelope.
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import logging.config
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.logging.models import LoggingServiceProvider
from src.services.logging.service import LoggingProviderFactory


def _optional_import(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


_structlog = _optional_import("structlog")

_DEFAULT_REQUEST_FIELDS: dict[str, bool] = {
    "provider": True,
    "translator": True,
    "model": True,
    "actor": True,
    "voice_id": True,
    "language": True,
    "input": True,
    "response_duration": True,
    "timestamp": True,
}


# pylint: disable=too-many-instance-attributes
class StructLogProvider(LoggingServiceProvider):
    """Logging provider backed by structlog + stdlib logging.

    Provider "config" sections
    ----------------------------
    "logging"
        A stdlib "dictConfig" blob (version, formatters, handlers, root …).
        Custom extensions: "active" flag on handlers, "${output_dir}"
        path placeholder, automatic Seq availability check.
    "structlog"
        "renderer" ("console" | "json") and optional processor list.
    "request_log"
        "path" (relative to output dir) and per-field "fields" toggles
        for the dedicated request logger channel.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._output_dir: Path | None = None
        self._profile: str = "dev"
        self._requests_logger: Any = None
        self._request_log_rel_path: str = "results/requests.log"
        self._request_fields: dict[str, bool] = dict(_DEFAULT_REQUEST_FIELDS)

    @classmethod
    def register(cls) -> None:
        """Register under "struct_log" in the: class:`LoggingProviderFactory`."""
        LoggingProviderFactory.register("struct_log", cls)

    # ------------------------------------------------------------------
    # Configuration (called once at startup)
    # ------------------------------------------------------------------

    def configure(self, *, output_dir: Path, profile: str) -> None:
        self._output_dir = output_dir
        self._profile = profile

        logging_config = self.config.get("logging")
        structlog_config = self.config.get("structlog")
        request_log_config = self.config.get("request_log", {})

        self._apply_request_log_config(request_log_config)

        if isinstance(logging_config, dict):
            prepared = self._prepare_logging_config(logging_config)
            logging.config.dictConfig(prepared)

        if isinstance(structlog_config, dict):
            self._configure_structlog(structlog_config)

        self._requests_logger = (
            _structlog.get_logger("request_logger")
            if _structlog is not None
            else logging.getLogger("request_logger")
        )

    # ------------------------------------------------------------------
    # Runtime interface
    # ------------------------------------------------------------------

    def get_logger(self, name: str) -> Any:
        if _structlog is None:
            return logging.getLogger(name)
        return _structlog.get_logger(name)

    def emit(self, logger_name: str, level: str, event: str, **fields: Any) -> None:
        if (
            logger_name == "request_logger"
            and event in ("provider_request", "translator_request")
        ):
            fields = self._filtered_request_log_fields(event, fields)
            if _structlog is None:
                req_log = logging.getLogger("request_logger")
                req_log.info("%s %s", event, json.dumps(fields, ensure_ascii=False))
                return
        log = self.get_logger(logger_name)
        method = getattr(log, level.lower(), None) or log.info
        method(event, **fields)

    def bind_request_id(self, request_id: str) -> None:
        if _structlog is not None:
            _structlog.contextvars.bind_contextvars(request_id=request_id)

    def clear_request_context(self) -> None:
        if _structlog is not None:
            _structlog.contextvars.clear_contextvars()

    def log_to_all_active_loggers(
        self, message: str, level: int = logging.INFO,
    ) -> None:
        root_logger = logging.getLogger()
        root_logger.log(level, message)

        manager = logging.Logger.manager
        for obj in manager.loggerDict.values():
            if not isinstance(obj, logging.Logger):
                continue
            if obj.name == "request_logger":
                continue
            if obj.disabled or obj.propagate or not obj.handlers:
                continue
            obj.log(level, message)

    # ------------------------------------------------------------------
    # Request logging
    # ------------------------------------------------------------------

    def _filtered_request_log_fields(
        self, event: str, raw: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply ``request_log.fields`` toggles; used by :meth:`emit` for request events."""
        payload: dict[str, Any] = {}
        if event == "provider_request":
            provider = raw.get("provider")
            if provider is not None and self._field_enabled("provider"):
                payload["provider"] = provider
        else:
            translator = raw.get("translator")
            if translator is not None and self._field_enabled("translator"):
                payload["translator"] = translator
        self._apply_common_fields(
            payload,
            model=raw.get("model"),
            actor=raw.get("actor"),
            voice_id=raw.get("voice_id"),
            language=raw.get("language"),
            input_data=raw.get("input_data"),
            response_duration=raw.get("response_duration"),
        )
        return payload

    # ------------------------------------------------------------------
    # Internal helpers — request logging
    # ------------------------------------------------------------------

    def _field_enabled(self, name: str) -> bool:
        return bool(self._request_fields.get(name, True))

    def _apply_common_fields(
        self,
        payload: dict[str, Any],
        *,
        model: str | None,
        actor: str | None,
        voice_id: str | None,
        language: str | None,
        input_data: Any | None,
        response_duration: float | None,
    ) -> None:
        if model is not None and self._field_enabled("model"):
            payload["model"] = model
        if actor is not None and self._field_enabled("actor"):
            payload["actor"] = actor
        if voice_id is not None and self._field_enabled("voice_id"):
            payload["voice_id"] = voice_id
        if language is not None and self._field_enabled("language"):
            payload["language"] = language
        if input_data is not None and self._field_enabled("input"):
            payload["input"] = input_data
        if response_duration is not None and self._field_enabled("response_duration"):
            payload["response_duration"] = round(float(response_duration), 6)
        if self._field_enabled("timestamp"):
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()

    def _apply_request_log_config(self, config: dict[str, Any] | None) -> None:
        if not isinstance(config, dict):
            return
        path_value = config.get("path")
        if isinstance(path_value, str) and path_value.strip():
            rel_path = Path(path_value.strip())
            if rel_path.is_absolute():
                raise ValueError(
                    "request_log.path must be relative to the output directory.",
                )
            self._request_log_rel_path = rel_path.as_posix()

        fields = config.get("fields")
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key in self._request_fields:
                    self._request_fields[key] = bool(value)

    def _resolve_requests_log_path(self) -> Path:
        output_dir = self._output_dir or Path(".")
        return output_dir / self._request_log_rel_path

    # ------------------------------------------------------------------
    # Internal helpers — stdlib dictConfig preparation
    # ------------------------------------------------------------------

    def _prepare_logging_config(self, config: dict[str, Any]) -> dict[str, Any]:
        prepared = copy.deepcopy(config)
        handlers = prepared.setdefault("handlers", {})
        if not isinstance(handlers, dict):
            raise ValueError("`handlers` in logging config must be an object.")

        self._normalize_file_handler_paths(handlers)
        self._resolve_file_handler_paths(handlers)
        self._disable_inactive_handlers(handlers, prepared)
        self._disable_unavailable_seq_handlers(handlers, prepared)
        self._ensure_requests_handler(prepared)
        return prepared

    def _normalize_file_handler_paths(self, handlers: dict[str, Any]) -> None:
        output_dir = self._output_dir or Path(".")
        logs_root = (output_dir / "results" / "logs").resolve()
        logs_root.mkdir(parents=True, exist_ok=True)
        for handler in handlers.values():
            if not isinstance(handler, dict):
                continue
            class_name = str(handler.get("class", ""))
            if "FileHandler" not in class_name:
                continue
            filename = handler.get("filename")
            if not isinstance(filename, str) or not filename:
                continue
            path_text = filename.replace(
                "${output_dir}", str(self._output_dir or "."),
            )
            path = Path(path_text)
            handler["filename"] = str(logs_root / (path.name or "app.log"))

    def _resolve_file_handler_paths(self, handlers: dict[str, Any]) -> None:
        output_dir = self._output_dir or Path(".")
        for handler in handlers.values():
            if not isinstance(handler, dict):
                continue
            filename = handler.get("filename")
            if not isinstance(filename, str) or not filename:
                continue
            resolved = filename.replace("${output_dir}", str(output_dir))
            path = Path(resolved)
            if not path.is_absolute():
                path = (output_dir / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            handler["filename"] = str(path)

    @staticmethod
    def _disable_inactive_handlers(
        handlers: dict[str, Any], config: dict[str, Any],
    ) -> None:
        disabled_names: list[str] = []
        for name, handler in list(handlers.items()):
            if not isinstance(handler, dict):
                continue
            active = handler.get("active", True)
            if active is False:
                disabled_names.append(name)
            else:
                handler.pop("active", None)
        for name in disabled_names:
            handlers.pop(name, None)
            StructLogProvider._remove_handler_references(config, name)

    def _disable_unavailable_seq_handlers(
        self, handlers: dict[str, Any], config: dict[str, Any],
    ) -> None:
        disabled_names: list[str] = []
        for name, handler in handlers.items():
            if not isinstance(handler, dict):
                continue
            class_name = str(handler.get("class", ""))
            if "seqlog." not in class_name:
                continue
            if not self._import_available("seqlog"):
                disabled_names.append(name)
                continue
            server_url = handler.get("server_url")
            if isinstance(server_url, str) and not self._seq_server_reachable(
                server_url,
            ):
                disabled_names.append(name)

        for name in disabled_names:
            handlers.pop(name, None)
            self._remove_handler_references(config, name)

        if any(
            isinstance(h, dict) and "seqlog." in str(h.get("class", ""))
            for h in handlers.values()
        ):
            self._enable_seq_ignore_submission_errors()

    @staticmethod
    def _remove_handler_references(
        config: dict[str, Any], handler_name: str,
    ) -> None:
        root = config.get("root")
        if isinstance(root, dict):
            root["handlers"] = [
                h for h in root.get("handlers", []) if h != handler_name
            ]
        loggers = config.get("loggers")
        if isinstance(loggers, dict):
            for logger_conf in loggers.values():
                if isinstance(logger_conf, dict):
                    logger_conf["handlers"] = [
                        h
                        for h in logger_conf.get("handlers", [])
                        if h != handler_name
                    ]

    def _ensure_requests_handler(self, config: dict[str, Any]) -> None:
        handlers = config.setdefault("handlers", {})
        formatters = config.setdefault("formatters", {})
        loggers = config.setdefault("loggers", {})
        if (
            not isinstance(handlers, dict)
            or not isinstance(formatters, dict)
            or not isinstance(loggers, dict)
        ):
            raise ValueError("Invalid logging config structure.")

        requests_path = self._resolve_requests_log_path().resolve()
        requests_path.parent.mkdir(parents=True, exist_ok=True)
        formatters.setdefault("plain", {"format": "%(message)s"})
        handlers.setdefault(
            "requests_file",
            {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(requests_path),
                "maxBytes": 10_485_760,
                "backupCount": 5,
                "formatter": "plain",
                "level": "INFO",
            },
        )
        loggers.setdefault(
            "request_logger",
            {
                "handlers": ["requests_file"],
                "level": "INFO",
                "propagate": False,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers — structlog
    # ------------------------------------------------------------------

    def _configure_structlog(self, structlog_config: dict[str, Any]) -> None:
        if _structlog is None:
            return
        default_renderer = "console" if self._profile == "dev" else "json"
        renderer_name = str(
            structlog_config.get("renderer", default_renderer),
        ).lower()
        if renderer_name == "json":
            renderer = _structlog.processors.JSONRenderer()
        else:
            renderer = _structlog.dev.ConsoleRenderer(colors=True)

        _structlog.configure(
            processors=[
                _structlog.contextvars.merge_contextvars,
                _structlog.stdlib.filter_by_level,
                _structlog.processors.add_log_level,
                _structlog.processors.TimeStamper(fmt="iso", utc=True),
                _structlog.processors.StackInfoRenderer(),
                _structlog.processors.format_exc_info,
                renderer,
            ],
            context_class=dict,
            logger_factory=_structlog.stdlib.LoggerFactory(),
            wrapper_class=_structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers — environment probing
    # ------------------------------------------------------------------

    @staticmethod
    def _import_available(module_name: str) -> bool:
        try:
            importlib.import_module(module_name)
        except Exception:  # pylint: disable=broad-exception-caught
            return False
        return True

    @staticmethod
    def _seq_server_reachable(server_url: str, timeout: float = 2.0) -> bool:
        if not server_url:
            return False
        url = server_url.rstrip("/") + "/api"
        try:
            req = urllib.request.Request(url, method="GET")
            urllib.request.urlopen(req, timeout=timeout)  # noqa: S310  # pylint: disable=consider-using-with
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    @staticmethod
    def _enable_seq_ignore_submission_errors() -> None:
        try:
            feature_flags = importlib.import_module("seqlog.feature_flags")
            feature_flag = getattr(feature_flags, "FeatureFlag")
            enable_feature = getattr(feature_flags, "enable_feature")
            enable_feature(feature_flag.IGNORE_SEQ_SUBMISSION_ERRORS)
        except Exception:  # pylint: disable=broad-exception-caught
            pass


StructLogProvider.register()
