"""
Analyses in each published state, for the messaging tests to publish.
"""

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import (
    Final,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
    Stage,
    StageTiming,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)

# NOTE:
# A pure reference instant with no product meaning of its own.
EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)

DOCUMENT: Final = Document(
    title="a-scandal-in-bohemia",
    text="Holmes became the drunken groom. The groom sauntered slowly.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)

PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)

ITEMS: Final = (
    StudyItem(
        item=VocabularyItem(
            lemma="groom",
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=2,
            example_sentence="Holmes became the drunken groom.",
        ),
        zipf_frequency=3.63,
        level=LevelAssessment(
            level=CefrLevel.B1,
            source=LevelSource.REFERENCE_LIST,
        ),
        tier=StudyTier.FOCUS,
        dictionary=DictionaryInformation(
            outcome=LookupOutcome.FOUND,
            failure_reason=None,
            phonetic=None,
            definitions=(),
        ),
        senses=(),
    ),
    StudyItem(
        item=VocabularyItem(
            lemma="drunken",
            part_of_speech=PartOfSpeech.ADJECTIVE,
            occurrence_count=1,
            example_sentence="Holmes became the drunken groom.",
        ),
        zipf_frequency=3.66,
        level=LevelAssessment(
            level=CefrLevel.A2,
            source=LevelSource.MODEL,
        ),
        tier=StudyTier.REVIEW,
        dictionary=None,
        senses=(),
    ),
)


def requested_analysis() -> Analysis:
    """
    An analysis that has just been requested, its request event still pending.
    """

    return request_an_analysis(
        document=DOCUMENT,
        profile=PROFILE,
        options=AnalysisOptions(),
        at=EPOCH,
    )


def completed_analysis() -> Analysis:
    """
    An analysis completed with two study items, its completion event still
    pending.
    """

    analysis = requested_analysis()

    analysis.pull_events()

    analysis.start(
        at=EPOCH
        + timedelta(
            seconds=1,
        ),
    )

    analysis.complete(
        at=EPOCH
        + timedelta(
            seconds=2,
        ),
        items=ITEMS,
        stage_timings=(
            StageTiming(
                stage=Stage.WINNOWING,
                seconds=0.5,
            ),
        ),
    )

    return analysis
