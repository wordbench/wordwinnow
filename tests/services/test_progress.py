"""
When the dictionary counts as paused, how a worker's progress reaches the
analysis store for a command waiting elsewhere, and how a view of the
dictionary hears what it is doing.
"""

from asyncio import (
    sleep as asyncio_sleep,
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
)
from tests.infrastructure.messaging.analyses import (
    completed_analysis,
    requested_analysis,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
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
    StoredProgress,
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
