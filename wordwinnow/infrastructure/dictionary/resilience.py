"""
The two small mechanisms that keep an external provider usable when its
answers come late or not at all: a token bucket that spreads requests over
time, and a circuit breaker that stops asking a provider that keeps failing.
"""

from asyncio import (
    Future,
    Lock,
    get_running_loop,
    shield,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Awaitable,
    Callable,
)
from enum import (
    Enum,
    auto,
)
from time import (
    monotonic,
)
from typing import (
    Final,
    final,
)

type MonotonicClock = Callable[[], float]

type Sleep = Callable[[float], Awaitable[None]]


@final
class TokenBucket:
    """
    Admits `burst` calls at once and `rate_per_second` on average.

    A caller that finds the bucket empty waits for the next token rather than
    being refused.
    """

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        clock: MonotonicClock = monotonic,
        sleep: Sleep = asyncio_sleep,
    ) -> None:
        # TEST:
        # The clock and the sleep are injected so a test can move time by hand.
        if rate_per_second <= 0:
            raise ValueError(
                f"a token bucket requires a positive rate, got {rate_per_second}",
            )

        if burst < 1:
            raise ValueError(
                f"a token bucket requires a burst of at least one, got {burst}",
            )

        self._rate: Final = rate_per_second

        self._burst: Final = burst

        self._capacity: Final = float(
            burst,
        )

        self._clock: Final = clock

        self._sleep: Final = sleep

        self._lock: Final = Lock()

        self._tokens = float(
            burst,
        )

        self._refilled_at = clock()

        self._waiting = 0

    @property
    def rate_per_second(
        self,
    ) -> float:
        return self._rate

    @property
    def burst(
        self,
    ) -> int:
        return self._burst

    @property
    def tokens(
        self,
    ) -> float:
        """
        The tokens available now, the part of the next one that has refilled
        included.
        """

        elapsed = max(
            0.0,
            self._clock() - self._refilled_at,
        )

        return max(
            0.0,
            min(
                self._capacity,
                self._tokens + elapsed * self._rate,
            ),
        )

    @property
    def waiting(
        self,
    ) -> int:
        """
        How many callers are waiting for a token.
        """

        return self._waiting

    async def acquire(
        self,
    ) -> None:
        """
        Take one token, waiting for it when none is available.
        """

        self._waiting += 1

        try:
            # NOTE:
            # The lock is held across the wait, so waiters are served in arrival order and never race for one token.
            async with self._lock:
                self._refill()

                if self._tokens < 1.0:
                    await self._sleep(
                        (1.0 - self._tokens) / self._rate,
                    )

                    self._refill()

                self._tokens -= 1.0

        finally:
            self._waiting -= 1

    def _refill(
        self,
    ) -> None:
        now = self._clock()

        elapsed = max(
            0.0,
            now - self._refilled_at,
        )

        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate,
        )

        self._refilled_at = now


class BreakerState(
    Enum,
):
    """
    Whether the breaker is letting calls through.
    """

    CLOSED = auto()

    OPEN = auto()

    HALF_OPEN = auto()


class Permit(
    Enum,
):
    """
    What the breaker lets one call do: go ahead, go ahead as the trial that
    tests a provider after the recovery period, or not be made at all.
    """

    CALL = auto()

    TRIAL = auto()

    REFUSED = auto()


@final
class CircuitBreaker:
    """
    Stops calls after `failure_threshold` consecutive failures and lets one
    trial call through once `recovery_seconds` have passed.

    A successful trial closes the circuit again; a failed one reopens it for
    another recovery period.

    A call that arrives while the trial is in flight waits for its outcome,
    and goes ahead or is refused by it.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        clock: MonotonicClock = monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(
                f"a circuit breaker requires a threshold of at least one, got {failure_threshold}",
            )

        if recovery_seconds <= 0:
            raise ValueError(
                f"a circuit breaker requires a positive recovery period, got {recovery_seconds}",
            )

        self._failure_threshold: Final = failure_threshold

        self._recovery_seconds: Final = recovery_seconds

        self._clock: Final = clock

        self._state = BreakerState.CLOSED

        self._failures = 0

        self._opened_at = 0.0

        self._trial: Future[None] | None = None

    @property
    def state(
        self,
    ) -> BreakerState:
        """
        Closed, or open for the recovery period after a failure opened the
        circuit, then half-open, because the next call is the trial.
        """

        # NOTE:
        # Nothing runs when the recovery period ends, so the state is read from the clock, as the pattern defines it;
        # a breaker that changed only when a call came would say it is open for as long as nothing is called.
        if self._state is BreakerState.OPEN and self._recovered():
            return BreakerState.HALF_OPEN

        return self._state

    @property
    def retry_in_seconds(
        self,
    ) -> float | None:
        """
        While the circuit is open, the seconds until a call may try again.
        """

        if self.state is not BreakerState.OPEN:
            return None

        return self._recovery_seconds - (self._clock() - self._opened_at)

    async def permit(
        self,
    ) -> Permit:
        """
        Claim permission for one call, once the trial in flight, if there is
        one, has its outcome.
        """

        # NOTE:
        # A call refused while the trial runs would be lost even when the trial succeeds, and a refusal takes no time,
        # so every call of a batch would be refused within the one trial; waiting costs a batch no more time than the
        # trial it already waits for.
        while self._trial is not None:
            await shield(
                arg=self._trial,
            )

        if self._state is BreakerState.CLOSED:
            return Permit.CALL

        if not self._recovered():
            return Permit.REFUSED

        self._state = BreakerState.HALF_OPEN

        self._trial = get_running_loop().create_future()

        return Permit.TRIAL

    def record_success(
        self,
    ) -> None:
        """
        Note that a permitted call succeeded, which closes the circuit.
        """

        self._state = BreakerState.CLOSED

        self._failures = 0

        self._settle()

    def record_failure(
        self,
    ) -> None:
        """
        Note that a permitted call failed.

        The circuit opens at the threshold, and at once when the call was a
        trial.
        """

        self._failures += 1

        if self._state is not BreakerState.CLOSED or self._failures >= self._failure_threshold:
            self._state = BreakerState.OPEN

            self._opened_at = self._clock()

            self._failures = 0

        self._settle()

    def abandon(
        self,
    ) -> None:
        """
        Note that the trial ended without an outcome, as a canceled call does,
        so the next call is the trial.
        """

        if self._trial is None:
            return

        self._state = BreakerState.OPEN

        self._settle()

    def _recovered(
        self,
    ) -> bool:
        return self._clock() - self._opened_at >= self._recovery_seconds

    def _settle(
        self,
    ) -> None:
        trial = self._trial

        if trial is None:
            return

        self._trial = None

        trial.set_result(
            None,
        )
