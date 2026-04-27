"""Structlog-based logging provider.

Configures stdlib "dictConfig", structlog processors, and optional
request-event field filtering from a "service_provider" config
envelope whose "config" section uses flat "formatters",
"handlers", and "loggers" keys.
"""

from __future__ import annotations

import copy
import importlib
import logging
import logging.config
import logging.handlers
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.services.logging.models import LogMessage, LoggingServiceProvider
from src.services.logging.service import LoggingProviderFactory


def _optional_import(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


_structlog = _optional_import("structlog")

# Register non-stdlib levels so dictConfig can resolve them by name.
_CUSTOM_LEVELS: dict[str, int] = {"TRACE": 5, "FATAL": 50}
for _name, _value in _CUSTOM_LEVELS.items():
    if not isinstance(logging.getLevelName(_name), int):
        logging.addLevelName(_value, _name)

_LEVEL_STYLES: dict[str, str] = {
    "TRC": "\033[90m",    # grey
    "DBG": "\033[34m",    # blue
    "INF": "\033[32m",    # green
    "WRN": "\033[33m",    # yellow
    "ERR": "\033[31m",    # red
    "CRT": "\033[1;31m",  # bold red
    "FTL": "\033[1;35m",  # bold magenta
}

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

    Provider "config" keys (all optional except "handlers")
    ------------------------------------------------------------
    "formatters"
        Python logging formatter definitions.
    "handlers"
        Handler definitions. A custom "active" flag (default "true")
        controls whether the handler is included; the "$ {output_dir}"
        placeholder is expanded in "filename" values.
    "loggers"
        Named stdlib logger definitions (e.g. "requests" with
        "propagate: false").
    "structlog"
        "renderer" (""console"" | ""json"") selection.
    "request_log"
        Per-field "fields" toggle for request-event logging.

    The provider auto-injects "version: 1" and
    "disable_existing_loggers: false" into the "dictConfig" dict.
    Active handlers **not** assigned to any named logger are
    automatically added to "root.handlers".
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._output_dir: Path | None = None
        self._profile: str = "dev"
        self._request_fields: dict[str, bool] = dict(_DEFAULT_REQUEST_FIELDS)
        self._is_configured: bool = False

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

        self._apply_request_log_config(self.config.get("request_log", {}))

        handlers = self.config.get("handlers")
        if isinstance(handlers, dict) and handlers:
            prepared = self._prepare_logging_config()
            self._apply_logging_config(prepared)

        structlog_config = self.config.get("structlog")
        if isinstance(structlog_config, dict):
            self._configure_structlog(structlog_config)

        self._is_configured = True

    def apply_levels(self) -> None:
        """Patch handler and logger levels from the current config without recreating handlers.

        Directly updates live stdlib ``Logger`` and ``Handler`` objects so no
        new handlers are created and no existing log records are lost.  Safe
        to call at any time after the initial :meth:`configure`.
        """
        # Resolve handler-name → desired level from config.
        handler_levels: dict[str, int] = {}
        for h_name, h_conf in (self.config.get("handlers") or {}).items():
            if not isinstance(h_conf, dict):
                continue
            level_str = h_conf.get("level")
            if not level_str:
                continue
            level_int = logging.getLevelName(str(level_str).upper())
            if isinstance(level_int, int):
                handler_levels[h_name] = level_int

        # Update levels on handlers attached to named loggers.
        # Logger-level is intentionally left at NOTSET — the handler level
        # is the sole filter gate.
        for l_name, l_conf in (self.config.get("loggers") or {}).items():
            if not isinstance(l_conf, dict):
                continue
            logger = logging.getLogger(l_name)
            config_handler_names: set[str] = set(l_conf.get("handlers", []))
            for handler in logger.handlers:
                if handler.name not in config_handler_names:
                    continue
                new_level = handler_levels.get(handler.name)
                if new_level is not None:
                    handler.setLevel(new_level)

    def reset(self) -> list[str]:
        """Reset file-backed handlers across all active stdlib loggers.

        Returns:
            list[str]: Sorted paths to active log files that were truncated.
        """
        reset_files: set[str] = set()
        for logger in self._iter_runtime_loggers():
            for handler in logger.handlers:
                file_path = self._reset_file_handler(handler)
                if file_path is not None:
                    reset_files.add(file_path)
        return sorted(reset_files)

    # ------------------------------------------------------------------
    # Runtime interface
    # ------------------------------------------------------------------

    def get_logger(self, name: str) -> Any:
        if _structlog is None:
            return logging.getLogger(name)
        return _structlog.get_logger(name)

    # Levels that stdlib/structlog silently alias to another level, losing
    # the original name before processors run.  We inject "_original_level"
    # so that _abbreviate_log_level can recover the intended abbreviation.
    _ALIASED_LEVELS: dict[str, str] = {
        "fatal": "critical",   # structlog: fatal = critical (same fn object)
        "trace": "debug",      # not a stdlib level; closest equivalent
    }

    def log(self, logger_name: str, msg: LogMessage) -> None:
        fields = dict(msg.fields)
        if msg.event in ("provider_request", "translator_request"):
            fields = self._filtered_request_log_fields(msg.event, fields)
        logger = self.get_logger(logger_name)
        level_name = logging.getLevelName(msg.level).lower()
        dispatch_level = self._ALIASED_LEVELS.get(level_name, level_name)
        if dispatch_level != level_name:
            fields = {**fields, "_original_level": level_name}
        method = getattr(logger, dispatch_level, None) or logger.info
        method(msg.event, **fields)

    def bind_request_id(self, request_id: str) -> None:
        if _structlog is not None:
            _structlog.contextvars.bind_contextvars(request_id=request_id)

    def clear_request_context(self) -> None:
        if _structlog is not None:
            _structlog.contextvars.clear_contextvars()

    def log_to_all_active_loggers(self, msg: LogMessage) -> None:
        root_logger = logging.getLogger()
        root_logger.log(msg.level, msg.event)

        manager = logging.Logger.manager
        for obj in manager.loggerDict.values():
            if not isinstance(obj, logging.Logger):
                continue
            if obj.name == "requests":
                continue
            if obj.disabled or obj.propagate or not obj.handlers:
                continue
            obj.log(msg.level, msg.event)

    # ------------------------------------------------------------------
    # Request logging
    # ------------------------------------------------------------------

    def _filtered_request_log_fields(
        self, event: str, raw: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply "request_log.fields" toggles; used by emit() for request events."""
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

    @staticmethod
    def _iter_runtime_loggers() -> list[logging.Logger]:
        loggers: list[logging.Logger] = [logging.getLogger()]
        manager = logging.Logger.manager
        for obj in manager.loggerDict.values():
            if isinstance(obj, logging.Logger):
                loggers.append(obj)
        return loggers

    @staticmethod
    def _reset_file_handler(handler: logging.Handler) -> str | None:
        if not isinstance(handler, logging.FileHandler):
            return None

        file_path = Path(handler.baseFilename)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        handler.acquire()
        try:
            stream = handler.stream
            if stream is None:
                with file_path.open("w", encoding="utf-8"):
                    pass
            else:
                stream.flush()
                stream.seek(0)
                stream.truncate(0)
        finally:
            handler.release()

        StructLogProvider._remove_rotating_backups(handler)
        return str(file_path)

    @staticmethod
    def _remove_rotating_backups(handler: logging.Handler) -> None:
        if not isinstance(handler, logging.handlers.RotatingFileHandler):
            return
        if handler.backupCount <= 0:
            return

        base_path = Path(handler.baseFilename)
        for idx in range(1, handler.backupCount + 1):
            backup_path = Path(f"{base_path}.{idx}")
            if not backup_path.exists():
                continue
            backup_path.unlink(missing_ok=True)

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
        fields = config.get("fields")
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key in self._request_fields:
                    self._request_fields[key] = bool(value)

    # ------------------------------------------------------------------
    # Internal helpers — stdlib dictConfig preparation
    # ------------------------------------------------------------------

    def _apply_logging_config(self, prepared: dict[str, Any]) -> None:
        """Apply a prepared logging config additively.

        Calls ``logging.config.dictConfig`` but then restores any root-logger
        handlers that were added by previously configured providers and are not
        duplicates of the new handlers.  This allows multiple active
        ``StructLogProvider`` instances to coexist without clobbering each
        other's file/stream handlers.
        """
        root = logging.getLogger()
        pre_handlers = list(root.handlers)

        logging.config.dictConfig(prepared)

        for handler in pre_handlers:
            if not self._handler_already_present(handler, root.handlers):
                root.addHandler(handler)

    @staticmethod
    def _handler_already_present(
        candidate: logging.Handler,
        handlers: list[logging.Handler],
    ) -> bool:
        """Return True if an equivalent handler is already in *handlers*.

        File handlers are matched by absolute filename; stream handlers by
        stream identity; all others by object identity.
        """
        for h in handlers:
            if isinstance(candidate, logging.FileHandler) or isinstance(h, logging.FileHandler):
                if (isinstance(candidate, logging.FileHandler)
                        and isinstance(h, logging.FileHandler)
                        and h.baseFilename == candidate.baseFilename):
                    return True
            elif isinstance(candidate, logging.StreamHandler) and isinstance(h, logging.StreamHandler):
                if h.stream is candidate.stream:
                    return True
            elif h is candidate:
                return True
        return False

    def _prepare_logging_config(self) -> dict[str, Any]:
        """Build an stdlib "dictConfig" dict from flat config keys.

        Reads "formatters", "handlers", and "loggers" from
        "self.config", injects "version" and
        "disable_existing_loggers", processes handler "active"
        flags / Seq availability, and auto-builds "root.handlers"
        from active handlers not assigned to any named logger.
        """
        formatters = copy.deepcopy(self.config.get("formatters") or {})
        handlers = copy.deepcopy(self.config.get("handlers") or {})
        loggers = copy.deepcopy(self.config.get("loggers") or {})

        if not isinstance(handlers, dict):
            raise ValueError("`handlers` in logging config must be an object.")

        prepared: dict[str, Any] = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": formatters if isinstance(formatters, dict) else {},
            "handlers": handlers,
            "loggers": loggers if isinstance(loggers, dict) else {},
        }

        self._resolve_file_handler_paths(handlers)
        self._disable_inactive_handlers(handlers, prepared)
        self._disable_unavailable_seq_handlers(handlers, prepared)

        assigned: set[str] = set()
        for logger_conf in prepared.get("loggers", {}).values():
            if isinstance(logger_conf, dict):
                for h_name in logger_conf.get("handlers", []):
                    assigned.add(h_name)

        root_handlers = [name for name in handlers if name not in assigned]
        if root_handlers:
            prepared["root"] = {"handlers": root_handlers, "level": "INFO"}

        return prepared

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

    # ------------------------------------------------------------------
    # Internal helpers — structlog
    # ------------------------------------------------------------------

    @staticmethod
    def _abbreviate_log_level(
        _logger: Any, _method: str, event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        _LEVEL_ABBREV: dict[str, str] = {
            "trace":    "TRC",
            "debug":    "DBG",
            "info":     "INF",
            "warning":  "WRN",
            "error":    "ERR",
            "critical": "CRT",
            "fatal":    "FTL",
        }
        # Prefer the original level injected by emit() for aliased levels
        # (e.g. "fatal" which structlog dispatches as "critical").
        level = event_dict.pop("_original_level", event_dict.get("level", ""))
        event_dict["level"] = _LEVEL_ABBREV.get(
            level.lower(), level.upper()[:3],
        )
        return event_dict

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
            renderer = _structlog.dev.ConsoleRenderer(
                colors=True, level_styles=_LEVEL_STYLES,
            )

        _structlog.configure(
            processors=[
                _structlog.contextvars.merge_contextvars,
                _structlog.stdlib.filter_by_level,
                _structlog.processors.add_log_level,
                self._abbreviate_log_level,
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
