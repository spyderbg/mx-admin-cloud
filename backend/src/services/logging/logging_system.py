"""Centralized logging configuration for console, file, and Seq sinks."""

from __future__ import annotations

import copy
import importlib
import json
import logging
import logging.config
import urllib.request
from pathlib import Path
from typing import Any

from .request_logger import configure_request_logger, resolve_requests_log_path


def _optional_import(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except Exception: # pylint: disable=broad-exception-caught - optional dependency fallback
        return None


structlog = _optional_import("structlog")


class LoggingSystem:
    """Configure stdlib logging and structlog from JSON config files."""

    def __init__(self, *, base_dir: Path, output_dir: Path, config_path: Path, profile: str = "dev") -> None:
        self.base_dir = base_dir
        self.output_dir = output_dir
        self.config_path = config_path
        self.profile = profile

    def configure(self) -> None:
        """Configure the logging system."""
        loaded = self._load_config(self.config_path)
        logging_config, structlog_config, request_logger_config = self._split_config(loaded)
        configure_request_logger(request_logger_config)
        prepared = self._prepare_logging_config(logging_config)
        logging.config.dictConfig(prepared)
        self._configure_structlog(structlog_config)

    def _load_config(self, path: Path) -> dict[str, Any]:
        if path.is_dir():
            return self._load_config_dir(path)
        return self._load_json(path)

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Logging config file not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"Logging config must be a JSON object: {path}")
        return loaded

    def _load_config_dir(self, config_dir: Path) -> dict[str, Any]:
        if not config_dir.exists():
            raise FileNotFoundError(f"Logging config directory not found: {config_dir}")

        merged: dict[str, Any] = {}
        for config_path in sorted(config_dir.glob("*.json")):
            loaded = self._load_json(config_path)
            section_name = config_path.stem.strip()
            if section_name:
                merged[section_name] = loaded
        if not merged:
            raise ValueError(f"No logging config JSON files found in: {config_dir}")
        return merged

    def _split_config(self, loaded: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if "version" in loaded:
            request_logger_config = loaded.get("request_logger", {})
            if not isinstance(request_logger_config, dict):
                raise ValueError("`request_logger` config must be an object.")
            return loaded, {}, request_logger_config
        logging_config = loaded.get("logging")
        if not isinstance(logging_config, dict):
            raise ValueError("Logging config JSON must contain either a top-level `version` or `logging` object.")
        structlog_config = loaded.get("structlog", {})
        if not isinstance(structlog_config, dict):
            raise ValueError("`structlog` config must be an object.")
        request_logger_config = loaded.get("request_logger", {})
        if not isinstance(request_logger_config, dict):
            raise ValueError("`request_logger` config must be an object.")
        return logging_config, structlog_config, request_logger_config

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
        logs_root = (self.output_dir / "results" / "logs").resolve()
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

            path_text = filename.replace("${output_dir}", str(self.output_dir))
            path = Path(path_text)
            log_name = path.name or "app.log"
            handler["filename"] = str(logs_root / log_name)

    def _resolve_file_handler_paths(self, handlers: dict[str, Any]) -> None:
        for handler in handlers.values():
            if not isinstance(handler, dict):
                continue
            filename = handler.get("filename")
            if not isinstance(filename, str) or not filename:
                continue
            resolved = filename.replace("${output_dir}", str(self.output_dir))
            path = Path(resolved)
            if not path.is_absolute():
                path = (self.output_dir / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            handler["filename"] = str(path)

    def _disable_inactive_handlers(self, handlers: dict[str, Any], config: dict[str, Any]) -> None:
        """Remove handlers with "active": false and strip "active" from the rest for dictConfig."""
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
            self._remove_handler_references(config, name)

    def _disable_unavailable_seq_handlers(self, handlers: dict[str, Any], config: dict[str, Any]) -> None:
        """Remove Seq handlers when seqlog is missing or server is unreachable."""
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
            if isinstance(server_url, str) and not self._seq_server_reachable(server_url):
                disabled_names.append(name)

        for name in disabled_names:
            handlers.pop(name, None)
            self._remove_handler_references(config, name)

        if any(
            isinstance(h, dict) and "seqlog." in str(h.get("class", ""))
            for h in handlers.values()
        ):
            self._enable_seq_ignore_submission_errors()

    def _remove_handler_references(self, config: dict[str, Any], handler_name: str) -> None:
        root = config.get("root")
        if isinstance(root, dict):
            root["handlers"] = [h for h in root.get("handlers", []) if h != handler_name]

        loggers = config.get("loggers")
        if isinstance(loggers, dict):
            for logger_conf in loggers.values():
                if isinstance(logger_conf, dict):
                    logger_conf["handlers"] = [h for h in logger_conf.get("handlers", []) if h != handler_name]

    def _ensure_requests_handler(self, config: dict[str, Any]) -> None:
        handlers = config.setdefault("handlers", {})
        formatters = config.setdefault("formatters", {})
        loggers = config.setdefault("loggers", {})
        if not isinstance(handlers, dict) or not isinstance(formatters, dict) or not isinstance(loggers, dict):
            raise ValueError("Invalid logging config structure.")

        requests_path = resolve_requests_log_path(self.output_dir).resolve()
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
            "services.requests",
            {
                "handlers": ["requests_file"],
                "level": "INFO",
                "propagate": False,
            },
        )

    def _configure_structlog(self, structlog_config: dict[str, Any]) -> None:
        if structlog is None:
            return
        renderer_name = str(structlog_config.get("renderer", "console" if self.profile == "dev" else "json")).lower()
        if renderer_name == "json":
            renderer = structlog.processors.JSONRenderer()
        else:
            renderer = structlog.dev.ConsoleRenderer(colors=True)

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.filter_by_level,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                renderer,
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    @staticmethod
    def _import_available(module_name: str) -> bool:
        try:
            importlib.import_module(module_name)
        except Exception:  # pragma: no cover - optional dependency fallback
            return False
        return True

    @staticmethod
    def _seq_server_reachable(server_url: str, timeout: float = 2.0) -> bool:
        """Return True if the Seq server at server_url is reachable."""
        if not server_url:
            return False
        url = server_url.rstrip("/") + "/api"
        try:
            req = urllib.request.Request(url, method="GET")
            urllib.request.urlopen(req, timeout=timeout)
            return True
        except Exception:  # pragma: no cover - optional dependency fallback
            return False

    @staticmethod
    def _enable_seq_ignore_submission_errors() -> None:
        """Enable seqlog to ignore submission errors so we never throw when Seq is down."""
        try:
            feature_flags = importlib.import_module("seqlog.feature_flags")
            feature_flag = getattr(feature_flags, "FeatureFlag")
            enable_feature = getattr(feature_flags, "enable_feature")
            enable_feature(feature_flag.IGNORE_SEQ_SUBMISSION_ERRORS)
        except Exception:  # pragma: no cover - optional dependency fallback
            pass


def bind_request_id(request_id: str) -> None:
    """Bind the request ID to the context."""
    if structlog is None:
        return
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    """Clear the request context."""
    if structlog is None:
        return
    structlog.contextvars.clear_contextvars()


def get_logger(name: str):
    """Get a logger by name."""
    if structlog is None:
        return logging.getLogger(name)
    return structlog.get_logger(name)


def log_to_all_active_loggers(message: str, level: int = logging.INFO) -> None:
    """Emit through root and standalone active named loggers.

    Root emission covers all propagating loggers.
    We additionally emit to non-propagating loggers that have handlers.
    """
    root_logger = logging.getLogger()
    root_logger.log(level, message)

    manager = logging.Logger.manager
    for obj in manager.loggerDict.values():
        if not isinstance(obj, logging.Logger):
            continue
        if obj.name == "services.requests":
            continue
        if obj.disabled:
            continue
        if obj.propagate:
            continue
        if not obj.handlers:
            continue
        obj.log(level, message)
