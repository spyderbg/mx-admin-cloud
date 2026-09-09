"""Traffic governance for outbound image-provider calls.

One :class:`RequestGovernor` per provider enforces three things that image APIs
make expensive to get wrong:

* **Concurrency** — at most ``max_concurrency`` in-flight requests.
* **Spacing** — at least ``min_interval_seconds`` between request starts.
* **Retries** — bounded exponential backoff on transient failures only,
  honouring a server-sent ``Retry-After``.

Provider SDK retries must be disabled so this is the only retry layer.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, TypeVar

import httpx

T = TypeVar("T")

RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _as_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(minimum, parsed)


def _as_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


@dataclass(frozen=True)
class TrafficPolicy:
    """Per-provider traffic and retry limits, read from ``config.traffic``."""

    max_concurrency: int = 2
    min_interval_seconds: float = 0.0
    max_retries: int = 2
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    timeout_seconds: float = 120.0

    @classmethod
    def from_config(cls, raw: Any) -> "TrafficPolicy":
        """Build a policy from a config dict, ignoring malformed values."""
        if not isinstance(raw, dict):
            return cls()
        defaults = cls()
        return cls(
            max_concurrency=_as_int(raw.get("max_concurrency"), defaults.max_concurrency, minimum=1),
            min_interval_seconds=_as_float(raw.get("min_interval_seconds"), defaults.min_interval_seconds),
            max_retries=_as_int(raw.get("max_retries"), defaults.max_retries),
            backoff_base_seconds=_as_float(
                raw.get("backoff_base_seconds"), defaults.backoff_base_seconds, minimum=0.0,
            ),
            backoff_max_seconds=_as_float(
                raw.get("backoff_max_seconds"), defaults.backoff_max_seconds, minimum=0.0,
            ),
            timeout_seconds=_as_float(raw.get("timeout_seconds"), defaults.timeout_seconds, minimum=1.0),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the policy as a plain dict for API responses."""
        return {
            "max_concurrency": self.max_concurrency,
            "min_interval_seconds": self.min_interval_seconds,
            "max_retries": self.max_retries,
            "backoff_base_seconds": self.backoff_base_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
            "timeout_seconds": self.timeout_seconds,
        }


class ProviderCallError(Exception):
    """Base class for classified provider-call failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = 0


class RetryableError(ProviderCallError):
    """Transient failure; the governor may try again."""

    def __init__(
            self,
            message: str,
            *,
            status_code: int | None = None,
            retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class NonRetryableError(ProviderCallError):
    """Permanent failure; retrying would only cost money."""


def parse_retry_after(value: Any) -> float | None:
    """Parse a ``Retry-After`` header value into seconds."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def _retry_after_from_response(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    return parse_retry_after(getter("retry-after"))


def _classify_status(message: str, status: int, response: Any) -> ProviderCallError:
    if status in RETRYABLE_STATUS_CODES:
        return RetryableError(message, status_code=status, retry_after=_retry_after_from_response(response))
    return NonRetryableError(message, status_code=status)


def classify_exception(
        exc: BaseException,
        *,
        extra_retryable: tuple[type[BaseException], ...] = (),
) -> ProviderCallError:
    """Map an arbitrary provider exception onto a retryable/non-retryable error.

    SDK exception types are matched by duck typing (``status_code`` or ``code``)
    so this module never imports the OpenAI or Google SDKs.
    """
    if isinstance(exc, ProviderCallError):
        return exc

    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        body = ""
        try:
            body = response.text[:300]
        except (UnicodeDecodeError, RuntimeError):  # pragma: no cover - defensive
            body = ""
        return _classify_status(f"HTTP {response.status_code}: {body}", response.status_code, response)

    if isinstance(exc, (httpx.TransportError, asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return RetryableError(f"{type(exc).__name__}: {exc}")

    if extra_retryable and isinstance(exc, extra_retryable):
        return RetryableError(f"{type(exc).__name__}: {exc}")

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return _classify_status(f"{type(exc).__name__} {status}: {exc}", status, getattr(exc, "response", None))

    return NonRetryableError(f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class GovernedResult(Generic[T]):
    """Value produced by a governed call plus the number of attempts spent."""

    value: T
    attempts: int


# The timing/RNG collaborators are injected so tests can run without real waits.
class RequestGovernor:  # pylint: disable=too-many-instance-attributes
    """Serialises, spaces, and retries one provider's outbound calls."""

    def __init__(
            self,
            policy: TrafficPolicy,
            *,
            sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
            clock: Callable[[], float] = time.monotonic,
            rng: Callable[[], float] = random.random,
            extra_retryable: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._policy = policy
        self._sleep = sleep
        self._clock = clock
        self._rng = rng
        self._extra_retryable = extra_retryable
        self._semaphore = asyncio.Semaphore(policy.max_concurrency)
        self._interval_lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    @property
    def policy(self) -> TrafficPolicy:
        """Return the traffic policy in force."""
        return self._policy

    async def _wait_for_slot(self) -> None:
        if self._policy.min_interval_seconds <= 0:
            return
        async with self._interval_lock:
            now = self._clock()
            wait = max(0.0, self._next_allowed_at - now)
            self._next_allowed_at = max(now, self._next_allowed_at) + self._policy.min_interval_seconds
        if wait > 0:
            await self._sleep(wait)

    def _backoff_delay(self, error: RetryableError, attempt_no: int) -> float:
        if error.retry_after is not None:
            return error.retry_after
        raw = self._policy.backoff_base_seconds * (2 ** (attempt_no - 1))
        capped = min(self._policy.backoff_max_seconds, raw)
        jitter = self._rng() * self._policy.backoff_base_seconds * 0.1
        return capped + jitter

    async def run(self, attempt: Callable[[], Awaitable[T]]) -> GovernedResult[T]:
        """Run *attempt* under the traffic policy, retrying transient failures.

        Raises the classified :class:`ProviderCallError` (with ``.attempts`` set)
        when every attempt fails.
        """
        total_attempts = self._policy.max_retries + 1
        async with self._semaphore:
            for attempt_no in range(1, total_attempts + 1):
                await self._wait_for_slot()
                try:
                    value = await asyncio.wait_for(attempt(), self._policy.timeout_seconds)
                    return GovernedResult(value=value, attempts=attempt_no)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    error = classify_exception(exc, extra_retryable=self._extra_retryable)
                    error.attempts = attempt_no
                    if isinstance(error, NonRetryableError) or attempt_no == total_attempts:
                        raise error from exc
                    await self._sleep(self._backoff_delay(error, attempt_no))
        raise NonRetryableError("Governor exhausted without result.")  # pragma: no cover - unreachable
