"""Unit tests for provider selection strategies: dispatch helpers, the stored strategies file,
the Service contract and the registry applying the stored strategy."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.services.logging.service import LoggingService
from src.services.provider_strategy import (
    STRATEGIES_FILE,
    AllProvidersFailedError,
    InvalidStrategiesFileError,
    ProviderSelectionStrategy,
    dispatch_each,
    first_success,
    read_strategies,
)
from src.services.service_registry import ServiceRegistry, ServiceRegistryBuilder
from src.services.text_to_speech.service import TextToSpeechService

ALL = ProviderSelectionStrategy.ALL_PROVIDERS
FIRST = ProviderSelectionStrategy.FIRST_SUCCESS


@dataclass
class _Provider:
    name: str
    outcome: str = "ok"  # "ok", "fail" (unsuccessful result) or "raise"


async def _attempt(provider: _Provider) -> tuple[str, bool]:
    if provider.outcome == "raise":
        raise RuntimeError("down")
    return provider.name, provider.outcome == "ok"


def _failure(result: tuple[str, bool]) -> str | None:
    return None if result[1] else "unsuccessful"


# first_success -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_success_returns_the_first_successful_result() -> None:
    result = await first_success([_Provider("a"), _Provider("b")], _attempt, _failure)

    assert result == ("a", True)


@pytest.mark.asyncio
async def test_first_success_moves_past_exceptions_and_unsuccessful_results() -> None:
    providers = [_Provider("a", "raise"), _Provider("b", "fail"), _Provider("c"), _Provider("d")]

    assert await first_success(providers, _attempt, _failure) == ("c", True)


@pytest.mark.asyncio
async def test_first_success_reports_every_error_when_all_fail() -> None:
    with pytest.raises(AllProvidersFailedError) as excinfo:
        await first_success([_Provider("a", "raise"), _Provider("b", "fail")], _attempt, _failure)

    assert excinfo.value.errors == ["a: down", "b: unsuccessful"]
    assert str(excinfo.value) == "a: down; b: unsuccessful"


@pytest.mark.asyncio
async def test_first_success_without_providers() -> None:
    with pytest.raises(AllProvidersFailedError, match="No provider succeeded"):
        await first_success([], _attempt, _failure)


# dispatch_each -------------------------------------------------------------------

def _call_recorder(calls: list[str]):
    def call(provider: _Provider) -> None:
        calls.append(provider.name)
        if provider.outcome != "ok":
            raise RuntimeError("down")
    return call


def test_dispatch_each_all_providers_calls_every_provider_and_reports_failures() -> None:
    calls: list[str] = []

    errors = dispatch_each(ALL, [_Provider("a"), _Provider("b", "raise"), _Provider("c")], _call_recorder(calls))

    assert calls == ["a", "b", "c"]
    assert errors == ["b: down"]


def test_dispatch_each_first_success_stops_after_the_first_call_that_works() -> None:
    calls: list[str] = []

    errors = dispatch_each(FIRST, [_Provider("a", "raise"), _Provider("b"), _Provider("c")], _call_recorder(calls))

    assert calls == ["a", "b"]
    assert errors == ["a: down"]


@pytest.mark.parametrize("strategy", [ALL, FIRST])
def test_dispatch_each_never_raises(strategy: ProviderSelectionStrategy) -> None:
    assert dispatch_each(strategy, [_Provider("a", "raise")], _call_recorder([])) == ["a: down"]


# read_strategies -----------------------------------------------------------------

def test_read_strategies_without_a_file(tmp_path: Path) -> None:
    assert not read_strategies(tmp_path)


def test_read_strategies_returns_enum_values(tmp_path: Path) -> None:
    (tmp_path / STRATEGIES_FILE).write_text(json.dumps({"logging": "FIRST_SUCCESS", "text_to_speech": "FIRST_SUCCESS"}))

    assert read_strategies(tmp_path) == {"logging": FIRST, "text_to_speech": FIRST}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", json.dumps({"logging": "SOMETIMES"})])
def test_read_strategies_rejects_a_broken_file(tmp_path: Path, content: str) -> None:
    (tmp_path / STRATEGIES_FILE).write_text(content)

    with pytest.raises(InvalidStrategiesFileError):
        read_strategies(tmp_path)


# Service contract ----------------------------------------------------------------

def _logging_service() -> LoggingService:
    return LoggingService(service_name="logging", package_name="tests", service_dir=Path("."), base_dir=Path("."))


def test_resolve_provider_strategy_keeps_supported_values_and_defaults_otherwise() -> None:
    assert LoggingService.resolve_provider_strategy("FIRST_SUCCESS") is FIRST
    assert LoggingService.resolve_provider_strategy(None) is ALL
    assert TextToSpeechService.resolve_provider_strategy("ALL_PROVIDERS") is FIRST


def test_set_provider_strategy_switches_between_supported_strategies() -> None:
    service = _logging_service()

    service.set_provider_strategy(FIRST)
    assert service.provider_strategy is FIRST
    service.set_provider_strategy("ALL_PROVIDERS")
    assert service.provider_strategy is ALL


def test_set_provider_strategy_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="does not support"):
        _logging_service().set_provider_strategy("SOMETIMES")


# Registry ------------------------------------------------------------------------

def _builder(config_root: Path) -> tuple[ServiceRegistry, ServiceRegistryBuilder]:
    registry = ServiceRegistry.build_runtime(config_root)
    return registry, ServiceRegistryBuilder(registry, config_root, config_root)


def _strategies(registry: ServiceRegistry) -> dict[str, ProviderSelectionStrategy | None]:
    return {name: service.provider_strategy for name, service in registry.get_registered_services().items()}


def test_registry_applies_stored_strategies_and_defaults(tmp_path: Path) -> None:
    (tmp_path / STRATEGIES_FILE).write_text(json.dumps({"logging": "FIRST_SUCCESS"}))
    registry, builder = _builder(tmp_path)

    builder._apply_provider_strategies()

    strategies = _strategies(registry)
    assert strategies["logging"] is FIRST
    assert strategies["voice_optimization"] is ALL
    assert strategies["text_to_speech"] is FIRST
    assert strategies["content"] is None


def test_registry_keeps_the_default_for_an_unsupported_stored_strategy(tmp_path: Path) -> None:
    (tmp_path / STRATEGIES_FILE).write_text(json.dumps({"text_to_speech": "ALL_PROVIDERS"}))
    registry, builder = _builder(tmp_path)

    builder._apply_provider_strategies()

    assert _strategies(registry)["text_to_speech"] is FIRST


def test_registry_falls_back_to_defaults_when_the_file_is_broken(tmp_path: Path) -> None:
    (tmp_path / STRATEGIES_FILE).write_text("{not json")
    registry, builder = _builder(tmp_path)

    builder._apply_provider_strategies()  # must not raise

    assert _strategies(registry)["logging"] is ALL


def test_registry_resets_a_service_to_its_default_when_its_entry_is_removed(tmp_path: Path) -> None:
    path = tmp_path / STRATEGIES_FILE
    path.write_text(json.dumps({"logging": "FIRST_SUCCESS"}))
    registry, builder = _builder(tmp_path)
    builder._apply_provider_strategies()

    path.write_text(json.dumps({}))
    builder._apply_provider_strategies()

    assert _strategies(registry)["logging"] is ALL


def test_load_all_from_disk_applies_the_stored_strategy(tmp_path: Path) -> None:
    (tmp_path / STRATEGIES_FILE).write_text(json.dumps({"voice_optimization": "FIRST_SUCCESS"}))
    registry, builder = _builder(tmp_path)

    builder.load_all_from_disk()

    assert _strategies(registry)["voice_optimization"] is FIRST
