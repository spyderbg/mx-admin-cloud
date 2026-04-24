"""Unit tests for LoggingService.emit() priority-ordered fallback logic."""

# pylint: disable=missing-function-docstring,too-few-public-methods

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.services.logging.models import LogMessage, LoggingServiceProvider
from src.services.logging.service import LoggingService


class _StubLoggingProvider(LoggingServiceProvider):
    """Minimal logging provider that records calls and optionally raises."""

    def __init__(
            self,
            *,
            name: str,
            priority: int = 10,
            category: str = "normal",
            enabled: bool = True,
            should_fail: bool = False,
            base_dir: Path | None = None,
    ) -> None:
        _dir = base_dir or Path(".")
        super().__init__(
            name=name,
            service_name="logging",
            provider_name="stub",
            provider_category=category,
            enabled=enabled,
            priority=priority,
            config={},
            config_path=_dir / f"{name}.json",
            base_dir=_dir,
        )
        self.logged: list[tuple[str, LogMessage]] = []
        self.should_fail = should_fail

    def configure(self, *, output_dir: Path, profile: str) -> None:
        pass

    def reset(self) -> list[str]:
        return []

    def get_logger(self, name: str) -> Any:
        return logging.getLogger(name)

    def log(self, logger_name: str, msg: LogMessage) -> None:
        if self.should_fail:
            raise RuntimeError(f"Simulated failure in {self.name}")
        self.logged.append((logger_name, msg))

    def bind_request_id(self, request_id: str) -> None:
        pass

    def clear_request_context(self) -> None:
        pass

    def log_to_all_active_loggers(self, msg: LogMessage) -> None:
        pass


def _make_service(providers: list[LoggingServiceProvider]) -> LoggingService:
    service = LoggingService(
        service_name="logging",
        package_name="tests",
        service_dir=Path("."),
        base_dir=Path("."),
    )
    service.replace_providers(providers)
    return service


_MSG = LogMessage(event="test_event", level=logging.INFO)


def test_emit_ignores_non_normal_category() -> None:
    requests_prov = _StubLoggingProvider(name="requests", priority=1, category="requests")
    normal_prov = _StubLoggingProvider(name="default", priority=10, category="normal")
    service = _make_service([requests_prov, normal_prov])

    service.emit(_MSG)

    assert len(normal_prov.logged) == 1
    assert len(requests_prov.logged) == 0


def test_emit_tries_highest_priority_first() -> None:
    high = _StubLoggingProvider(name="high", priority=1)
    low = _StubLoggingProvider(name="low", priority=10)
    service = _make_service([low, high])

    service.emit(_MSG)

    assert len(high.logged) == 1
    assert len(low.logged) == 0


def test_emit_falls_back_on_failure() -> None:
    failing = _StubLoggingProvider(name="failing", priority=1, should_fail=True)
    fallback = _StubLoggingProvider(name="fallback", priority=5)
    service = _make_service([failing, fallback])

    service.emit(_MSG)

    assert len(failing.logged) == 0
    assert len(fallback.logged) == 1


def test_emit_logs_to_all_same_priority_peers_on_success() -> None:
    p1 = _StubLoggingProvider(name="a", priority=3)
    p2 = _StubLoggingProvider(name="b", priority=3)
    p3 = _StubLoggingProvider(name="c", priority=3)
    service = _make_service([p1, p2, p3])

    service.emit(_MSG)

    assert len(p1.logged) == 1
    assert len(p2.logged) == 1
    assert len(p3.logged) == 1


def test_emit_does_not_reach_lower_priority_after_success() -> None:
    high = _StubLoggingProvider(name="high", priority=1)
    low = _StubLoggingProvider(name="low", priority=10)
    service = _make_service([high, low])

    service.emit(_MSG)

    assert len(high.logged) == 1
    assert len(low.logged) == 0


def test_emit_skips_failing_peer_but_logs_others() -> None:
    ok1 = _StubLoggingProvider(name="ok1", priority=5)
    fail = _StubLoggingProvider(name="fail", priority=5, should_fail=True)
    ok2 = _StubLoggingProvider(name="ok2", priority=5)
    service = _make_service([ok1, fail, ok2])

    service.emit(_MSG)

    assert len(ok1.logged) == 1
    assert len(fail.logged) == 0
    assert len(ok2.logged) == 1


def test_emit_noop_when_no_active_providers() -> None:
    disabled = _StubLoggingProvider(name="off", priority=1, enabled=False)
    service = _make_service([disabled])

    service.emit(_MSG)  # should not raise


def test_emit_noop_when_no_normal_category() -> None:
    prov = _StubLoggingProvider(name="special", priority=1, category="requests")
    service = _make_service([prov])

    service.emit(_MSG)

    assert len(prov.logged) == 0


def test_emit_falls_through_entire_priority_group_on_all_fail() -> None:
    fail_a = _StubLoggingProvider(name="fail_a", priority=1, should_fail=True)
    fail_b = _StubLoggingProvider(name="fail_b", priority=1, should_fail=True)
    fallback = _StubLoggingProvider(name="fallback", priority=5)
    service = _make_service([fail_a, fail_b, fallback])

    service.emit(_MSG)

    assert len(fail_a.logged) == 0
    assert len(fail_b.logged) == 0
    assert len(fallback.logged) == 1


def test_emit_all_groups_fail_silently() -> None:
    fail1 = _StubLoggingProvider(name="f1", priority=1, should_fail=True)
    fail2 = _StubLoggingProvider(name="f2", priority=5, should_fail=True)
    service = _make_service([fail1, fail2])

    service.emit(_MSG)  # should not raise
