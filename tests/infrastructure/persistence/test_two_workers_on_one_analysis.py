"""
Two workers on one analysis at once, through the use case and the SQLite
store.

A worker that holds a record longer than the broker's poll interval, a
rebalance that outlasts the handoff, and a requeue of an analysis still
processing each give one analysis to a second worker while the first is still
on it, so the two runs can meet at any point of either.

Each meeting is forced with events rather than left to timing: a held
dictionary keeps a run in its pipeline, and a held unit of work keeps a run,
or the requeue, between reading the analysis and writing it.
"""

from asyncio import (
    Event,
    create_task,
)
from datetime import (
    datetime,
    timedelta,
)
from types import (
    TracebackType,
)
from typing import (
    Final,
    Self,
    final,
)

from pytest import (
    raises,
)

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    FakeLevelEstimator,
    FakeLexicalSemantics,
    FakeLinguisticAnalyzer,
    FakeReferenceLexicon,
    FakeWordFrequency,
    RecordingPublisher,
)
from tests.infrastructure.persistence.conftest import (
    NewUnitOfWork,
    load_analysis,
)
from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
)
from wordwinnow.application.ports.analysis_repository import (
    AnalysisRepository,
)
from wordwinnow.application.ports.uow import (
    UnitOfWork,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    Pipeline,
    process_an_analysis,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.application.use_cases.requeue_stale_analyses import (
    requeue_stale_analyses,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisCompleted,
    AnalysisOptions,
    AnalysisStatus,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)

_DOCUMENT: Final = Document(
    title="groom",
    text="The groom sauntered.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="groom.txt",
)

_NOT_FOUND: Final = DictionaryLookup(
    outcome=LookupOutcome.NOT_FOUND,
)

_UNAVAILABLE: Final = DictionaryLookup(
    outcome=LookupOutcome.UNAVAILABLE,
    failure_reason="timed_out",
)


@final
class _HeldDictionary:
    """
    A dictionary whose lookups wait until the test lets them through, then
    answer one lookup for every lemma, or raise when told to.
    """

    def __init__(
        self,
        *,
        lookup: DictionaryLookup,
        crash: bool = False,
    ) -> None:
        self._lookup: Final = lookup

        self._crash: Final = crash

        self.entered: Final = Event()

        self.released: Final = Event()

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        self.entered.set()

        await self.released.wait()

        if self._crash:
            raise RuntimeError(
                f"the provider crashed on {lemma!r}",
            )

        return self._lookup


@final
class _ReadHeldRepository:
    """
    The store's analyses, except that a read of one analysis, or of the stale
    ones, waits until the test lets it go on.
    """

    def __init__(
        self,
        *,
        inner: AnalysisRepository,
        read: Event,
        released: Event,
    ) -> None:
        self._inner: Final = inner

        self._read: Final = read

        self._released: Final = released

    async def add(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        await self._inner.add(
            analysis=analysis,
        )

    async def get(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> Analysis | None:
        analysis = await self._inner.get(
            analysis_id=analysis_id,
        )

        await self._hold()

        return analysis

    async def save(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        await self._inner.save(
            analysis=analysis,
        )

    async def save_unless_completed(
        self,
        *,
        analysis: Analysis,
    ) -> bool:
        return await self._inner.save_unless_completed(
            analysis=analysis,
        )

    async def save_while_processing(
        self,
        *,
        analysis: Analysis,
        started_at: datetime,
    ) -> bool:
        return await self._inner.save_while_processing(
            analysis=analysis,
            started_at=started_at,
        )

    async def record_progress(
        self,
        *,
        analysis_id: AnalysisId,
        progress: ProcessingProgress,
    ) -> None:
        await self._inner.record_progress(
            analysis_id=analysis_id,
            progress=progress,
        )

    async def progress_of(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> ProcessingProgress | None:
        return await self._inner.progress_of(
            analysis_id=analysis_id,
        )

    async def list_recent(
        self,
        *,
        limit: int,
    ) -> tuple[AnalysisSummary, ...]:
        return await self._inner.list_recent(
            limit=limit,
        )

    async def list_stale(
        self,
        *,
        stale_before: datetime,
    ) -> tuple[Analysis, ...]:
        stale = await self._inner.list_stale(
            stale_before=stale_before,
        )

        await self._hold()

        return stale

    async def delete_expired(
        self,
        *,
        now: datetime,
    ) -> int:
        return await self._inner.delete_expired(
            now=now,
        )

    async def _hold(
        self,
    ) -> None:
        self._read.set()

        await self._released.wait()


@final
class _ReadHeldUnitOfWork:
    """
    A unit of work over the store whose reads wait until the test lets them go
    on.
    """

    def __init__(
        self,
        *,
        inner: UnitOfWork,
        read: Event,
        released: Event,
    ) -> None:
        self._inner: Final = inner

        self._read: Final = read

        self._released: Final = released

    @property
    def analyses(
        self,
    ) -> AnalysisRepository:
        return _ReadHeldRepository(
            inner=self._inner.analyses,
            read=self._read,
            released=self._released,
        )

    async def __aenter__(
        self,
    ) -> Self:
        await self._inner.__aenter__()

        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        await self._inner.__aexit__(
            exception_type,
            exception,
            traceback,
        )

    async def commit(
        self,
    ) -> None:
        await self._inner.commit()


@final
class _FirstReadHeld:
    """
    Units of work over the store, the first of which waits once it has read
    until the test lets it go on.

    A run's first unit of work reads the analysis and stores its start, and
    the requeue's only one reads the stale analyses and hands them back, so
    the wait falls between a read and the write that relies on it.
    """

    def __init__(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        self._new_unit_of_work: Final = new_unit_of_work

        self.read: Final = Event()

        self.released: Final = Event()

        self._handed_out = False

    def __call__(
        self,
    ) -> UnitOfWork:
        if self._handed_out:
            return self._new_unit_of_work()

        self._handed_out = True

        return _ReadHeldUnitOfWork(
            inner=self._new_unit_of_work(),
            read=self.read,
            released=self.released,
        )


def _pipeline(
    *,
    dictionary: _HeldDictionary,
) -> Pipeline:
    return Pipeline(
        linguistics=FakeLinguisticAnalyzer(
            function_words=frozenset(
                ("the",),
            ),
        ),
        frequency=FakeWordFrequency(),
        reference_lexicon=FakeReferenceLexicon(),
        level_estimator=FakeLevelEstimator(),
        dictionary=dictionary,
        lexical_semantics=FakeLexicalSemantics(),
    )


def _outcomes(
    analysis: Analysis | None,
    /,
) -> tuple[LookupOutcome | None, ...]:
    assert analysis is not None

    return tuple(
        value_object.outcome if (value_object := item.dictionary) is not None else None for item in analysis.items
    )


async def _requested(
    *,
    new_unit_of_work: NewUnitOfWork,
    publisher: RecordingPublisher,
) -> AnalysisId:
    analysis = await request_an_analysis(
        document=_DOCUMENT,
        profile=LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
        ),
        options=AnalysisOptions(
            include_dictionary=True,
        ),
        new_unit_of_work=new_unit_of_work,
        publisher=publisher,
        clock=FakeClock(
            at=EPOCH,
        ),
    )

    return analysis.id


def _completions(
    publisher: RecordingPublisher,
    /,
) -> tuple[AnalysisCompleted, ...]:
    return tuple(
        event
        for (
            _,
            event,
        ) in publisher.published
        if isinstance(
            event,
            AnalysisCompleted,
        )
    )


@final
class TestTwoWorkersOnOneAnalysis:
    async def test_the_first_completion_stands_when_a_second_run_finishes_later(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        first_dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
        )

        second_dictionary = _HeldDictionary(
            lookup=_UNAVAILABLE,
        )

        first = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=first_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                    ),
                ),
            ),
        )

        await first_dictionary.entered.wait()

        # NOTE:
        # The second worker finds the analysis processing, takes it for one a dead worker left, and starts it again.
        second = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=second_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                ),
            ),
        )

        await second_dictionary.entered.wait()

        first_dictionary.released.set()

        await first

        after_first = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert after_first is not None

        assert after_first.status is AnalysisStatus.COMPLETED

        assert set(
            _outcomes(
                after_first,
            ),
        ) == {
            LookupOutcome.NOT_FOUND,
        }

        second_dictionary.released.set()

        await second

        after_second = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert _outcomes(
            after_second,
        ) == _outcomes(
            after_first,
        )

        assert after_second is not None

        assert after_second.finished_at == after_first.finished_at

        assert (
            len(
                _completions(
                    publisher,
                ),
            )
            == 1
        )

    async def test_a_second_run_that_fails_leaves_a_completed_analysis_completed(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        first_dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
        )

        second_dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
            crash=True,
        )

        first = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=first_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                    ),
                ),
            ),
        )

        await first_dictionary.entered.wait()

        second = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=second_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                ),
            ),
        )

        await second_dictionary.entered.wait()

        first_dictionary.released.set()

        await first

        second_dictionary.released.set()

        with raises(
            expected_exception=RuntimeError,
        ):
            await second

        stored = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert stored is not None

        assert stored.status is AnalysisStatus.COMPLETED

    async def test_a_run_that_finds_the_analysis_completed_as_it_starts_publishes_that_completion_and_stops(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        first_dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
        )

        second_dictionary = _HeldDictionary(
            lookup=_UNAVAILABLE,
        )

        # TEST:
        # Open from the start, so that a second run that went on into the pipeline would finish rather than wait.
        second_dictionary.released.set()

        second_units_of_work = _FirstReadHeld(
            new_unit_of_work=new_unit_of_work,
        )

        first = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=first_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                    ),
                ),
            ),
        )

        await first_dictionary.entered.wait()

        second = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=second_dictionary,
                ),
                new_unit_of_work=second_units_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                ),
            ),
        )

        # NOTE:
        # The second run has read the analysis processing, and the first completes it before the second stores its own
        # start.
        await second_units_of_work.read.wait()

        first_dictionary.released.set()

        await first

        completed = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        second_units_of_work.released.set()

        processed = await second

        stored = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert completed is not None

        assert stored is not None

        assert processed.redelivered

        assert not second_dictionary.entered.is_set()

        assert stored.finished_at == completed.finished_at

        assert _outcomes(
            stored,
        ) == _outcomes(
            completed,
        )

        assert _completions(
            publisher,
        ) == (
            completed.completion(),
            completed.completion(),
        )

    async def test_a_requeue_leaves_an_analysis_completed_after_it_was_found_stale_as_it_is(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
        )

        run = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                    ),
                ),
            ),
        )

        await dictionary.entered.wait()

        requeue_units_of_work = _FirstReadHeld(
            new_unit_of_work=new_unit_of_work,
        )

        requeue_publisher = RecordingPublisher()

        # NOTE:
        # An hour after the run started, the requeue finds it processing for longer than ten minutes, and the run
        # completes the analysis before the requeue hands it back.
        requeue = create_task(
            coro=requeue_stale_analyses(
                older_than_seconds=600,
                new_unit_of_work=requeue_units_of_work,
                publisher=requeue_publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        hours=1,
                    ),
                ),
            ),
        )

        await requeue_units_of_work.read.wait()

        dictionary.released.set()

        await run

        completed = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        requeue_units_of_work.released.set()

        requeued = await requeue

        stored = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert completed is not None

        assert stored is not None

        assert requeued == ()

        assert requeue_publisher.published == []

        assert stored.status is AnalysisStatus.COMPLETED

        assert stored.finished_at == completed.finished_at

        assert _outcomes(
            stored,
        ) == _outcomes(
            completed,
        )

    async def test_a_requeue_leaves_an_analysis_started_again_after_it_was_found_stale_to_its_new_run(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        # NOTE:
        # A run stored its start a second after the request and stopped, as a worker that died would.
        async with new_unit_of_work() as uow:
            abandoned = await uow.analyses.get(
                analysis_id=analysis_id,
            )

            assert abandoned is not None

            abandoned.start(
                at=EPOCH
                + timedelta(
                    seconds=1,
                ),
            )

            await uow.analyses.save(
                analysis=abandoned,
            )

            await uow.commit()

        requeue_units_of_work = _FirstReadHeld(
            new_unit_of_work=new_unit_of_work,
        )

        requeue_publisher = RecordingPublisher()

        requeue = create_task(
            coro=requeue_stale_analyses(
                older_than_seconds=600,
                new_unit_of_work=requeue_units_of_work,
                publisher=requeue_publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        hours=1,
                    ),
                ),
            ),
        )

        await requeue_units_of_work.read.wait()

        # NOTE:
        # The record is delivered again after the requeue found the analysis stale, and the new run stores its start
        # before the requeue hands the analysis back.
        dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
        )

        run = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        hours=1,
                    ),
                ),
            ),
        )

        await dictionary.entered.wait()

        requeue_units_of_work.released.set()

        requeued = await requeue

        while_running = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        dictionary.released.set()

        await run

        stored = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert while_running is not None

        assert stored is not None

        assert requeued == ()

        assert requeue_publisher.published == []

        assert while_running.status is AnalysisStatus.PROCESSING

        assert while_running.started_at == EPOCH + timedelta(
            hours=1,
        )

        assert stored.status is AnalysisStatus.COMPLETED

        assert (
            len(
                _completions(
                    publisher,
                ),
            )
            == 1
        )

    async def test_a_run_that_another_run_started_again_stores_no_failure_while_that_run_works(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        failing_dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
            crash=True,
        )

        working_dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
        )

        failing = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=failing_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                    ),
                ),
            ),
        )

        await failing_dictionary.entered.wait()

        # TEST:
        # The second run starts a microsecond after the first, the least two starts can differ by and the most both
        # stores keep, so a store that kept less would give the two runs one start and store the first one's failure.
        working = create_task(
            coro=process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=working_dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                        microseconds=1,
                    ),
                ),
            ),
        )

        await working_dictionary.entered.wait()

        failing_dictionary.released.set()

        with raises(
            expected_exception=RuntimeError,
        ):
            await failing

        while_working = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        working_dictionary.released.set()

        await working

        stored = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert while_working is not None

        assert stored is not None

        assert while_working.status is AnalysisStatus.PROCESSING

        assert stored.status is AnalysisStatus.COMPLETED

        assert (
            len(
                _completions(
                    publisher,
                ),
            )
            == 1
        )

    async def test_a_run_still_on_its_analysis_stores_its_failure(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        publisher = RecordingPublisher()

        analysis_id = await _requested(
            new_unit_of_work=new_unit_of_work,
            publisher=publisher,
        )

        dictionary = _HeldDictionary(
            lookup=_NOT_FOUND,
            crash=True,
        )

        dictionary.released.set()

        # TEST:
        # The run's start carries a microsecond, so its failure is stored only if the store gives the start back
        # exactly as the run took it.
        with raises(
            expected_exception=RuntimeError,
        ):
            await process_an_analysis(
                analysis_id=analysis_id,
                pipeline=_pipeline(
                    dictionary=dictionary,
                ),
                new_unit_of_work=new_unit_of_work,
                publisher=publisher,
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        seconds=1,
                        microseconds=1,
                    ),
                ),
            )

        stored = await load_analysis(
            analysis_id=analysis_id,
            new_unit_of_work=new_unit_of_work,
        )

        assert stored is not None

        assert stored.status is AnalysisStatus.FAILED

        assert (
            _completions(
                publisher,
            )
            == ()
        )
