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
from random import (
    random as uniform_random,
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

# WARN:
# A wait shorter than this is no wait: a clock compared with a moment computed from it can stop a hair short of it,
# and sleeping that hair again and again would never reach the token.
_NEGLIGIBLE_SECONDS: Final = 1e-6


@final
class TokenBucket:
    """
    Admits `burst` calls at once and `rate_per_second` on average.

    A caller that finds the bucket empty waits for the next token rather than
    being refused.

    A pause, which a provider's request to slow down starts, holds every
    caller until it ends and then admits them one at a time at the rate, so
    its end never releases a burst.
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

        now = self._clock()

        if now < self._refilled_at:
            return 0.0

        return max(
            0.0,
            min(
                self._capacity,
                self._tokens + (now - self._refilled_at) * self._rate,
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
        *,
        max_pause_seconds: float | None = None,
    ) -> bool:
        """
        Take one token, waiting for it when none is available, and say whether
        one was taken.

        With `max_pause_seconds`, a caller that a pause would hold longer than
        that takes nothing and is told so at once, while a wait for the rate
        alone is always waited out.
        """

        self._waiting += 1

        try:
            # NOTE:
            # The lock is held across the wait, so waiters are served in arrival order and never race for one token.
            async with self._lock:
                while True:
                    self._refill()

                    now = self._clock()

                    wait = (
                        max(
                            0.0,
                            self._refilled_at - now,
                        )
                        + max(
                            0.0,
                            1.0 - self._tokens,
                        )
                        / self._rate
                    )

                    if wait < _NEGLIGIBLE_SECONDS:
                        self._tokens -= 1.0

                        return True

                    if max_pause_seconds is not None and self._refilled_at - now > max_pause_seconds:
                        return False

                    await self._sleep(
                        wait,
                    )

        finally:
            self._waiting -= 1

    def pause(
        self,
        *,
        until: float,
    ) -> None:
        """
        Issue no token before `until`, one at `until`, and then the usual
        rate.

        Tokens that would have refilled meanwhile are not kept, because the
        provider has said it is getting more than it can take; a pause that
        ends sooner than one already in force changes nothing.
        """

        if until <= self._refilled_at:
            return

        self._tokens = 1.0

        self._refilled_at = until

    def _refill(
        self,
    ) -> None:
        now = self._clock()

        # NOTE:
        # During a pause nothing refills, and the tokens stand as they will be when it ends.
        if now <= self._refilled_at:
            return

        self._tokens = min(
            self._capacity,
            self._tokens + (now - self._refilled_at) * self._rate,
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
    trial call through once `recovery_seconds` have passed and every wait a
    failure named has ended, whichever is later.

    `jitter` is the share of each recovery period that is random, which
    shortens the period and never lengthens it.

    A successful trial closes the circuit again; a failed one reopens it for
    another recovery period.

    A call that arrives while the trial is in flight waits for its outcome,
    and goes ahead or is refused by it.

    Only the trial's own outcome ends the trial, and a call admitted before
    the circuit opened never closes it.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        recovery_seconds: float,
        jitter: float = 0.0,
        clock: MonotonicClock = monotonic,
        random: Callable[[], float] = uniform_random,
    ) -> None:
        # TEST:
        # The random draw is injected so a test can fix where in its range each period falls.
        if failure_threshold < 1:
            raise ValueError(
                f"a circuit breaker requires a threshold of at least one, got {failure_threshold}",
            )

        if recovery_seconds <= 0:
            raise ValueError(
                f"a circuit breaker requires a positive recovery period, got {recovery_seconds}",
            )

        if not 0 <= jitter < 1:
            raise ValueError(
                f"a circuit breaker requires a random share from nought to below one, got {jitter}",
            )

        self._failure_threshold: Final = failure_threshold

        self._recovery_seconds: Final = recovery_seconds

        self._jitter: Final = jitter

        self._clock: Final = clock

        self._random: Final = random

        self._state = BreakerState.CLOSED

        self._failures = 0

        # NOTE:
        # When the latest wait a failure named ends, whichever failure named it, since no trial may come before it.
        self._named_until = 0.0

        self._trial_at = 0.0

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
    def trial(
        self,
    ) -> object | None:
        """
        The trial in flight, the same object for its whole course, or `None`
        when there is none, so a call that held it can tell whether it still
        does.
        """

        return self._trial

    @property
    def retry_in_seconds(
        self,
    ) -> float | None:
        """
        While the circuit is open, the seconds until a call may try again.
        """

        if self.state is not BreakerState.OPEN:
            return None

        return self._trial_at - self._clock()

    @property
    def consecutive_failures(
        self,
    ) -> int:
        """
        The permitted calls that failed in a row while the circuit was closed,
        which open it once they reach the threshold.

        The count starts again at any success, and it is nought while the
        circuit is open, when the trial decides instead.
        """

        return self._failures

    @property
    def failure_threshold(
        self,
    ) -> int:
        """
        How many failures in a row open the circuit.
        """

        return self._failure_threshold

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
        *,
        trial: bool,
    ) -> None:
        """
        Note that a permitted call succeeded.

        The trial's success closes the circuit; another call's success counts
        only while the circuit is closed.
        """

        # NOTE:
        # A call admitted before the circuit opened can still answer after it opened, and only the trial's answer
        # closes the circuit, so that the recovery period runs its whole length whatever such a call says.
        if trial:
            self._state = BreakerState.CLOSED

            self._failures = 0

            self._settle()

        elif self._state is BreakerState.CLOSED:
            self._failures = 0

    def record_failure(
        self,
        *,
        trial: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        """
        Note that a permitted call failed, with how long the provider asked to
        be left alone, when it said.

        The circuit opens at the threshold, and at once when the call was the
        trial, until the recovery period has passed and every wait a failure
        named has ended, whichever is later.

        Another call that fails while the circuit is open, having been
        admitted before it opened, leaves the trial where it is unless the
        provider now asks for a longer wait, because only the trial decides;
        one that fails during the trial leaves the trial to decide, and the
        wait it names holds back the trial after it.
        """

        now = self._clock()

        # NOTE:
        # A wait the provider named is the least time before the next trial whenever a failure named it: before the
        # circuit opened, as it opened, while it was open, or during the trial.
        if retry_after_seconds is not None:
            self._named_until = max(
                self._named_until,
                now + retry_after_seconds,
            )

        if self._state is BreakerState.HALF_OPEN and not trial:
            return

        if self._state is BreakerState.OPEN and not trial:
            self._trial_at = max(
                self._trial_at,
                self._named_until,
            )

            return

        self._failures += 1

        if trial or self._failures >= self._failure_threshold:
            self._state = BreakerState.OPEN

            # NOTE:
            # The random share shortens the period and never lengthens it, and the provider's own waits are the least
            # the circuit stays open, whatever the draw.
            self._trial_at = max(
                now + self._recovery_seconds * (1.0 - self._jitter * self._random()),
                self._named_until,
            )

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
        # NOTE:
        # The moment of the trial is a sum the clock reaches by other sums, which can fall a hair short of it, and a
        # refusal for that hair would name a wait of nought.
        return self._trial_at - self._clock() < _NEGLIGIBLE_SECONDS

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
