"""Retries with exponential backoff and jitter for async calls, observable through a RetryProbe.

The policy knows nothing about domain errors: the caller says which exceptions are worth
retrying (`retry_if`) and how to describe them to the probe (`reason`).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

type RetryPredicate = Callable[[BaseException], bool]
type Describe = Callable[[BaseException], str]


class RetryProbe(Protocol):
    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None: ...


class NullRetryProbe:
    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        pass


def default_wait() -> wait_base:
    """0.2 s, 0.4 s, 0.8 s ... capped at 2 s, plus up to 1 s of jitter."""
    return wait_exponential_jitter(multiplier=0.2, max=2.0)


class RetryPolicy:
    def __init__(
        self,
        *,
        retry_if: RetryPredicate,
        attempts: int = 3,
        reason: Describe = str,
        wait: wait_base | None = None,
        probe: RetryProbe | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        self._retry_if = retry_if
        self._attempts = attempts
        self._reason = reason
        self._wait = wait if wait is not None else default_wait()
        self._probe = probe or NullRetryProbe()

    async def call[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run `operation`, retrying while `retry_if` accepts the error; re-raises the last one."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=self._wait,
            retry=retry_if_exception(self._retry_if),
            before_sleep=self._report_retry,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await operation()
        raise AssertionError("unreachable: tenacity re-raises the last error")

    def _report_retry(self, state: RetryCallState) -> None:
        # before_sleep only runs for errors `retry_if` accepted.
        error = state.outcome.exception() if state.outcome else None
        if error is not None:
            self._probe.retry_scheduled(
                attempt=state.attempt_number,
                reason=self._reason(error),
                wait_seconds=state.next_action.sleep if state.next_action else 0.0,
            )


if TYPE_CHECKING:
    _: type[RetryProbe] = NullRetryProbe
