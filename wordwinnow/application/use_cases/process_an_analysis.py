"""
The pipeline: turn a requested analysis into study items.

Each stage is timed, and the timings are stored with the result, because
performance is part of what the analysis reports.
"""

from asyncio import (
    Semaphore,
    gather,
    to_thread,
)
from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from time import (
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
    """

    linguistics: LinguisticAnalyzer

    frequency: WordFrequency

    reference_lexicon: ReferenceLexicon

    level_estimator: LevelEstimator

    dictionary: Dictionary

    lexical_semantics: LexicalSemantics

    dictionary_concurrency: int = 8

    progress: PipelineProgress = _UNWATCHED


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

        dictionary_information = await _look_up_all(
            items,
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
            # WARN:
            # The result is committed before its completion is published, so a worker can stop, or the broker refuse,
            # in between; the requested message then arrives again and finds a completed analysis, and publishing the
            # completion once more is what keeps it from being lost, while the fact store keeps one row per fact.
            await publisher.publish(
                analysis=analysis,
                event=analysis.completion(),
            )

            return ProcessedAnalysis(
                analysis=analysis,
                redelivered=True,
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

        analysis.start(
            at=clock.now(),
        )

        await uow.analyses.save(
            analysis=analysis,
        )

        await uow.commit()

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

        await _save_and_publish(
            analysis,
            new_unit_of_work,
            publisher,
        )

        raise

    analysis.complete(
        at=clock.now(),
        items=result.items,
        stage_timings=result.stage_timings,
    )

    await _save_and_publish(
        analysis,
        new_unit_of_work,
        publisher,
    )

    return ProcessedAnalysis(
        analysis=analysis,
        redelivered=False,
    )


async def _save_and_publish(
    analysis: Analysis,
    new_unit_of_work: UnitOfWorkFactory,
    publisher: AnalysisEventPublisher,
    /,
) -> None:
    async with new_unit_of_work() as uow:
        await uow.analyses.save(
            analysis=analysis,
        )

        await uow.commit()

    for event in analysis.pull_events():
        await publisher.publish(
            analysis=analysis,
            event=event,
        )


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
    pipeline: Pipeline,
    /,
) -> tuple[DictionaryInformation, ...]:
    semaphore = Semaphore(
        value=pipeline.dictionary_concurrency,
    )

    async def look_up(
        item: VocabularyItem,
        /,
    ) -> DictionaryInformation:
        async with semaphore:
            lookup = await pipeline.dictionary.look_up(
                lemma=item.lemma,
            )

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

    return tuple(
        await gather(
            *(
                look_up(
                    item,
                )
                for item in items
            ),
        ),
    )
