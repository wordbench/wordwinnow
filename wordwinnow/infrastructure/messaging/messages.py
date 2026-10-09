"""
The versioned integration messages an analysis produces.

A message is a contract with other services, so its shape is fixed by the
version in its topic name and in its `schema_version` field, and the golden
files under the tests prove that the shape has not drifted.
"""

from typing import (
    Literal,
)
from uuid import (
    UUID,
)

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
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
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.study import (
    StudyTier,
)


class FactV1(
    BaseModel,
):
    """
    One study item of a completed analysis, as the completed message carries
    it.
    """

    model_config = ConfigDict(
        frozen=True,
    )

    lemma: str

    part_of_speech: PartOfSpeech

    level: CefrLevel

    level_source: LevelSource

    tier: StudyTier

    occurrence_count: int

    lookup_outcome: LookupOutcome | None


class AnalysisRequestedV1(
    BaseModel,
):
    """
    A learner asked for a document to be analyzed.
    """

    model_config = ConfigDict(
        frozen=True,
    )

    schema_version: Literal[1] = 1

    occurred_at: AwareDatetime

    analysis_id: UUID


class AnalysisCompletedV1(
    BaseModel,
):
    """
    An analysis produced its study items, carried as facts for aggregation.

    `occurred_at` is when the analysis completed.
    """

    model_config = ConfigDict(
        frozen=True,
    )

    schema_version: Literal[1] = 1

    occurred_at: AwareDatetime

    analysis_id: UUID

    origin: DocumentOrigin

    learner_level: CefrLevel

    facts: tuple[FactV1, ...]


type Message = AnalysisRequestedV1 | AnalysisCompletedV1
