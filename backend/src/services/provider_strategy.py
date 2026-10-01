"""Provider selection strategies: how a service dispatches one task to its active providers.

Each service declares the strategies it supports and its default (see `Service` in models.py).
The selected strategy per service is stored in `<config root>/_service_strategies.json`, next to
(not inside) the per-service provider directories, and applied by the registry whenever it loads
the configuration.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from src.core.utils import JsonObjectReadError, read_json_object

STRATEGIES_FILE = "_service_strategies.json"

ProviderT = TypeVar("ProviderT")
ResultT = TypeVar("ResultT")


class ProviderSelectionStrategy(str, Enum):
    """How a service dispatches one task to its active providers."""

    # Call every active provider, in priority order (e.g. logging).
    ALL_PROVIDERS = "ALL_PROVIDERS"
    # Try active providers in priority order until one succeeds.
    FIRST_SUCCESS = "FIRST_SUCCESS"


class InvalidStrategiesFileError(ValueError):
    """Raised when the strategies file is not a JSON object of known strategy names."""


class AllProvidersFailedError(RuntimeError):
    """Raised when no provider succeeded; *errors* holds one "provider: error" entry per attempt."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "No provider succeeded.")


def read_strategies(config_root: Path) -> dict[str, ProviderSelectionStrategy]:
    """Return the stored strategy per service name, or {} when nothing is stored yet.

    Raises InvalidStrategiesFileError when the file is not a JSON object whose values are
    strategy names. Whether a service supports its stored strategy is up to the service.
    """
    path = config_root / STRATEGIES_FILE
    if not path.exists():
        return {}
    try:
        payload = read_json_object(path)
    except JsonObjectReadError as exc:
        raise InvalidStrategiesFileError(f"{STRATEGIES_FILE} is not a valid JSON object: {exc}.") from exc
    strategies: dict[str, ProviderSelectionStrategy] = {}
    for service_name, value in payload.items():
        try:
            strategies[str(service_name)] = ProviderSelectionStrategy(value)
        except ValueError as exc:
            raise InvalidStrategiesFileError(
                f"{STRATEGIES_FILE} has an unknown strategy {value!r} for '{service_name}'.",
            ) from exc
    return strategies


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "name", type(provider).__name__))


async def first_success(
        providers: Iterable[ProviderT],
        attempt: Callable[[ProviderT], Awaitable[ResultT]],
        failure: Callable[[ResultT], str | None] = lambda _result: None,
) -> ResultT:
    """Return the result of the first provider whose *attempt* succeeds, trying them in order.

    An attempt fails when it raises or when *failure* returns an error message for its result;
    the next provider is then tried. Raises AllProvidersFailedError when every attempt failed.
    """
    errors: list[str] = []
    for provider in providers:
        try:
            result = await attempt(provider)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(f"{_provider_name(provider)}: {exc}")
            continue
        error = failure(result)
        if error is None:
            return result
        errors.append(f"{_provider_name(provider)}: {error}")
    raise AllProvidersFailedError(errors)


def dispatch_each(
        strategy: ProviderSelectionStrategy | None,
        providers: Iterable[ProviderT],
        call: Callable[[ProviderT], object],
) -> list[str]:
    """Run a side-effect *call* per provider according to *strategy*; never raises.

    ALL_PROVIDERS calls every provider; FIRST_SUCCESS stops after the first call that does
    not raise. Returns one "provider: error" entry per call that raised.
    """
    errors: list[str] = []
    for provider in providers:
        try:
            call(provider)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(f"{_provider_name(provider)}: {exc}")
            continue
        if strategy is ProviderSelectionStrategy.FIRST_SUCCESS:
            break
    return errors
