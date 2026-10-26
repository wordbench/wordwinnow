"""
The pipeline: turn a requested analysis into study items.

Each stage is timed, and the timings are stored with the result, because
performance is part of what the analysis reports.
"""

from asyncio import (
    Semaphore,
    Timeout,
    gather,
    timeout,
    to_thread,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    Awaitable,
    Callable,
    Generator,
    Sequence,
)
from contextlib import (
    contextmanager,
)
from dataclasses import (
    dataclass,
)
from time import (
    monotonic,
    perf_counter,
)
from typing import (
    Final,
    final,
)

from wordwinnow.application.errors import (
    AnalysisNotFoundError,
)
from wordwinnow.application.ports.analysis_event_publisher import (
    AnalysisEventPublisher,
)
from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.dictionary import (
    Dictionary,
)
from wordwinnow.application.ports.level_estimator import (
    LevelEstimator,
)
from wordwinnow.application.ports.lexical_semantics import (
    LexicalSemantics,
)
from wordwinnow.application.ports.linguistic_analyzer import (
    LinguisticAnalyzer,
)
from wordwinnow.application.ports.pipeline_progress import (
    PipelineProgress,
)
from wordwinnow.application.ports.reference_lexicon import (
    ReferenceLexicon,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)
from wordwinnow.application.ports.word_frequency import (
    WordFrequency,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Stage,
    StageTiming,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    assess_level,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
    select_definitions,
)
from wordwinnow.domain.difficulty import (
    build_lexical_features,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    EnrichedItem,
    StudyItem,
    study_tier,
    tier_order,
    winnow,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
    collect_vocabulary,
)

# NOTE:
# How many definitions a learner sees per word.
#
# Three is enough to show a word's range without turning the report into a dictionary page.
DEFINITIONS_PER_ITEM: Final = 3

# NOTE:
# How many senses a learner sees per word, most used first.
SENSES_PER_ITEM: Final = 3

# NOTE:
# The shortest wait, so a lookup told to ask again at once still lets the clock move before it asks.
_SHORTEST_WAIT_SECONDS: Final = 1.0


@final
class _Unwatched:
    """
    The progress of a pipeline nobody watches.
    """

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        return

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        return

    def dictionary_waiting(
        self,
        *,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        return


_UNWATCHED: Final = _Unwatched()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Pipeline:
    """
    Everything `process_an_analysis` needs besides the analysis itself.

    `dictionary_concurrency` bounds how many dictionary lookups are in flight
    at once.

    `progress` hears how far a run has come; a pipeline built without one
    tells nobody.

    `dictionary_patience_seconds` is how long the dictionary stage may wait,
    in all, for a dictionary that asked to be asked again later, and nought,
    as a pipeline built without it has, ends such a lookup at once; `clock`
    and `sleep` time those waits.
    """

    linguistics: LinguisticAnalyzer

    frequency: WordFrequency

    reference_lexicon: ReferenceLexicon

    level_estimator: LevelEstimator

    dictionary: Dictionary

    lexical_semantics: LexicalSemantics

    dictionary_concurrency: int = 8

    progress: PipelineProgress = _UNWATCHED

    dictionary_patience_seconds: float = 0.0

    clock: Callable[[], float] = monotonic

    sleep: Callable[[float], Awaitable[None]] = asyncio_sleep


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ProcessedAnalysis:
    """
    What `process_an_analysis` hands back: the analysis, and whether the
    request was a redelivery of one already completed, which the caller should
    count once rather than twice.
    """

    analysis: Analysis

    redelivered: bool


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class PipelineResult:
    """
    What the pipeline produced for one document and one profile.
    """

    items: tuple[StudyItem, ...]

    stage_timings: tuple[StageTiming, ...]


async def run_pipeline(
    *,
    text: str,
    profile: LearnerProfile,
    include_dictionary: bool,
    pipeline: Pipeline,
) -> PipelineResult:
    """
    Run every stage over `text` for `profile` and time each one.

    This is the analysis without its lifecycle, so the local mode and the
    experiments can run it directly.
    """

    timings: list[StageTiming] = []

    progress = pipeline.progress

    started = perf_counter()

    progress.stage_started(
        stage=Stage.LINGUISTIC_ANALYSIS,
        steps=None,
    )

    # PERF:
    # Tokenizing and tagging are CPU-bound and grow with every word of the text, so a long one would hold the event
    # loop for seconds; in a worker thread they leave the loop free to answer its other waits, a worker's heartbeat to
    # the broker among them.
    #
    # The stages after them stay on the loop: they grow only with the distinct words, which grow far more slowly.
    sentences = await to_thread(
        pipeline.linguistics.analyze,
        text=text,
    )

    started = _record(
        timings,
        Stage.LINGUISTIC_ANALYSIS,
        started,
    )

    progress.stage_started(
        stage=Stage.VOCABULARY_SELECTION,
        steps=None,
    )

    items = collect_vocabulary(
        sentences=sentences,
        function_words=pipeline.linguistics.function_words,
    )

    started = _record(
        timings,
        Stage.VOCABULARY_SELECTION,
        started,
    )

    progress.stage_started(
        stage=Stage.LEXICAL_SEMANTICS,
        steps=None,
    )

    senses = tuple(
        pipeline.lexical_semantics.senses_of(
            lemma=item.lemma,
            part_of_speech=item.part_of_speech,
        )
        for item in items
    )

    started = _record(
        timings,
        Stage.LEXICAL_SEMANTICS,
        started,
    )

    progress.stage_started(
        stage=Stage.LEVEL_ASSESSMENT,
        steps=None,
    )

    frequencies = tuple(
        pipeline.frequency.zipf(
            lemma=item.lemma,
        )
        for item in items
    )

    levels = _assess_all(
        items,
        frequencies,
        senses,
        pipeline,
    )

    started = _record(
        timings,
        Stage.LEVEL_ASSESSMENT,
        started,
    )

    dictionary_information: tuple[DictionaryInformation | None, ...]

    if include_dictionary:
        # NOTE:
        # The only stage whose size is known before it runs and whose length a person notices: one lookup per item,
        # each of which may wait for the provider's budget.
        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=len(
                items,
            ),
        )

        # NOTE:
        # The words a learner studies first are asked first, the more frequent in the text before the rest, so a
        # provider that stops answering late, or a wait that runs out, costs the words that matter least; the report
        # keeps its own order whatever this one is.
        tiers = tuple(
            study_tier(
                lemma=item.lemma,
                level=level,
                profile=profile,
            )
            for (
                item,
                level,
            ) in zip(
                items,
                levels,
                strict=True,
            )
        )

        dictionary_information = await _look_up_all(
            items,
            sorted(
                range(
                    len(
                        items,
                    ),
                ),
                key=lambda index: (
                    tier_order(
                        tier=tiers[index],
                    ),
                    -items[index].occurrence_count,
                ),
            ),
            pipeline,
        )

    else:
        dictionary_information = tuple(None for _ in items)

    started = _record(
        timings,
        Stage.DICTIONARY_ENRICHMENT,
        started,
    )

    progress.stage_started(
        stage=Stage.WINNOWING,
        steps=None,
    )

    enriched = tuple(
        EnrichedItem(
            item=item,
            zipf_frequency=zipf_frequency,
            level=level,
            dictionary=information,
            senses=item_senses[:SENSES_PER_ITEM],
        )
        for (
            item,
            zipf_frequency,
            level,
            information,
            item_senses,
        ) in zip(
            items,
            frequencies,
            levels,
            dictionary_information,
            senses,
            strict=True,
        )
    )

    winnowed = winnow(
        enriched=enriched,
        profile=profile,
    )

    _record(
        timings,
        Stage.WINNOWING,
        started,
    )

    return PipelineResult(
        items=winnowed,
        stage_timings=tuple(
            timings,
        ),
    )


async def process_an_analysis(
    *,
    analysis_id: AnalysisId,
    pipeline: Pipeline,
    new_unit_of_work: UnitOfWorkFactory,
    publisher: AnalysisEventPublisher,
    clock: Clock,
) -> ProcessedAnalysis:
    """
    Run the pipeline for a stored analysis and record the outcome.

    Processing is idempotent under redelivery: a completed analysis is
    returned as it is and its completion published again, and an analysis left
    processing by a worker that died is failed, reopened, and processed again.

    The first run to complete an analysis decides its result: a run that finds
    it completed only as it stores its own start is a redelivery too, and a
    run that ends after another run completed it stores and publishes nothing,
    returning the stored analysis if it completed and raising its error if it
    failed.

    A run that fails after another run has started the analysis again stores
    nothing either, so the analysis is not shown as failed while that run is
    still working on it.
    """

    async with new_unit_of_work() as uow:
        analysis = await uow.analyses.get(
            analysis_id=analysis_id,
        )

        if analysis is None:
            raise AnalysisNotFoundError(
                f"no analysis {analysis_id}",
            )

        if analysis.status is AnalysisStatus.COMPLETED:
            return await _published_again(
                analysis,
                publisher,
            )

        if analysis.status is AnalysisStatus.PROCESSING:
            analysis.fail(
                at=clock.now(),
                reason="processing was interrupted",
            )

            analysis.reopen(
                at=clock.now(),
            )

        if analysis.status is AnalysisStatus.FAILED:
            analysis.reopen(
                at=clock.now(),
            )

        analysis.pull_events()

        started_at = clock.now()

        analysis.start(
            at=started_at,
        )

        # WARN:
        # Another run can complete the analysis after the read above, and an unconditional save would turn its result
        # back into a run in progress, so the start is stored only while the analysis is not completed.
        started = await uow.analyses.save_unless_completed(
            analysis=analysis,
        )

        if started:
            await uow.commit()

    if not started:
        async with new_unit_of_work() as uow:
            completed = await uow.analyses.get(
                analysis_id=analysis_id,
            )

        if completed is None:
            raise AnalysisNotFoundError(
                f"no analysis {analysis_id}",
            )

        return await _published_again(
            completed,
            publisher,
        )

    try:
        result = await run_pipeline(
            text=analysis.document.text,
            profile=analysis.profile,
            include_dictionary=analysis.options.include_dictionary,
            pipeline=pipeline,
        )

    except Exception as exception:
        kind = type(
            exception,
        ).__name__

        analysis.fail(
            at=clock.now(),
            reason=f"{kind}: {exception}",
        )

        # NOTE:
        # The failure is stored only while the analysis is still this run's: a run whose analysis another run has
        # started again, completed, or handed back since leaves it to that run, and a failure publishes no event.
        async with new_unit_of_work() as uow:
            if await uow.analyses.save_while_processing(
                analysis=analysis,
                started_at=started_at,
            ):
                await uow.commit()

        raise

    analysis.complete(
        at=clock.now(),
        items=result.items,
        stage_timings=result.stage_timings,
    )

    if not await _save_and_publish(
        analysis,
        new_unit_of_work,
        publisher,
    ):
        async with new_unit_of_work() as uow:
            stored = await uow.analyses.get(
                analysis_id=analysis.id,
            )

        return ProcessedAnalysis(
            analysis=stored if stored is not None else analysis,
            redelivered=True,
        )

    return ProcessedAnalysis(
        analysis=analysis,
        redelivered=False,
    )


async def _published_again(
    analysis: Analysis,
    publisher: AnalysisEventPublisher,
    /,
) -> ProcessedAnalysis:
    # WARN:
    # The result is committed before its completion is published, so a worker can stop, or the broker refuse, in
    # between; the requested message then arrives again and finds a completed analysis, and publishing the completion
    # once more is what keeps it from being lost, while the fact store keeps one row per fact.
    await publisher.publish(
        analysis=analysis,
        event=analysis.completion(),
    )

    return ProcessedAnalysis(
        analysis=analysis,
        redelivered=True,
    )


async def _save_and_publish(
    analysis: Analysis,
    new_unit_of_work: UnitOfWorkFactory,
    publisher: AnalysisEventPublisher,
    /,
) -> bool:
    # NOTE:
    # Two runs of one analysis can overlap when a second worker takes it while the first is still on it, and the store
    # keeps whichever run completes first; a run that finds the analysis completed by the other leaves its result and
    # its completion as they are, so a reader never sees a completed result change or turn into a failure.
    async with new_unit_of_work() as uow:
        if not await uow.analyses.save_unless_completed(
            analysis=analysis,
        ):
            return False

        await uow.commit()

    for event in analysis.pull_events():
        await publisher.publish(
            analysis=analysis,
            event=event,
        )

    return True


def _record(
    timings: list[StageTiming],
    stage: Stage,
    started: float,
    /,
) -> float:
    now = perf_counter()

    timings.append(
        StageTiming(
            stage=stage,
            seconds=now - started,
        ),
    )

    return now


def _assess_all(
    items: Sequence[VocabularyItem],
    frequencies: Sequence[float],
    senses: Sequence[Sequence[LexicalSense]],
    pipeline: Pipeline,
    /,
) -> tuple[LevelAssessment, ...]:
    reference_levels = tuple(
        pipeline.reference_lexicon.level_of(
            lemma=item.lemma,
            part_of_speech=item.part_of_speech,
        )
        for item in items
    )

    # PERF:
    # The model is asked once for every word the lists do not cover, because one prediction costs about the same for
    # one word as for a thousand: `wordwinnow bench stages` measures level assessment with the model at 0.008 to
    # 0.010 s whether the sample story contributes 608 vocabulary items or 1,509.
    unlisted = tuple(
        index
        for (
            index,
            reference_level,
        ) in enumerate(
            iterable=reference_levels,
        )
        if reference_level is None
    )

    model_levels: dict[int, CefrLevel | None] = {}

    if unlisted:
        estimated = pipeline.level_estimator.estimate_many(
            features=tuple(
                build_lexical_features(
                    lemma=items[index].lemma,
                    part_of_speech=items[index].part_of_speech,
                    zipf_frequency=frequencies[index],
                    senses=senses[index],
                )
                for index in unlisted
            ),
        )

        model_levels = dict(
            zip(
                unlisted,
                estimated,
                strict=True,
            ),
        )

    return tuple(
        assess_level(
            reference_level=reference_level,
            model_level=model_levels.get(
                index,
            ),
            zipf_frequency=frequencies[index],
        )
        for (
            index,
            reference_level,
        ) in enumerate(
            iterable=reference_levels,
        )
    )


async def _look_up_all(
    items: Sequence[VocabularyItem],
    order: Sequence[int],
    pipeline: Pipeline,
    /,
) -> tuple[DictionaryInformation, ...]:
    """
    Look the items up in the order `order` gives, as many at a time as the
    pipeline allows, and hand the answers back in the items' own order.
    """

    semaphore = Semaphore(
        value=pipeline.dictionary_concurrency,
    )

    patience = _Patience(
        seconds=pipeline.dictionary_patience_seconds,
        clock=pipeline.clock,
        sleep=pipeline.sleep,
        progress=pipeline.progress,
    )

    async def look_up(
        item: VocabularyItem,
        /,
    ) -> DictionaryInformation:
        # NOTE:
        # A word the dictionary turned away with a time to ask again holds the analysis's waiting time from that
        # moment until it is answered or let go, so the time it spends asking again, waiting for a trial's outcome
        # included, counts as well as the time it sleeps.
        held = False

        try:
            async with semaphore:
                lookup = await pipeline.dictionary.look_up(
                    lemma=item.lemma,
                )

            while (
                wait := patience.wait_for(
                    retry_after_seconds=lookup.retry_after_seconds,
                )
            ) is not None:
                if not held:
                    patience.hold()

                    held = True

                await patience.sleep(
                    seconds=wait,
                )

                # NOTE:
                # A waiting lookup gives its place back and takes one again to ask: the words the cache can answer go
                # on meanwhile, and the lookups that wake together still ask the dictionary only as many at a time as
                # an analysis does.
                #
                # While the dictionary answers none of the waiting words, it asks within the waiting time left, so a
                # request that hangs, or a trial whose outcome is slow to come, never keeps a waiting word past the
                # moment the waiting time runs out; a word cut off then keeps the refusal it last had, and WordNet
                # stands in.
                #
                # The deadline is read on the loop's clock, which is the monotonic clock the pipeline's own defaults
                # to, and the virtual one a test gives both.
                deadline = timeout(
                    delay=patience.time_to_ask(),
                )

                try:
                    async with deadline:
                        with patience.bounding(
                            deadline=deadline,
                        ):
                            async with semaphore:
                                lookup = await pipeline.dictionary.look_up(
                                    lemma=item.lemma,
                                )

                except TimeoutError:
                    if not deadline.expired():
                        raise

                    break

                if lookup.outcome is not LookupOutcome.UNAVAILABLE:
                    patience.answered()

        finally:
            if held:
                patience.release()

        pipeline.progress.looked_up(
            lookup=lookup,
        )

        definitions = (
            select_definitions(
                entries=lookup.entries,
                part_of_speech=item.part_of_speech,
                limit=DEFINITIONS_PER_ITEM,
            )
            if lookup.outcome is LookupOutcome.FOUND
            else ()
        )

        phonetic = next(
            (entry.phonetic for entry in lookup.entries if entry.phonetic),
            None,
        )

        return DictionaryInformation(
            outcome=lookup.outcome,
            failure_reason=lookup.failure_reason,
            phonetic=phonetic,
            definitions=definitions,
        )

    # NOTE:
    # Tasks take the semaphore in the order they start, which is the order given here.
    answers = await gather(
        *(
            look_up(
                items[index],
            )
            for index in order
        ),
    )

    by_index = dict(
        zip(
            order,
            answers,
            strict=True,
        ),
    )

    return tuple(
        by_index[index]
        for index in range(
            len(
                items,
            ),
        )
    )


@final
class _Patience:
    """
    How long one analysis may still spend, in all, on words the dictionary
    turned away with a time to ask again.

    The time counts from a word's first refusal until it is answered or let
    go, while at least one word is in that state, however many are, so an
    outage spends it once.
    """

    def __init__(
        self,
        *,
        seconds: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        progress: PipelineProgress,
    ) -> None:
        self._seconds: Final = seconds

        self._clock: Final = clock

        self._sleep: Final = sleep

        self._progress: Final = progress

        # NOTE:
        # When each sleeping word asks again, so the progress can name the earliest of them rather than the latest.
        self._wakes: Final[list[float]] = []

        self._held = 0

        self._since = 0.0

        self._spent = 0.0

        # NOTE:
        # The deadlines of the waiting words now asking again, and whether the dictionary has answered a waiting word
        # since it last turned one away, which lifts them: once it answers again, the words still queued behind the
        # pace of its answers are no longer waiting for it to come back.
        self._deadlines: Final[set[Timeout]] = set()

        self._answering = False

    def wait_for(
        self,
        *,
        retry_after_seconds: float | None,
    ) -> float | None:
        """
        How long to wait before asking again, or `None` when the word is let
        go: the dictionary named no time, or the time left would not cover it.
        """

        if retry_after_seconds is None:
            return None

        self._answering = False

        wait = max(
            retry_after_seconds,
            _SHORTEST_WAIT_SECONDS,
        )

        if wait > self.left():
            return None

        return wait

    def time_to_ask(
        self,
    ) -> float | None:
        """
        How long a waiting word may take to ask again: the time left, or no
        limit once the dictionary has answered a waiting word since it last
        turned one away.
        """

        return None if self._answering else self.left()

    @contextmanager
    def bounding(
        self,
        *,
        deadline: Timeout,
    ) -> Generator[None]:
        """
        Keep `deadline`, a waiting word's while it asks again, so an answer to
        another waiting word can lift it.
        """

        self._deadlines.add(
            deadline,
        )

        try:
            yield

        finally:
            self._deadlines.discard(
                deadline,
            )

    def answered(
        self,
    ) -> None:
        """
        A waiting word was answered: the dictionary answers again, so the
        deadlines of the words asking now are lifted, and the words that ask
        later have none until it turns a word away again.
        """

        self._answering = True

        for deadline in self._deadlines:
            deadline.reschedule(
                when=None,
            )

    def hold(
        self,
    ) -> None:
        """
        A word turned away begins to count toward the time.
        """

        if not self._held:
            self._since = self._clock()

        self._held += 1

    def release(
        self,
    ) -> None:
        """
        A word that held the time is answered or let go.
        """

        self._held -= 1

        if not self._held:
            self._spent += self._clock() - self._since

    async def sleep(
        self,
        *,
        seconds: float,
    ) -> None:
        """
        Sleep before a word asks again, and tell the progress when the
        earliest sleeping word asks again and how long the stage still waits
        at most, each time a word begins or ends a sleep.
        """

        wake = self._clock() + seconds

        self._wakes.append(
            wake,
        )

        try:
            self._report()

            await self._sleep(
                seconds,
            )

        finally:
            self._wakes.remove(
                wake,
            )

            self._report()

    def _report(
        self,
    ) -> None:
        now = self._clock()

        self._progress.dictionary_waiting(
            retry_in_seconds=max(
                0.0,
                min(
                    self._wakes,
                )
                - now,
            )
            if self._wakes
            else None,
            patience_left_seconds=self.left(),
        )

    def left(
        self,
    ) -> float:
        """
        How much of the time is left now.
        """

        spent = self._spent + (self._clock() - self._since if self._held else 0.0)

        return max(
            0.0,
            self._seconds - spent,
        )
