"""Unit tests for the traffic governor (concurrency, spacing, retries)."""

# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from src.services.text_to_image.governor import (
    NonRetryableError,
    RequestGovernor,
    RetryableError,
    TrafficPolicy,
    classify_exception,
    parse_retry_after,
)


class _FakeClock:
    """Monotonic clock that only advances when sleep() is awaited."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _make_governor(policy: TrafficPolicy, clock: _FakeClock, rng: Any = lambda: 0.0) -> RequestGovernor:
    return RequestGovernor(policy, sleep=clock.sleep, clock=clock.time, rng=rng)


# ---------------------------------------------------------------------------
# TrafficPolicy
# ---------------------------------------------------------------------------

def test_policy_from_config_uses_defaults_for_garbage() -> None:
    policy = TrafficPolicy.from_config({"max_concurrency": "abc", "timeout_seconds": None})
    assert policy.max_concurrency == 2
    assert policy.timeout_seconds == 120.0


def test_policy_from_config_clamps_minimums() -> None:
    policy = TrafficPolicy.from_config(
        {"max_concurrency": 0, "min_interval_seconds": -5, "max_retries": -1},
    )
    assert policy.max_concurrency == 1
    assert policy.min_interval_seconds == 0.0
    assert policy.max_retries == 0


def test_policy_from_config_reads_values() -> None:
    policy = TrafficPolicy.from_config(
        {
            "max_concurrency": 3,
            "min_interval_seconds": 0.5,
            "max_retries": 4,
            "backoff_base_seconds": 2.0,
            "backoff_max_seconds": 60.0,
            "timeout_seconds": 90.0,
        },
    )
    assert policy.as_dict() == {
        "max_concurrency": 3,
        "min_interval_seconds": 0.5,
        "max_retries": 4,
        "backoff_base_seconds": 2.0,
        "backoff_max_seconds": 60.0,
        "timeout_seconds": 90.0,
    }


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------

def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/images")
    response = httpx.Response(status, headers=headers or {}, text="boom", request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_classify_retryable_statuses(status: int) -> None:
    assert isinstance(classify_exception(_status_error(status)), RetryableError)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_classify_non_retryable_statuses(status: int) -> None:
    error = classify_exception(_status_error(status))
    assert isinstance(error, NonRetryableError)
    assert error.status_code == status


def test_classify_reads_retry_after_header() -> None:
    error = classify_exception(_status_error(429, {"retry-after": "12"}))
    assert isinstance(error, RetryableError)
    assert error.retry_after == 12.0


def test_classify_transport_error_is_retryable() -> None:
    exc = httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://example.test"))
    assert isinstance(classify_exception(exc), RetryableError)


def test_classify_asyncio_timeout_is_retryable() -> None:
    assert isinstance(classify_exception(asyncio.TimeoutError()), RetryableError)


def test_classify_duck_typed_sdk_error() -> None:
    class _SdkError(Exception):
        code = 429

    assert isinstance(classify_exception(_SdkError("rate limited")), RetryableError)

    class _SdkClientError(Exception):
        status_code = 400

    assert isinstance(classify_exception(_SdkClientError("bad request")), NonRetryableError)


def test_classify_extra_retryable_types() -> None:
    class _SdkConnectionError(Exception):
        pass

    error = classify_exception(_SdkConnectionError("socket closed"), extra_retryable=(_SdkConnectionError,))
    assert isinstance(error, RetryableError)

    # Without the opt-in the same exception stays permanent.
    assert isinstance(classify_exception(_SdkConnectionError("socket closed")), NonRetryableError)


def test_classify_unknown_is_non_retryable() -> None:
    assert isinstance(classify_exception(ValueError("nope")), NonRetryableError)


def test_parse_retry_after_variants() -> None:
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


# ---------------------------------------------------------------------------
# RequestGovernor.run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_returns_value_on_first_attempt() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(), clock)

    async def attempt() -> str:
        return "ok"

    result = await governor.run(attempt)
    assert result.value == "ok"
    assert result.attempts == 1
    assert clock.slept == []


@pytest.mark.asyncio
async def test_run_retries_then_succeeds() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(max_retries=2, backoff_base_seconds=1.0), clock)
    calls = {"n": 0}

    async def attempt() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableError("transient")
        return "ok"

    result = await governor.run(attempt)
    assert result.attempts == 2
    assert clock.slept == [1.0]


@pytest.mark.asyncio
async def test_run_honours_retry_after_verbatim() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(max_retries=1, backoff_base_seconds=1.0), clock)
    calls = {"n": 0}

    async def attempt() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableError("slow down", status_code=429, retry_after=17.5)
        return "ok"

    await governor.run(attempt)
    assert clock.slept == [17.5]


@pytest.mark.asyncio
async def test_run_caps_backoff() -> None:
    clock = _FakeClock()
    policy = TrafficPolicy(max_retries=4, backoff_base_seconds=10.0, backoff_max_seconds=15.0)
    governor = _make_governor(policy, clock)

    async def attempt() -> str:
        raise RetryableError("always down")

    with pytest.raises(RetryableError):
        await governor.run(attempt)

    assert clock.slept == [10.0, 15.0, 15.0, 15.0]


@pytest.mark.asyncio
async def test_run_non_retryable_fails_immediately() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(max_retries=3), clock)
    calls = {"n": 0}

    async def attempt() -> str:
        calls["n"] += 1
        raise NonRetryableError("bad api key")

    with pytest.raises(NonRetryableError) as excinfo:
        await governor.run(attempt)

    assert calls["n"] == 1
    assert excinfo.value.attempts == 1
    assert clock.slept == []


@pytest.mark.asyncio
async def test_run_exhausts_retries_and_reports_attempts() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(max_retries=2, backoff_base_seconds=1.0), clock)

    async def attempt() -> str:
        raise RetryableError("still down")

    with pytest.raises(RetryableError) as excinfo:
        await governor.run(attempt)

    assert excinfo.value.attempts == 3


@pytest.mark.asyncio
async def test_run_timeout_is_retryable() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(max_retries=0, timeout_seconds=1.0), clock)

    async def attempt() -> str:
        await asyncio.sleep(5)
        return "never"

    with pytest.raises(RetryableError):
        await governor.run(attempt)


@pytest.mark.asyncio
async def test_run_limits_concurrency() -> None:
    governor = RequestGovernor(TrafficPolicy(max_concurrency=2, min_interval_seconds=0.0))
    state = {"in_flight": 0, "peak": 0}

    async def attempt() -> str:
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.01)
        state["in_flight"] -= 1
        return "ok"

    await asyncio.gather(*(governor.run(attempt) for _ in range(6)))
    assert state["peak"] <= 2


@pytest.mark.asyncio
async def test_run_spaces_request_starts() -> None:
    clock = _FakeClock()
    governor = _make_governor(TrafficPolicy(max_concurrency=1, min_interval_seconds=2.0), clock)

    async def attempt() -> str:
        return "ok"

    await governor.run(attempt)
    await governor.run(attempt)
    await governor.run(attempt)

    # First call is free; each later start waits out the interval.
    assert clock.slept == [2.0, 2.0]
