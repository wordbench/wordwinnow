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

    breaker.record_failure(
        trial=False,
    )

    return breaker


async def _opened_with_a_call_in_flight(
    clock: FakeMonotonicClock,
    /,
) -> CircuitBreaker:
    """
    A breaker that opened at the first failure while a second call it had
    admitted is still in flight.
    """

    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_seconds=30.0,
        clock=clock,
    )

    for _ in range(
        2,
    ):
        assert await breaker.permit() is Permit.CALL

    breaker.record_failure(
        trial=False,
    )

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

    async def test_a_pause_holds_every_caller_and_ends_with_one_token_then_the_rate(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        sleep = RecordingSleep(
            clock=clock,
        )

        bucket = TokenBucket(
            rate_per_second=2.0,
            burst=4,
            clock=clock,
            sleep=sleep,
        )

        bucket.pause(
            until=9.0,
        )

        paused = bucket.tokens

        taken = (
            await bucket.acquire(),
            await bucket.acquire(),
            await bucket.acquire(),
        )

        # NOTE:
        # Nine seconds would have refilled the whole burst, and the pause keeps none of it: the first caller gets the
        # one token the end of the pause gives, and each of the others waits half a second for the next.
        assert paused == 0.0

        assert taken == (
            True,
            True,
            True,
        )

        assert sleep.delays == [
            9.0,
            0.5,
            0.5,
        ]

    async def test_a_pause_that_ends_sooner_than_one_in_force_changes_nothing(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        sleep = RecordingSleep(
            clock=clock,
        )

        bucket = TokenBucket(
            rate_per_second=2.0,
            burst=4,
            clock=clock,
            sleep=sleep,
        )

        bucket.pause(
            until=9.0,
        )

        bucket.pause(
            until=3.0,
        )

        await bucket.acquire()

        assert sleep.delays == [
            9.0,
        ]

    async def test_a_caller_gives_up_only_on_a_pause_longer_than_it_waits_for_one(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        sleep = RecordingSleep(
            clock=clock,
        )

        bucket = TokenBucket(
            rate_per_second=0.1,
            burst=1,
            clock=clock,
            sleep=sleep,
        )

        await bucket.acquire()

        # NOTE:
        # The next token is ten seconds away at the rate, which is the bucket's own pacing and waited out however
        # short the caller's limit; a pause is the provider's, and only a pause longer than the limit sends the caller
        # away.
        paced = await bucket.acquire(
            max_pause_seconds=1.0,
        )

        bucket.pause(
            until=clock.now + 60.0,
        )

        refused = await bucket.acquire(
            max_pause_seconds=30.0,
        )

        admitted = await bucket.acquire(
            max_pause_seconds=60.0,
        )

        assert (
            paced,
            refused,
            admitted,
        ) == (
            True,
            False,
            True,
        )

        assert sleep.delays == [
            10.0,
            60.0,
        ]

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

            breaker.record_failure(
                trial=False,
            )

        assert breaker.state is BreakerState.CLOSED

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
        )

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

    async def test_a_clock_a_hair_short_of_the_trial_counts_as_reaching_it(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        # NOTE:
        # From this moment, ten seconds and then twenty more come to 33.484145999999996, a hair short of the 33.484146
        # the thirty seconds of recovery come to.
        clock.advance(
            seconds=3.484146,
        )

        breaker = CircuitBreaker(
            failure_threshold=1,
            recovery_seconds=30.0,
            clock=clock,
        )

        breaker.record_failure(
            trial=False,
        )

        clock.advance(
            seconds=10.0,
        )

        clock.advance(
            seconds=20.0,
        )

        assert breaker.state is BreakerState.HALF_OPEN

        assert await breaker.permit() is Permit.TRIAL

    async def test_a_success_resets_the_failure_count(
        self,
    ) -> None:
        breaker = CircuitBreaker(
            failure_threshold=2,
            recovery_seconds=30.0,
            clock=FakeMonotonicClock(),
        )

        breaker.record_failure(
            trial=False,
        )

        breaker.record_success(
            trial=False,
        )

        breaker.record_failure(
            trial=False,
        )

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

        breaker.record_success(
            trial=True,
        )

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

        breaker.record_success(
            trial=True,
        )

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

        breaker.record_failure(
            trial=True,
        )

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

        breaker.record_failure(
            trial=True,
        )

        assert breaker.state is BreakerState.OPEN

        assert await breaker.permit() is Permit.REFUSED

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

    async def test_the_failed_trials_count_from_the_opening_and_start_again_when_a_trial_closes_the_circuit(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = CircuitBreaker(
            failure_threshold=1,
            recovery_seconds=10.0,
            clock=clock,
        )

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
        )

        counts = [
            breaker.failed_trials,
        ]

        for _ in range(
            2,
        ):
            clock.advance(
                seconds=10.0,
            )

            assert await breaker.permit() is Permit.TRIAL

            breaker.record_failure(
                trial=True,
            )

            counts.append(
                breaker.failed_trials,
            )

        clock.advance(
            seconds=10.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        breaker.record_success(
            trial=True,
        )

        counts.append(
            breaker.failed_trials,
        )

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
        )

        counts.append(
            breaker.failed_trials,
        )

        assert counts == [
            0,
            1,
            2,
            0,
            0,
        ]

    async def test_the_random_share_of_a_period_only_shortens_it(
        self,
    ) -> None:
        # NOTE:
        # A draw of nought keeps the whole period and a draw just short of one takes the whole random quarter off it,
        # so the period is never passed and a quarter is the most it moves.
        def period(
            draw: float,
            /,
        ) -> float | None:
            breaker = CircuitBreaker(
                failure_threshold=1,
                recovery_seconds=10.0,
                jitter=0.25,
                clock=FakeMonotonicClock(),
                random=lambda: draw,
            )

            breaker.record_failure(
                trial=False,
            )

            return breaker.retry_in_seconds

        assert (
            period(
                0.0,
            ),
            period(
                0.5,
            ),
            period(
                0.999,
            ),
        ) == (
            10.0,
            8.75,
            10.0 * (1.0 - 0.25 * 0.999),
        )

    async def test_a_wait_the_provider_names_is_the_least_the_circuit_stays_open_whatever_the_draw(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = CircuitBreaker(
            failure_threshold=1,
            recovery_seconds=10.0,
            jitter=0.25,
            clock=clock,
            random=lambda: 0.999,
        )

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
            retry_after_seconds=120.0,
        )

        assert breaker.retry_in_seconds == 120.0

    async def test_a_call_admitted_before_the_circuit_opened_does_not_close_it_by_succeeding(
        self,
    ) -> None:
        breaker = await _opened_with_a_call_in_flight(
            FakeMonotonicClock(),
        )

        breaker.record_success(
            trial=False,
        )

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            30.0,
        )

        assert await breaker.permit() is Permit.REFUSED

    async def test_a_late_success_once_the_recovery_period_is_over_leaves_the_next_call_the_trial(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        breaker.record_success(
            trial=False,
        )

        assert breaker.state is BreakerState.HALF_OPEN

        assert await breaker.permit() is Permit.TRIAL

    async def test_a_late_success_during_the_trial_neither_closes_the_circuit_nor_ends_the_trial(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
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

        breaker.record_success(
            trial=False,
        )

        await asyncio_sleep(
            delay=0,
        )

        assert (
            breaker.state,
            waiting.done(),
        ) == (
            BreakerState.HALF_OPEN,
            False,
        )

        breaker.record_failure(
            trial=True,
        )

        assert await waiting is Permit.REFUSED

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            30.0,
        )

    async def test_a_late_failure_during_the_trial_leaves_the_calls_waiting_for_it_waiting(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
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

        breaker.record_failure(
            trial=False,
        )

        await asyncio_sleep(
            delay=0,
        )

        assert (
            breaker.state,
            waiting.done(),
        ) == (
            BreakerState.HALF_OPEN,
            False,
        )

        breaker.record_success(
            trial=True,
        )

        assert await waiting is Permit.CALL

        assert breaker.state is BreakerState.CLOSED

    async def test_a_call_that_fails_while_the_circuit_is_open_leaves_the_trial_where_it_is(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
            clock,
        )

        clock.advance(
            seconds=10.0,
        )

        breaker.record_failure(
            trial=False,
        )

        # NOTE:
        # The call was admitted before the circuit opened, so its failure says nothing the opening did not, and only
        # the trial decides.
        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            20.0,
        )

    async def test_a_late_failure_that_names_a_longer_wait_moves_the_trial_later(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
            clock,
        )

        clock.advance(
            seconds=10.0,
        )

        breaker.record_failure(
            trial=False,
            retry_after_seconds=120.0,
        )

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            120.0,
        )

    async def test_a_failure_that_names_a_longer_wait_opens_the_circuit_for_that_wait(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = CircuitBreaker(
            failure_threshold=1,
            recovery_seconds=30.0,
            clock=clock,
        )

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
            retry_after_seconds=120.0,
        )

        clock.advance(
            seconds=60.0,
        )

        refused = await breaker.permit()

        clock.advance(
            seconds=60.0,
        )

        tried = await breaker.permit()

        assert (
            refused,
            tried,
        ) == (
            Permit.REFUSED,
            Permit.TRIAL,
        )

    async def test_a_wait_named_before_the_opening_holds_the_trial_back(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = CircuitBreaker(
            failure_threshold=2,
            recovery_seconds=10.0,
            clock=clock,
        )

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
            retry_after_seconds=60.0,
        )

        clock.advance(
            seconds=5.0,
        )

        assert await breaker.permit() is Permit.CALL

        breaker.record_failure(
            trial=False,
        )

        # NOTE:
        # The second failure opens the circuit for its own ten seconds, but the first named sixty, of which fifty-five
        # are left, and no trial may come before they end.
        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            55.0,
        )

        clock.advance(
            seconds=54.0,
        )

        refused = await breaker.permit()

        clock.advance(
            seconds=1.0,
        )

        tried = await breaker.permit()

        assert (
            refused,
            tried,
        ) == (
            Permit.REFUSED,
            Permit.TRIAL,
        )

    async def test_a_wait_named_during_the_trial_holds_the_next_trial_back(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
            clock,
        )

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        # NOTE:
        # The call admitted before the opening fails during the trial and names ninety seconds, which the trial's own
        # failure then cannot shorten to its thirty.
        breaker.record_failure(
            trial=False,
            retry_after_seconds=90.0,
        )

        breaker.record_failure(
            trial=True,
        )

        assert (
            breaker.state,
            breaker.retry_in_seconds,
        ) == (
            BreakerState.OPEN,
            90.0,
        )

    async def test_the_failures_in_a_row_count_toward_the_threshold_and_start_again_at_an_answer(
        self,
    ) -> None:
        breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_seconds=30.0,
            clock=FakeMonotonicClock(),
        )

        counts: list[int] = []

        for answered in (
            False,
            False,
            True,
            False,
            False,
            False,
        ):
            assert await breaker.permit() is Permit.CALL

            if answered:
                breaker.record_success(
                    trial=False,
                )

            else:
                breaker.record_failure(
                    trial=False,
                )

            counts.append(
                breaker.consecutive_failures,
            )

        assert (
            counts,
            breaker.failure_threshold,
            breaker.state,
        ) == (
            [
                1,
                2,
                0,
                1,
                2,
                0,
            ],
            3,
            BreakerState.OPEN,
        )

    async def test_the_count_stays_nought_while_the_circuit_is_open_and_through_its_trial(
        self,
    ) -> None:
        clock = FakeMonotonicClock()

        breaker = await _opened_with_a_call_in_flight(
            clock,
        )

        counts = [
            breaker.consecutive_failures,
        ]

        clock.advance(
            seconds=30.0,
        )

        assert await breaker.permit() is Permit.TRIAL

        breaker.record_failure(
            trial=False,
        )

        counts.append(
            breaker.consecutive_failures,
        )

        breaker.record_success(
            trial=True,
        )

        counts.append(
            breaker.consecutive_failures,
        )

        assert (
            counts,
            breaker.state,
        ) == (
            [
                0,
                0,
                0,
            ],
            BreakerState.CLOSED,
        )

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

        with raises(
            expected_exception=ValueError,
            match="random share from nought to below one",
        ):
            CircuitBreaker(
                failure_threshold=1,
                recovery_seconds=10.0,
                jitter=1.0,
            )
