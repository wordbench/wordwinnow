"""
The read models and facts the application hands across its boundary.

A fact is what the aggregation side of the system keeps about a completed
analysis: one row per study item, flat enough to count and group.
"""

from dataclasses import (
    dataclass,
)
from datetime import (
    datetime,
)
from typing import (
    final,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Stage,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.study import (
    StudyTier,
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class AnalysisSummary:
    """
    One line of an analysis listing.
    """

    analysis_id: AnalysisId

    title: str

    origin: DocumentOrigin

    learner_level: CefrLevel

    status: AnalysisStatus

    requested_at: datetime

    item_count: int


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ProcessingProgress:
    """
    How far a worker has come with an analysis it is processing, as it last
    reported.

    `steps` is how many steps the stage takes when that is known, `done` how
    many have ended, and `unavailable` how many words of the dictionary stage
    went unanswered.

    `dictionary_paused` says the dictionary has stopped asking its provider,
    so WordNet stands in for every word until it asks again.
    """

    stage: Stage

    steps: int | None

    done: int

    unavailable: int

    dictionary_paused: bool

    reported_at: datetime


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class VocabularyFact:
    """
    One study item of one completed analysis, flattened for aggregation.
    """

    analysis_id: AnalysisId

    completed_at: datetime

    origin: DocumentOrigin

    learner_level: CefrLevel

    lemma: str

    part_of_speech: PartOfSpeech

    level: CefrLevel

    level_source: LevelSource

    tier: StudyTier

    occurrence_count: int

    lookup_outcome: LookupOutcome | None


def facts_of(
    *,
    analysis: Analysis,
) -> tuple[VocabularyFact, ...]:
    """
    Flatten a completed analysis into its facts.

    An analysis that has not completed has no facts.
    """

    if analysis.status is not AnalysisStatus.COMPLETED or analysis.finished_at is None:
        return ()

    return tuple(
        VocabularyFact(
            analysis_id=analysis.id,
            completed_at=analysis.finished_at,
            origin=analysis.document.origin,
            learner_level=analysis.profile.level,
            lemma=study_item.item.lemma,
            part_of_speech=study_item.item.part_of_speech,
            level=study_item.level.level,
            level_source=study_item.level.source,
            tier=study_item.tier,
            occurrence_count=study_item.item.occurrence_count,
            lookup_outcome=value_object.outcome if (value_object := study_item.dictionary) is not None else None,
        )
        for study_item in analysis.items
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LevelCount:
    """
    How many study items sit at one level.
    """

    level: CefrLevel

    count: int


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LemmaCount:
    """
    How many analyses placed one lemma in the focus tier, and how often it
    occurred across them.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    analysis_count: int

    occurrence_count: int


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class VocabularySummary:
    """
    What the aggregation side knows across every completed analysis.
    """

    analysis_count: int

    fact_count: int

    levels: tuple[LevelCount, ...]

    focus_lemmas: tuple[LemmaCount, ...]
