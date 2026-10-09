"""
The intake API's wire schema, and the translation to and from the domain.

The CLI reads an analysis back through the same schema and prints it as JSON
through the same schema, so the report a learner sees is built from the same
domain objects in both modes.
"""

from collections.abc import (
    Sequence,
)
from datetime import (
    datetime,
    timedelta,
)
from enum import (
    auto,
)
from typing import (
    Self,
)
from uuid import (
    UUID,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    LemmaCount,
    LevelCount,
    ProcessingProgress,
    VocabularySummary,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
    AnalysisStatus,
    Stage,
    StageTiming,
    reconstitute,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    License,
    LookupOutcome,
    Provenance,
    SelectedDefinition,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    Reference,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
    default_target_level,
    known_lemmas_from_lines,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    GlossSource,
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)


class ProblemType(
    UnorderedStrEnum,
):
    """
    Why a request was refused, as every client of the API spells it.
    """

    ANALYSIS_NOT_FOUND = auto()

    REQUEST_REJECTED = auto()

    SOURCE_NOT_CONFIGURED = auto()

    SOURCE_UNAVAILABLE = auto()

    ANALYSIS_NOT_PUBLISHED = auto()

    MESSAGING_UNAVAILABLE = auto()

    STORE_UNAVAILABLE = auto()


class Problem(
    BaseModel,
):
    """
    An error answer: its type, for a program, and its detail, for a person.
    """

    type: ProblemType

    detail: str


class AnalysisRequest(
    BaseModel,
):
    """
    What a learner asks for: a text of their own, or a topic from a named
    source, analyzed for the learner the profile fields describe.

    Exactly one of `text` and `topic` is given, and a topic comes with its
    source.
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    text: str | None = None

    title: str | None = None

    reference: str | None = None

    source: str | None = None

    topic: str | None = None

    limit: int = Field(
        default=15,
        ge=1,
        le=100,
    )

    level: CefrLevel

    target_level: CefrLevel | None = None

    known_lemmas: list[str] = []

    include_dictionary: bool = True

    @model_validator(
        mode="after",
    )
    def _consistent(
        self,
    ) -> Self:
        if (self.text is None) == (self.topic is None):
            given = "both" if self.text is not None else "neither"

            raise ValueError(
                f"an analysis request requires exactly one of text and topic, got {given}",
            )

        if self.topic is not None and self.source is None:
            raise ValueError(
                "an analysis request requires a source with a topic",
            )

        if self.target_level is not None and self.target_level < self.level:
            raise ValueError(
                f"an analysis request requires a target level at or above {self.level}, got {self.target_level}",
            )

        return self

    def profile(
        self,
    ) -> LearnerProfile:
        """
        The learner profile the request describes.
        """

        return LearnerProfile(
            level=self.level,
            target_level=(
                self.target_level
                if self.target_level is not None
                else default_target_level(
                    level=self.level,
                )
            ),
            known_lemmas=known_lemmas_from_lines(
                lines=self.known_lemmas,
            ),
        )

    def options(
        self,
    ) -> AnalysisOptions:
        """
        The analysis options the request describes.
        """

        return AnalysisOptions(
            include_dictionary=self.include_dictionary,
        )


class AnalysisAccepted(
    BaseModel,
):
    """
    The answer to a request: the id to ask about later.
    """

    analysis_id: UUID

    status: AnalysisStatus


class ReferenceOut(
    BaseModel,
):
    """
    Where to read one piece of a document in full, on the wire.
    """

    title: str

    url: str


class DocumentOut(
    BaseModel,
):
    """
    A document on the wire, with whose it is and how long it may be kept.
    """

    title: str

    text: str

    origin: DocumentOrigin

    reference: str

    attribution: str | None = None

    references: list[ReferenceOut] = []

    retention_seconds: float | None = None


class ProfileOut(
    BaseModel,
):
    """
    A learner profile on the wire.
    """

    level: CefrLevel

    target_level: CefrLevel

    known_lemmas: list[str]


class LicenseOut(
    BaseModel,
):
    """
    A dictionary's license on the wire.
    """

    name: str

    url: str


class ProvenanceOut(
    BaseModel,
):
    """
    Where a definition comes from, on the wire.
    """

    license: LicenseOut | None = None

    source_urls: list[str] = []


class DefinitionOut(
    BaseModel,
):
    """
    A selected definition on the wire.
    """

    text: str

    example: str | None

    part_of_speech_matched: bool

    provenance: ProvenanceOut | None = None


class DictionaryOut(
    BaseModel,
):
    """
    Dictionary information on the wire.
    """

    outcome: LookupOutcome

    failure_reason: str | None

    phonetic: str | None

    definitions: list[DefinitionOut]


class SenseOut(
    BaseModel,
):
    """
    A lexical sense on the wire.
    """

    key: str

    part_of_speech: PartOfSpeech

    gloss: str

    example: str | None

    synonyms: list[str]

    hypernyms: list[str]

    antonyms: list[str]

    usage_count: int

    hyponym_count: int

    category: str


class GlossOut(
    BaseModel,
):
    """
    The one-line meaning a learner sees, on the wire.
    """

    text: str

    source: GlossSource

    part_of_speech_matched: bool

    provenance: ProvenanceOut | None = None


class StudyItemOut(
    BaseModel,
):
    """
    A study item on the wire.

    `gloss` is derived from `dictionary` and `senses`, and is carried so a
    client need not repeat the rule that chooses it.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    occurrence_count: int

    example_sentence: str

    zipf_frequency: float

    level: CefrLevel

    level_source: LevelSource

    tier: StudyTier

    gloss: GlossOut | None

    dictionary: DictionaryOut | None

    senses: list[SenseOut]


class StageTimingOut(
    BaseModel,
):
    """
    A stage timing on the wire.
    """

    stage: Stage

    seconds: float


class ProgressOut(
    BaseModel,
):
    """
    How far a worker has come with an analysis it is processing, on the wire.
    """

    stage: Stage

    steps: int | None

    done: int

    unavailable: int

    dictionary_paused: bool

    reported_at: datetime

    @classmethod
    def from_domain(
        cls,
        *,
        progress: ProcessingProgress,
    ) -> Self:
        """
        Put the progress on the wire.
        """

        return cls(
            stage=progress.stage,
            steps=progress.steps,
            done=progress.done,
            unavailable=progress.unavailable,
            dictionary_paused=progress.dictionary_paused,
            reported_at=progress.reported_at,
        )

    def to_domain(
        self,
    ) -> ProcessingProgress:
        """
        The progress the wire form describes.
        """

        return ProcessingProgress(
            stage=self.stage,
            steps=self.steps,
            done=self.done,
            unavailable=self.unavailable,
            dictionary_paused=self.dictionary_paused,
            reported_at=self.reported_at,
        )


def _is_absent(
    value: object,
    /,
) -> bool:
    return value is None


class AnalysisOut(
    BaseModel,
):
    """
    A whole analysis on the wire, enough to rebuild it.

    `progress` appears only while a worker is processing the analysis and has
    said how far it has come.
    """

    analysis_id: UUID

    document: DocumentOut

    profile: ProfileOut

    include_dictionary: bool

    status: AnalysisStatus

    requested_at: datetime

    started_at: datetime | None

    finished_at: datetime | None

    failure_reason: str | None

    stage_timings: list[StageTimingOut]

    items: list[StudyItemOut]

    # NOTE:
    # Left out of the payload rather than written as null, so a finished analysis, and the JSON report the command
    # line prints from it, carry no field that is always empty.
    progress: ProgressOut | None = Field(
        default=None,
        exclude_if=_is_absent,
    )

    @classmethod
    def from_domain(
        cls,
        *,
        analysis: Analysis,
        progress: ProcessingProgress | None = None,
    ) -> Self:
        """
        Put an analysis on the wire, with how far a worker has come with it
        when that is known.
        """

        return cls(
            analysis_id=analysis.id.value,
            document=DocumentOut(
                title=analysis.document.title,
                text=analysis.document.text,
                origin=analysis.document.origin,
                reference=analysis.document.reference,
                attribution=analysis.document.attribution,
                references=[
                    ReferenceOut(
                        title=reference.title,
                        url=reference.url,
                    )
                    for reference in analysis.document.references
                ],
                retention_seconds=(
                    value.total_seconds() if (value := analysis.document.retention) is not None else None
                ),
            ),
            profile=ProfileOut(
                level=analysis.profile.level,
                target_level=analysis.profile.target_level,
                known_lemmas=sorted(
                    analysis.profile.known_lemmas,
                ),
            ),
            include_dictionary=analysis.options.include_dictionary,
            status=analysis.status,
            requested_at=analysis.requested_at,
            started_at=analysis.started_at,
            finished_at=analysis.finished_at,
            failure_reason=analysis.failure_reason,
            stage_timings=[
                StageTimingOut(
                    stage=timing.stage,
                    seconds=timing.seconds,
                )
                for timing in analysis.stage_timings
            ],
            items=[
                _item_out(
                    study_item,
                )
                for study_item in analysis.items
            ],
            progress=(
                ProgressOut.from_domain(
                    progress=progress,
                )
                if progress is not None
                else None
            ),
        )

    def processing_progress(
        self,
    ) -> ProcessingProgress | None:
        """
        How far a worker has come with the analysis, when the wire form says.
        """

        if self.progress is None:
            return None

        return self.progress.to_domain()

    def to_domain(
        self,
    ) -> Analysis:
        """
        Rebuild the analysis the wire form describes.
        """

        return reconstitute(
            id=AnalysisId(
                value=self.analysis_id,
            ),
            document=Document(
                title=self.document.title,
                text=self.document.text,
                origin=self.document.origin,
                reference=self.document.reference,
                attribution=self.document.attribution,
                references=tuple(
                    Reference(
                        title=reference.title,
                        url=reference.url,
                    )
                    for reference in self.document.references
                ),
                retention=(
                    timedelta(
                        seconds=value,
                    )
                    if (value := self.document.retention_seconds) is not None
                    else None
                ),
            ),
            profile=LearnerProfile(
                level=self.profile.level,
                target_level=self.profile.target_level,
                known_lemmas=frozenset(
                    self.profile.known_lemmas,
                ),
            ),
            options=AnalysisOptions(
                include_dictionary=self.include_dictionary,
            ),
            requested_at=self.requested_at,
            status=self.status,
            started_at=self.started_at,
            finished_at=self.finished_at,
            failure_reason=self.failure_reason,
            stage_timings=tuple(
                StageTiming(
                    stage=timing.stage,
                    seconds=timing.seconds,
                )
                for timing in self.stage_timings
            ),
            items=tuple(
                _item_in(
                    item,
                )
                for item in self.items
            ),
        )


def _provenance_out(
    provenance: Provenance | None,
    /,
) -> ProvenanceOut | None:
    if provenance is None:
        return None

    return ProvenanceOut(
        license=(
            LicenseOut(
                name=value_object.name,
                url=value_object.url,
            )
            if (value_object := provenance.license) is not None
            else None
        ),
        source_urls=list(
            provenance.source_urls,
        ),
    )


def _provenance_in(
    provenance: ProvenanceOut | None,
    /,
) -> Provenance | None:
    if provenance is None:
        return None

    return Provenance(
        license=(
            License(
                name=value_object.name,
                url=value_object.url,
            )
            if (value_object := provenance.license) is not None
            else None
        ),
        source_urls=tuple(
            provenance.source_urls,
        ),
    )


def _item_out(
    study_item: StudyItem,
    /,
) -> StudyItemOut:
    dictionary = study_item.dictionary

    gloss = study_item.gloss

    return StudyItemOut(
        lemma=study_item.item.lemma,
        part_of_speech=study_item.item.part_of_speech,
        occurrence_count=study_item.item.occurrence_count,
        example_sentence=study_item.item.example_sentence,
        zipf_frequency=study_item.zipf_frequency,
        level=study_item.level.level,
        level_source=study_item.level.source,
        tier=study_item.tier,
        gloss=(
            GlossOut(
                text=gloss.text,
                source=gloss.source,
                part_of_speech_matched=gloss.part_of_speech_matched,
                provenance=_provenance_out(
                    gloss.provenance,
                ),
            )
            if gloss is not None
            else None
        ),
        dictionary=(
            DictionaryOut(
                outcome=dictionary.outcome,
                failure_reason=dictionary.failure_reason,
                phonetic=dictionary.phonetic,
                definitions=[
                    DefinitionOut(
                        text=definition.text,
                        example=definition.example,
                        part_of_speech_matched=definition.part_of_speech_matched,
                        provenance=_provenance_out(
                            definition.provenance,
                        ),
                    )
                    for definition in dictionary.definitions
                ],
            )
            if dictionary is not None
            else None
        ),
        senses=[
            SenseOut(
                key=sense.key,
                part_of_speech=sense.part_of_speech,
                gloss=sense.gloss,
                example=sense.example,
                synonyms=list(
                    sense.synonyms,
                ),
                hypernyms=list(
                    sense.hypernyms,
                ),
                antonyms=list(
                    sense.antonyms,
                ),
                usage_count=sense.usage_count,
                hyponym_count=sense.hyponym_count,
                category=sense.category,
            )
            for sense in study_item.senses
        ],
    )


def _item_in(
    item: StudyItemOut,
    /,
) -> StudyItem:
    dictionary = item.dictionary

    return StudyItem(
        item=VocabularyItem(
            lemma=item.lemma,
            part_of_speech=item.part_of_speech,
            occurrence_count=item.occurrence_count,
            example_sentence=item.example_sentence,
        ),
        zipf_frequency=item.zipf_frequency,
        level=LevelAssessment(
            level=item.level,
            source=item.level_source,
        ),
        tier=item.tier,
        dictionary=(
            DictionaryInformation(
                outcome=dictionary.outcome,
                failure_reason=dictionary.failure_reason,
                phonetic=dictionary.phonetic,
                definitions=tuple(
                    SelectedDefinition(
                        text=definition.text,
                        example=definition.example,
                        part_of_speech_matched=definition.part_of_speech_matched,
                        provenance=_provenance_in(
                            definition.provenance,
                        ),
                    )
                    for definition in dictionary.definitions
                ),
            )
            if dictionary is not None
            else None
        ),
        senses=tuple(
            LexicalSense(
                key=sense.key,
                part_of_speech=sense.part_of_speech,
                gloss=sense.gloss,
                example=sense.example,
                synonyms=tuple(
                    sense.synonyms,
                ),
                hypernyms=tuple(
                    sense.hypernyms,
                ),
                antonyms=tuple(
                    sense.antonyms,
                ),
                usage_count=sense.usage_count,
                hyponym_count=sense.hyponym_count,
                category=sense.category,
            )
            for sense in item.senses
        ),
    )


class AnalysisSummaryOut(
    BaseModel,
):
    """
    One line of a listing on the wire.
    """

    analysis_id: UUID

    title: str

    origin: DocumentOrigin

    learner_level: CefrLevel

    status: AnalysisStatus

    requested_at: datetime

    item_count: int

    @classmethod
    def from_domain(
        cls,
        *,
        summary: AnalysisSummary,
    ) -> Self:
        """
        Put a listing line on the wire.
        """

        return cls(
            analysis_id=summary.analysis_id.value,
            title=summary.title,
            origin=summary.origin,
            learner_level=summary.learner_level,
            status=summary.status,
            requested_at=summary.requested_at,
            item_count=summary.item_count,
        )

    def to_domain(
        self,
    ) -> AnalysisSummary:
        """
        Rebuild the listing line the wire form describes.
        """

        return AnalysisSummary(
            analysis_id=AnalysisId(
                value=self.analysis_id,
            ),
            title=self.title,
            origin=self.origin,
            learner_level=self.learner_level,
            status=self.status,
            requested_at=self.requested_at,
            item_count=self.item_count,
        )


class LevelCountOut(
    BaseModel,
):
    """
    A count by level on the wire.
    """

    level: CefrLevel

    count: int


class LemmaCountOut(
    BaseModel,
):
    """
    A focus lemma's counts on the wire.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    analysis_count: int

    occurrence_count: int


class VocabularySummaryOut(
    BaseModel,
):
    """
    The cross-analysis summary on the wire.
    """

    analysis_count: int

    fact_count: int

    levels: list[LevelCountOut]

    focus_lemmas: list[LemmaCountOut]

    @classmethod
    def from_domain(
        cls,
        *,
        summary: VocabularySummary,
    ) -> Self:
        """
        Put a summary on the wire.
        """

        return cls(
            analysis_count=summary.analysis_count,
            fact_count=summary.fact_count,
            levels=[
                LevelCountOut(
                    level=count.level,
                    count=count.count,
                )
                for count in summary.levels
            ],
            focus_lemmas=[
                LemmaCountOut(
                    lemma=count.lemma,
                    part_of_speech=count.part_of_speech,
                    analysis_count=count.analysis_count,
                    occurrence_count=count.occurrence_count,
                )
                for count in summary.focus_lemmas
            ],
        )

    def to_domain(
        self,
    ) -> VocabularySummary:
        """
        Rebuild the summary the wire form describes.
        """

        return VocabularySummary(
            analysis_count=self.analysis_count,
            fact_count=self.fact_count,
            levels=tuple(
                LevelCount(
                    level=count.level,
                    count=count.count,
                )
                for count in self.levels
            ),
            focus_lemmas=tuple(
                LemmaCount(
                    lemma=count.lemma,
                    part_of_speech=count.part_of_speech,
                    analysis_count=count.analysis_count,
                    occurrence_count=count.occurrence_count,
                )
                for count in self.focus_lemmas
            ),
        )


class RequeueResult(
    BaseModel,
):
    """
    Which analyses an operator's requeue handed back to the workers.
    """

    analysis_ids: list[UUID]


def summaries_out(
    *,
    summaries: Sequence[AnalysisSummary],
) -> list[AnalysisSummaryOut]:
    """
    Put a listing on the wire.
    """

    return [
        AnalysisSummaryOut.from_domain(
            summary=summary,
        )
        for summary in summaries
    ]
