"""Unit tests for LoggingService.emit(): dispatch per provider strategy (ALL_PROVIDERS / FIRST_SUCCESS)."""

# pylint: disable=missing-function-docstring,too-few-public-methods

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from src.services.logging.models import LogLevel, LogMessage, LoggingServiceProvider
from src.services.logging.service import LoggingService
from src.services.provider_strategy import ProviderSelectionStrategy


class _StubLoggingProvider(LoggingServiceProvider):
    """Minimal logging provider that records calls and optionally raises."""

    def __init__(
            self,
            *,
            name: str,
            priority: int = 10,
            category: str = "",
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


def _make_service(
        providers: list[LoggingServiceProvider],
        strategy: ProviderSelectionStrategy | None = None,
) -> LoggingService:
    service = LoggingService(
        service_name="logging",
        package_name="tests",
        service_dir=Path("."),
        base_dir=Path("."),
    )
    service.replace_providers(providers)
    if strategy is not None:
        service.set_provider_strategy(strategy)
    return service


_MSG = LogMessage(event="test_event", level=LogLevel.INFO)


_ALL = ProviderSelectionStrategy.ALL_PROVIDERS
_FIRST = ProviderSelectionStrategy.FIRST_SUCCESS


def test_logging_defaults_to_all_providers() -> None:
    assert _make_service([]).provider_strategy is _ALL


@pytest.mark.parametrize("strategy", [_ALL, _FIRST])
def test_emit_ignores_providers_with_category(strategy: ProviderSelectionStrategy) -> None:
    requests_prov = _StubLoggingProvider(name="requests", priority=1, category="requests")
    no_category_prov = _StubLoggingProvider(name="default", priority=10, category="")
    service = _make_service([requests_prov, no_category_prov], strategy)

    service.emit(_MSG)

    assert len(no_category_prov.logged) == 1
    assert len(requests_prov.logged) == 0


@pytest.mark.parametrize("strategy", [_ALL, _FIRST])
def test_emit_noop_without_uncategorized_active_providers(strategy: ProviderSelectionStrategy) -> None:
    disabled = _StubLoggingProvider(name="off", priority=1, enabled=False)
    special = _StubLoggingProvider(name="special", priority=1, category="requests")
    service = _make_service([disabled, special], strategy)

    service.emit(_MSG)  # should not raise

    assert not disabled.logged
    assert not special.logged


@pytest.mark.parametrize("strategy", [_ALL, _FIRST])
def test_emit_never_raises_when_every_provider_fails(strategy: ProviderSelectionStrategy) -> None:
    fail1 = _StubLoggingProvider(name="f1", priority=1, should_fail=True)
    fail2 = _StubLoggingProvider(name="f2", priority=5, should_fail=True)
    service = _make_service([fail1, fail2], strategy)

    service.emit(_MSG)  # should not raise


# ALL_PROVIDERS ----------------------------------------------------------------

def test_emit_all_providers_logs_through_every_active_provider() -> None:
    high = _StubLoggingProvider(name="high", priority=1)
    low = _StubLoggingProvider(name="low", priority=10)
    peer = _StubLoggingProvider(name="peer", priority=10)
    service = _make_service([low, high, peer], _ALL)

    service.emit(_MSG)

    assert [len(p.logged) for p in (high, low, peer)] == [1, 1, 1]
    assert high.logged[0] == ("high", _MSG)


def test_emit_all_providers_keeps_going_past_a_failing_provider() -> None:
    ok1 = _StubLoggingProvider(name="ok1", priority=1)
    fail = _StubLoggingProvider(name="fail", priority=5, should_fail=True)
    ok2 = _StubLoggingProvider(name="ok2", priority=10)
    service = _make_service([ok1, fail, ok2], _ALL)

    service.emit(_MSG)

    assert len(ok1.logged) == 1
    assert len(fail.logged) == 0
    assert len(ok2.logged) == 1


# FIRST_SUCCESS ----------------------------------------------------------------

def test_emit_first_success_uses_only_the_highest_priority_provider() -> None:
    high = _StubLoggingProvider(name="high", priority=1)
    low = _StubLoggingProvider(name="low", priority=10)
    service = _make_service([low, high], _FIRST)

    service.emit(_MSG)

    assert len(high.logged) == 1
    assert len(low.logged) == 0


def test_emit_first_success_falls_back_in_priority_order() -> None:
    fail_a = _StubLoggingProvider(name="fail_a", priority=1, should_fail=True)
    fail_b = _StubLoggingProvider(name="fail_b", priority=2, should_fail=True)
    fallback = _StubLoggingProvider(name="fallback", priority=5)
    after = _StubLoggingProvider(name="after", priority=9)
    service = _make_service([after, fail_a, fallback, fail_b], _FIRST)

    service.emit(_MSG)

    assert len(fallback.logged) == 1
    assert len(after.logged) == 0
