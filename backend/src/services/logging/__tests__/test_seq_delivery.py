"""Seq delivery through ``StructLogProvider`` and the 'service' logger endpoint.

Seq is replaced by a recording ``publish_log_batch``; nothing leaves the process.
"""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_internal
from src.services.logging import routes as logging_routes
from src.services.logging.models import LogLevel, LogMessage
from src.services.logging.providers.struct_log import StructLogProvider
from src.services.logging.providers.struct_log import provider as struct_log_provider
from src.services.logging.providers.struct_log.seq_handler import SeqHandler
from src.services.logging.service import LoggingService

_SEQ_URL = "http://seq.test:5341"


def _wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the messages each SeqHandler would post to Seq."""
    messages: list[str] = []

    def _publish(_handler: SeqHandler, batch: list[logging.LogRecord]) -> None:
        messages.extend(record.getMessage() for record in batch)

    monkeypatch.setattr(SeqHandler, "publish_log_batch", _publish)
    return messages


@pytest.fixture
def isolated_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Treat Seq as reachable, start with no handler owners and undo the providers' changes to stdlib logging."""
    monkeypatch.setattr(StructLogProvider, "_seq_server_reachable", staticmethod(lambda _url, _timeout=2.0: True))
    monkeypatch.setattr(struct_log_provider, "_HANDLER_OWNERS", weakref.WeakKeyDictionary())
    before = {
        logger: (list(logger.handlers), logger.propagate, logger.level)
        for logger in StructLogProvider._iter_runtime_loggers()
    }
    pre_existing = {id(handler) for handlers, _, _ in before.values() for handler in handlers}
    yield
    for logger in StructLogProvider._iter_runtime_loggers():
        for handler in list(logger.handlers):
            if isinstance(handler, SeqHandler) and id(handler) not in pre_existing:
                handler.stop_sending()
        handlers, propagate, level = before.get(logger, ([], True, logging.NOTSET))
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.setLevel(level)


def _seq_handler_config() -> dict[str, Any]:
    return {"class": "seqlog.structured_logging.SeqLogHandler", "level": "INFO", "server_url": _SEQ_URL}


def _provider(tmp_path: Path, name: str, handlers: dict[str, Any], category: str = "") -> StructLogProvider:
    return StructLogProvider(
        name=name,
        service_name="logging",
        provider_name="struct_log",
        provider_category=category,
        enabled=True,
        priority=1,
        config={"handlers": handlers},
        config_path=tmp_path / f"{name}.json",
        base_dir=tmp_path,
    )


def _seq_handlers(logger: logging.Logger) -> list[SeqHandler]:
    return [handler for handler in logger.handlers if isinstance(handler, SeqHandler)]


def test_seq_probe_counts_an_error_status_as_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _answer_404(request: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", _answer_404)

    assert StructLogProvider._seq_server_reachable(_SEQ_URL) is True


def test_seq_probe_reports_a_server_that_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(request: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)

    assert StructLogProvider._seq_server_reachable(_SEQ_URL) is False


def test_seq_handler_keeps_sending_after_close(sent: list[str]) -> None:
    handler = SeqHandler(_SEQ_URL, auto_flush_timeout=0.05)
    try:
        handler.close()
        handler.handle(logging.makeLogRecord({"msg": "after-close", "levelno": logging.INFO}))

        assert _wait_until(lambda: "after-close" in sent)
        assert handler.consumer.is_running
    finally:
        handler.stop_sending()


def test_seq_handler_sends_a_single_record_without_waiting_for_a_full_batch(sent: list[str]) -> None:
    handler = SeqHandler(_SEQ_URL)
    try:
        handler.handle(logging.makeLogRecord({"msg": "lonely", "levelno": logging.INFO}))

        assert _wait_until(lambda: "lonely" in sent)
    finally:
        handler.stop_sending()


@pytest.mark.usefixtures("isolated_logging")
def test_every_provider_keeps_delivering_to_seq(tmp_path: Path, sent: list[str]) -> None:
    default = _provider(tmp_path, "default", {"seq": _seq_handler_config()})
    service = _provider(tmp_path, "service", {"service_seq": _seq_handler_config()}, category="service")

    default.configure(output_dir=tmp_path, profile="dev")
    # dictConfig here closes every existing handler, the default provider's Seq handler included.
    service.configure(output_dir=tmp_path, profile="dev")
    logging.getLogger().warning("root-event")
    logging.getLogger("service").warning("service-event")

    assert _wait_until(lambda: {"root-event", "service-event"} <= set(sent))
    assert len(_seq_handlers(logging.getLogger())) == 1
    assert len(_seq_handlers(logging.getLogger("service"))) == 1


@pytest.mark.usefixtures("isolated_logging")
def test_a_reloaded_provider_replaces_and_stops_its_predecessors_seq_handler(tmp_path: Path) -> None:
    _provider(tmp_path, "default", {"seq": _seq_handler_config()}).configure(output_dir=tmp_path, profile="dev")
    [replaced] = _seq_handlers(logging.getLogger())

    # A registry reload builds a new provider object with the same name.
    _provider(tmp_path, "default", {"seq": _seq_handler_config()}).configure(output_dir=tmp_path, profile="dev")
    [current] = _seq_handlers(logging.getLogger())

    assert current is not replaced
    assert _wait_until(lambda: not replaced.consumer.is_running)
    assert current.consumer.is_running


@pytest.mark.usefixtures("isolated_logging")
def test_switching_a_handler_off_removes_it(tmp_path: Path) -> None:
    _provider(tmp_path, "default", {"seq": _seq_handler_config()}).configure(output_dir=tmp_path, profile="dev")
    [replaced] = _seq_handlers(logging.getLogger())

    inactive = {**_seq_handler_config(), "active": False}
    _provider(tmp_path, "default", {"seq": inactive}).configure(output_dir=tmp_path, profile="dev")

    assert not _seq_handlers(logging.getLogger())
    assert _wait_until(lambda: not replaced.consumer.is_running)


def _file_handler_config(path: Path) -> dict[str, Any]:
    return {"class": "logging.FileHandler", "level": "INFO", "filename": str(path), "encoding": "utf-8"}


def _trace_provider(tmp_path: Path) -> StructLogProvider:
    return _provider(tmp_path, "trace", {
        "file": _file_handler_config(tmp_path / "trace.log"),
        "seq": {**_seq_handler_config(), "active": False},
    })


def _file_names(logger: logging.Logger, tmp_path: Path) -> list[str]:
    return [
        Path(handler.baseFilename).name
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).parent == tmp_path
    ]


@pytest.mark.usefixtures("isolated_logging")
def test_providers_may_reuse_handler_names(tmp_path: Path, sent: list[str]) -> None:
    # Shaped like default.json and trace.json, which both name their handlers "file" and "seq";
    # trace's "seq" is inactive. Configured in registry order: trace comes after default.
    _provider(tmp_path, "default", {
        "file": _file_handler_config(tmp_path / "app.log"),
        "seq": _seq_handler_config(),
    }).configure(output_dir=tmp_path, profile="dev")
    _trace_provider(tmp_path).configure(output_dir=tmp_path, profile="dev")
    [default_seq] = _seq_handlers(logging.getLogger())

    # A registry reload of trace leaves the default provider's handlers alone too.
    _trace_provider(tmp_path).configure(output_dir=tmp_path, profile="dev")
    logging.getLogger().warning("root-event")
    logging.getLogger("trace").warning("trace-event")

    assert _seq_handlers(logging.getLogger()) == [default_seq]
    assert default_seq.consumer.is_running
    assert not _seq_handlers(logging.getLogger("trace"))
    assert _file_names(logging.getLogger(), tmp_path) == ["app.log"]
    assert _file_names(logging.getLogger("trace"), tmp_path) == ["trace.log"]
    assert "root-event" in (tmp_path / "app.log").read_text(encoding="utf-8")
    assert (tmp_path / "trace.log").read_text(encoding="utf-8").strip() == "trace-event"
    assert _wait_until(lambda: "root-event" in sent)


def _logging_client(service: LoggingService) -> TestClient:
    app = FastAPI()
    app.include_router(logging_routes.router)
    app.dependency_overrides[require_internal] = lambda: {"username": "test", "role": "internal"}
    app.dependency_overrides[logging_routes.get_logging_service] = lambda: service
    return TestClient(app)


def _logging_service(tmp_path: Path, *providers: StructLogProvider) -> LoggingService:
    service = LoggingService(service_name="logging", package_name="tests", service_dir=tmp_path, base_dir=tmp_path)
    service.replace_providers(list(providers))
    return service


def test_service_log_records_the_service_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(tmp_path, "service", {}, category="service")
    logged: list[tuple[str, LogMessage]] = []
    monkeypatch.setattr(provider, "log", lambda name, msg: logged.append((name, msg)))
    client = _logging_client(_logging_service(tmp_path, provider))

    response = client.post(
        "/internal/loggers/logging/log/service",
        json={"event": "deployed", "level": "warning", "service": "cli", "fields": {"version": "1.2"}},
    )

    assert response.status_code == 200
    assert logged == [
        ("service", LogMessage(event="deployed", level=LogLevel.WARNING, fields={"version": "1.2", "service": "cli"})),
    ]


def test_service_log_answers_404_without_a_service_logger(tmp_path: Path) -> None:
    client = _logging_client(_logging_service(tmp_path))

    response = client.post("/internal/loggers/logging/log/service", json={"event": "deployed", "service": "cli"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Logger 'service' not found."
