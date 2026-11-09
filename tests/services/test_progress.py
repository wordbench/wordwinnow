"""
When the dictionary counts as paused, how a worker's progress reaches the
analysis store for a command waiting elsewhere, and how a view of the
dictionary hears what it is doing.
"""

from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Sequence,
)
from datetime import (
    datetime,
    timedelta,
)
from logging import (
    WARNING,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from pytest import (
    LogCaptureFixture,
)

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    InMemoryStorage,
    InMemoryUnitOfWork,
)
from tests.fakes.progress import (
    RecordingWatch,
    held_words,
)
from tests.infrastructure.messaging.analyses import (
    completed_analysis,
    requested_analysis,
)
from wordwinnow.application.dto import (
    DictionaryWait,
    ProcessingProgress,
)
from wordwinnow.application.ports.pipeline_progress import (
    HeldWords,
)
from wordwinnow.domain.analysis import (
    Analysis,
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    LookupCounts,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    ProviderActivity,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    unavailable,
)
from wordwinnow.services.progress import (
    ActivityUnreadableError,
    DictionaryWaitState,
    StoredProgress,
    dictionary_wait_of,
    paused_after,
    watching,
)

_ANSWERED: Final = DictionaryLookup(
    outcome=LookupOutcome.NOT_FOUND,
)

_REFUSED: Final = unavailable(
    reason=FailureReason.CIRCUIT_OPEN,
)

_TIMED_OUT: Final = unavailable(
    reason=FailureReason.TIMED_OUT,
)

_TIMED_OUT_WORD: Final = (
    HeldWords(
        reason=FailureReason.TIMED_OUT,
        turned_away=1,
        turned_away_wake_in_seconds=30.0,
        others_sleeping=0,
        wake_in_seconds=30.0,
        longest_wait_seconds=30.0,
        repeated=False,
    ),
)

_IDLE: Final = DictionaryActivity(
    counts=LookupCounts(
        cached=0,
        found=0,
        not_found=0,
        unavailable=MappingProxyType(
            mapping={},
        ),
    ),
    provider=ProviderActivity(
        circuit=BreakerState.CLOSED,
        retry_in_seconds=None,
        cooldown_seconds=None,
        consecutive_failures=0,
        failure_threshold=5,
        tokens=4.0,
        burst=4,
        requests_per_second=2.0,
        waiting_for_budget=0,
        timeout_seconds=30.0,
        lookups=(),
        retries=MappingProxyType(
            mapping={},
        ),
    ),
)


async def _stored(
    analysis: Analysis,
    /,
) -> InMemoryStorage:
    storage = InMemoryStorage()

    async with storage.new_unit_of_work() as uow:
        await uow.analyses.add(
            analysis=analysis,
        )

        await uow.commit()

    return storage


def _processing() -> Analysis:
    analysis = requested_analysis()

    analysis.pull_events()

    analysis.start(
        at=EPOCH,
    )

    return analysis


@final
class _MovingClock:
    """
    A clock at the epoch until the test moves it.
    """

    def __init__(
        self,
    ) -> None:
        self.at = EPOCH

    def now(
        self,
    ) -> datetime:
        return self.at


@final
class TestPausedAfter:
    def test_a_refusal_pauses_an_answer_resumes_and_any_other_failure_leaves_it_as_it_was(
        self,
    ) -> None:
        assert tuple(
            paused_after(
                paused=paused,
                lookup=lookup,
            )
            for (
                paused,
                lookup,
            ) in (
                (
                    False,
                    _REFUSED,
                ),
                (
                    True,
                    _TIMED_OUT,
                ),
                (
                    False,
                    _TIMED_OUT,
                ),
                (
                    True,
                    _ANSWERED,
                ),
            )
        ) == (
            True,
            True,
            False,
            False,
        )


@final
class TestStoredProgress:
    async def test_the_latest_report_is_written_once(
        self,
    ) -> None:
        analysis = _processing()

        storage = await _stored(
            analysis,
        )

        progress = StoredProgress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=4,
        )

        for lookup in (
            _ANSWERED,
            _REFUSED,
            _TIMED_OUT,
        ):
            progress.looked_up(
                lookup=lookup,
            )

        await progress.flush()

        written = dict(
            storage.progress,
        )

        # NOTE:
        # Nothing changed since the write, so a second flush must leave the store alone, which an emptied store shows.
        storage.progress.clear()

        await progress.flush()

        assert written == {
            analysis.id: ProcessingProgress(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=4,
                done=3,
                unavailable=2,
                dictionary_paused=True,
                reported_at=EPOCH,
                dictionary_waiting_words=0,
            ),
        }

        assert storage.progress == {}

    async def test_a_report_says_when_the_waiting_lookups_ask_again_and_how_long_the_stage_waits_at_most(
        self,
    ) -> None:
        analysis = _processing()

        storage = await _stored(
            analysis,
        )

        clock = _MovingClock()

        progress = StoredProgress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
            clock=clock,
        )

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=4,
        )

        progress.dictionary_waiting(
            held=_TIMED_OUT_WORD,
            ending=False,
            retry_in_seconds=30.0,
            patience_left_seconds=180.0,
        )

        # NOTE:
        # The report is written ten seconds later, and says ten seconds less of each wait.
        clock.at += timedelta(
            seconds=10,
        )

        await progress.flush()

        waiting = storage.progress[analysis.id]

        progress.dictionary_waiting(
            held=(),
            ending=False,
            retry_in_seconds=None,
            patience_left_seconds=150.0,
        )

        await progress.flush()

        resumed = storage.progress[analysis.id]

        assert (
            waiting.dictionary_retry_in_seconds,
            waiting.dictionary_patience_left_seconds,
        ) == (
            20.0,
            170.0,
        )

        assert (
            resumed.dictionary_retry_in_seconds,
            resumed.dictionary_patience_left_seconds,
        ) == (
            None,
            None,
        )

    async def test_a_report_on_an_analysis_no_longer_processing_is_dropped(
        self,
    ) -> None:
        analysis = completed_analysis()

        analysis.pull_events()

        storage = await _stored(
            analysis,
        )

        progress = StoredProgress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        progress.stage_started(
            stage=Stage.WINNOWING,
            steps=None,
        )

        await progress.flush()

        assert storage.progress == {}

    async def test_a_store_that_fails_is_logged_once_and_never_raised(
        self,
        *,
        caplog: LogCaptureFixture,
    ) -> None:
        analysis = _processing()

        def unreachable() -> InMemoryUnitOfWork:
            raise OSError(
                "the store is not there",
            )

        progress = StoredProgress(
            analysis_id=analysis.id,
            new_unit_of_work=unreachable,
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        with caplog.at_level(
            level=WARNING,
            logger="wordwinnow.progress",
        ):
            for _ in range(
                2,
            ):
                progress.stage_started(
                    stage=Stage.LINGUISTIC_ANALYSIS,
                    steps=None,
                )

                await progress.flush()

        assert tuple(
            (
                record.levelname,
                record.getMessage(),
                vars(
                    record,
                )["reason"],
            )
            for record in caplog.records
        ) == (
            (
                "WARNING",
                "progress.unrecorded",
                "OSError: the store is not there",
            ),
        )

    async def test_reports_reach_the_store_while_the_processing_runs_and_stop_after_it(
        self,
    ) -> None:
        analysis = _processing()

        storage = await _stored(
            analysis,
        )

        async with StoredProgress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
            clock=FakeClock(
                at=EPOCH,
            ),
            interval_seconds=0.0,
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=2,
            )

            progress.looked_up(
                lookup=_ANSWERED,
            )

            # TEST:
            # With no interval, each turn of the event loop gives the keeping task a chance to write.
            for _ in range(
                5,
            ):
                await asyncio_sleep(
                    delay=0,
                )

            during = storage.progress.get(
                analysis.id,
            )

        progress.looked_up(
            lookup=_ANSWERED,
        )

        for _ in range(
            5,
        ):
            await asyncio_sleep(
                delay=0,
            )

        after = storage.progress.get(
            analysis.id,
        )

        assert during is not None

        assert during.done == 1

        assert after == during


@final
class TestWatching:
    async def test_the_watch_hears_each_read_and_why_a_read_failed_and_nothing_after_the_block(
        self,
    ) -> None:
        reads: list[DictionaryActivity | ActivityUnreadableError] = [
            ActivityUnreadableError(
                "the enrichment service does not answer at http://enrichment.invalid",
            ),
            _IDLE,
        ]

        async def read() -> DictionaryActivity:
            outcome = (
                reads.pop(
                    0,
                )
                if len(
                    reads,
                )
                > 1
                else reads[0]
            )

            if isinstance(
                outcome,
                ActivityUnreadableError,
            ):
                raise outcome

            return outcome

        watch = RecordingWatch()

        async with watching(
            read=read,
            watch=watch,
            interval_seconds=0.0,
        ):
            while (
                len(
                    watch.heard,
                )
                < 3
            ):
                await asyncio_sleep(
                    delay=0,
                )

        heard = len(
            watch.heard,
        )

        await asyncio_sleep(
            delay=0.01,
        )

        assert watch.heard[:3] == [
            "the enrichment service does not answer at http://enrichment.invalid",
            "circuit closed",
            "circuit closed",
        ]

        assert (
            len(
                watch.heard,
            )
            == heard
        )


def _state_of(
    *,
    held: Sequence[HeldWords],
    steps: int = 40,
    done: int = 0,
    paused: bool = False,
    left: float = 150.0,
) -> DictionaryWaitState:
    return dictionary_wait_of(
        held=held,
        patience_left_seconds=left,
        steps=steps,
        done=done,
        paused=paused,
    )


def _state(
    wait: DictionaryWait | None,
    words: int,
    next_in: float | None,
    bound: float | None,
    /,
) -> DictionaryWaitState:
    return DictionaryWaitState(
        wait=wait,
        words=words,
        next_in_seconds=next_in,
        wordnet_within_seconds=bound,
    )


@final
class TestDictionaryWaitOf:
    def test_nothing_held_says_no_wait_and_a_paused_dictionary_alone_says_the_same_to_be_drawn_as_paused(
        self,
    ) -> None:
        assert _state_of(
            held=(),
        ) == _state(
            None,
            0,
            None,
            None,
        )

        assert _state_of(
            held=(),
            paused=True,
        ) == _state(
            None,
            0,
            None,
            None,
        )

    def test_words_the_circuit_refused_name_its_next_check_and_count_every_word_that_waits(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.CIRCUIT_OPEN,
                    turned_away=5,
                    turned_away_wake_in_seconds=9.0,
                ),
                held_words(
                    reason=FailureReason.RATE_LIMITED,
                    turned_away=3,
                    turned_away_wake_in_seconds=45.0,
                ),
                held_words(
                    reason=FailureReason.PROVIDER_ERROR,
                    turned_away=2,
                    turned_away_wake_in_seconds=20.0,
                    longest_wait_seconds=20.0,
                ),
            ),
            paused=True,
        ) == _state(
            DictionaryWait.NOT_ANSWERING,
            10,
            9.0,
            150.0,
        )

    def test_the_circuits_next_check_is_the_wake_of_a_counted_word_and_not_of_one_answered_since(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.CIRCUIT_OPEN,
                    turned_away=2,
                    turned_away_wake_in_seconds=9.0,
                    others_sleeping=1,
                    wake_in_seconds=3.0,
                ),
            ),
        ) == _state(
            DictionaryWait.NOT_ANSWERING,
            2,
            9.0,
            150.0,
        )

    def test_a_trial_in_flight_has_no_next_check_and_keeps_the_circuits_wait(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.CIRCUIT_OPEN,
                    turned_away=5,
                    turned_away_wake_in_seconds=None,
                ),
            ),
        ) == _state(
            DictionaryWait.NOT_ANSWERING,
            5,
            None,
            150.0,
        )

    def test_a_pause_comes_before_the_dictionarys_paused_state_and_after_the_circuit(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.RATE_LIMITED,
                    turned_away=4,
                    turned_away_wake_in_seconds=45.0,
                ),
            ),
            paused=True,
        ) == _state(
            DictionaryWait.ASKED_TO_PAUSE,
            4,
            45.0,
            150.0,
        )

        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.RATE_LIMITED,
                    turned_away=4,
                    turned_away_wake_in_seconds=None,
                ),
            ),
        ) == _state(
            DictionaryWait.ASKED_TO_PAUSE,
            4,
            None,
            150.0,
        )

    def test_words_of_the_circuit_answered_since_no_longer_count_even_while_they_sleep(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.CIRCUIT_OPEN,
                    turned_away=0,
                    turned_away_wake_in_seconds=None,
                    others_sleeping=3,
                    wake_in_seconds=9.0,
                ),
            ),
        ) == _state(
            None,
            0,
            None,
            None,
        )

    def test_a_dictionary_paused_by_refusals_comes_before_the_words_own_waits(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.PROVIDER_ERROR,
                    turned_away=1,
                    turned_away_wake_in_seconds=90.0,
                    longest_wait_seconds=90.0,
                ),
            ),
            steps=1,
            paused=True,
        ) == _state(
            None,
            0,
            None,
            None,
        )

    def test_words_that_are_all_that_is_left_name_the_earliest_wake_of_the_sleeping_ones(
        self,
    ) -> None:
        assert _state_of(
            held=(
                held_words(
                    reason=FailureReason.PROVIDER_ERROR,
                    turned_away=2,
                    turned_away_wake_in_seconds=50.0,
                    others_sleeping=1,
                    wake_in_seconds=22.0,
                    longest_wait_seconds=90.0,
                ),
            ),
            steps=40,
            done=37,
        ) == _state(
            DictionaryWait.WAITING_WORDS,
            3,
            22.0,
            150.0,
        )

    def test_wordnets_meaning_is_named_for_a_wait_longer_than_the_clients_own_or_a_word_that_waits_again(
        self,
    ) -> None:
        for (
            longest,
            repeated,
            bound,
        ) in (
            (
                30.0,
                False,
                None,
            ),
            (
                30.1,
                False,
                150.0,
            ),
            (
                2.0,
                True,
                150.0,
            ),
        ):
            assert _state_of(
                held=(
                    held_words(
                        reason=FailureReason.PROVIDER_ERROR,
                        turned_away=1,
                        turned_away_wake_in_seconds=2.0,
                        longest_wait_seconds=longest,
                        repeated=repeated,
                    ),
                ),
                steps=1,
            ) == _state(
                DictionaryWait.WAITING_WORDS,
                1,
                2.0,
                bound,
            )

    def test_some_words_wait_while_others_are_still_asked_or_while_none_of_them_sleeps(
        self,
    ) -> None:
        for wake in (
            30.0,
            None,
        ):
            assert _state_of(
                held=(
                    held_words(
                        reason=FailureReason.PROVIDER_ERROR,
                        turned_away=2,
                        turned_away_wake_in_seconds=wake,
                        longest_wait_seconds=90.0,
                    ),
                ),
                steps=2 if wake is None else 40,
            ) == _state(
                DictionaryWait.SOME_WORDS,
                2,
                None,
                None,
            )
