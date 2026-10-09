"""
The token bucket and the circuit breaker against a clock the test moves.
"""

from asyncio import (
    Event,
    create_task,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from typing import (
    final,
)

from pytest import (
    raises,
)

from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
    RecordingSleep,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
    CircuitBreaker,
    Permit,
    TokenBucket,
)


async def _opened(
    clock: FakeMonotonicClock,
    /,
) -> CircuitBreaker:
    """
    A breaker that opens at the first failure, after that failure.
    """

    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_seconds=30.0,
        clock=clock,
    )

    assert await breaker.permit() is Permit.CALL

    breaker.record_failure()

    return breaker


@final
class TestTokenBucket:
    async def test_the_burst_is_admitted_at_once_and_the_next_call_waits_for_a_token(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        sleep = RecordingSleep(
            clock=clock,
        )

        bucket = TokenBucket(
            rate_per_second=2.0,
            burst=2,
            clock=clock,
            sleep=sleep,
        )

        await bucket.acquire()

        await bucket.acquire()

        assert sleep.delays == []

        await bucket.acquire()

        assert sleep.delays == [
            0.5,
        ]

    async def test_a_refill_never_exceeds_the_burst(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        sleep = RecordingSleep(
            clock=clock,
        )

        bucket = TokenBucket(
            rate_per_second=10.0,
            burst=2,
            clock=clock,
            sleep=sleep,
        )

        clock.advance(
            seconds=100.0,
        )

        await bucket.acquire()

        await bucket.acquire()

        assert sleep.delays == []

        await bucket.acquire()

        assert sleep.delays == [
            0.1,
        ]

    async def test_the_tokens_refill_at_the_rate_and_a_caller_without_one_is_waiting(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        released = Event()

        # NOTE:
        # The wait for a token holds until the test releases it, so the caller can be seen waiting.
        async def sleep(
            delay: float,
            /,
        ) -> None:
            await released.wait()

            clock.advance(
                seconds=delay,
            )

        bucket = TokenBucket(
            rate_per_second=2.0,
            burst=2,
            clock=clock,
            sleep=sleep,
        )

        await bucket.acquire()

        await bucket.acquire()

        emptied = (
            bucket.tokens,
            bucket.waiting,
        )

        clock.advance(
            seconds=0.25,
        )

        refilled = bucket.tokens

        waiting = create_task(
            coro=bucket.acquire(),
        )

        await asyncio_sleep(
            delay=0,
        )

        counted = bucket.waiting

        released.set()

        await waiting

        assert emptied == (
            0.0,
            0,
        )

        assert refilled == 0.5

        assert counted == 1

        assert (
            bucket.tokens,
            bucket.waiting,
        ) == (
            0.0,
            0,
        )

    def test_an_invalid_configuration_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="positive rate",
        ):
            TokenBucket(
                rate_per_second=0.0,
                burst=1,
            )

        with raises(
            expected_exception=ValueError,
            match="burst of at least one",
        ):
            TokenBucket(
                rate_per_second=1.0,
                burst=0,
            )


@final
class TestCircuitBreaker:
    async def test_it_opens_at_the_threshold_and_refuses_until_the_recovery_period_passes(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            clock=clock,
        )

        for _ in range(
            2,
        ):
            assert await breaker.permit() is Permit.CALL

            breaker.record_failure()

        assert breaker.state is BreakerState.CLOSED

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure()

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            30.0,
        )

        assert await breaker.permit() is Permit.REFUSED

        clock.advance(
            seconds=29.0,
        )

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            1.0,
        )

        assert await breaker.permit() is Permit.REFUSED

    async def test_a_success_resets_the_failure_count(
        self,
    ) -> None:
        breaker = CircuitBreaker(
            failure_threshold=2,
            recovery_seconds=30.0,
            clock=FakeMonotonicClock(),
        )

        breaker.record_failure()

        breaker.record_success()

        breaker.record_failure()

        assert breaker.state is BreakerState.CLOSED

        assert await breaker.permit() is Permit.CALL

    async def test_once_the_recovery_period_is_over_it_is_half_open_before_any_call(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.HALF_OPEN,
            None,
        )

    async def test_after_recovery_one_trial_is_permitted_and_its_success_closes_the_circuit(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        assert breaker.state is BreakerState.HALF_OPEN

        breaker.record_success()

        assert breaker.state is BreakerState.CLOSED

        assert await breaker.permit() is Permit.CALL

    async def test_a_call_during_the_trial_waits_for_it_and_goes_ahead_when_it_succeeds(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        waiting = create_task(
            coro=breaker.permit(),
        )

        await asyncio_sleep(
            delay=0,
        )

        assert not waiting.done()

        breaker.record_success()

        assert await waiting is Permit.CALL

    async def test_a_call_during_the_trial_waits_for_it_and_is_refused_when_it_fails(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        waiting = create_task(
            coro=breaker.permit(),
        )

        await asyncio_sleep(
            delay=0,
        )

        breaker.record_failure()

        assert await waiting is Permit.REFUSED

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            30.0,
        )

    async def test_an_abandoned_trial_makes_the_next_call_the_trial(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        waiting = create_task(
            coro=breaker.permit(),
        )

        await asyncio_sleep(
            delay=0,
        )

        breaker.abandon()

        assert await waiting is Permit.TRIAL

        assert breaker.state is BreakerState.HALF_OPEN

    async def test_a_failed_trial_reopens_the_circuit_for_another_recovery_period(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        breaker.record_failure()

        assert breaker.state is BreakerState.OPEN

        assert await breaker.permit() is Permit.REFUSED

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

    def test_an_invalid_configuration_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="threshold of at least one",
        ):
            CircuitBreaker(
                failure_threshold=0,
                recovery_seconds=30.0,
            )

        with raises(
            expected_exception=ValueError,
            match="positive recovery period",
        ):
            CircuitBreaker(
                failure_threshold=1,
                recovery_seconds=0.0,
            )
