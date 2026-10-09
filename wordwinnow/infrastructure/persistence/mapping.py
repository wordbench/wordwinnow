"""
Between the domain and its rows: an analysis to a row and its item rows, and
back again.

Every enumeration travels as its value, and every timestamp is written in UTC
and read back timezone-aware in UTC.

The JSON shapes are flat and named after the domain fields they carry.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
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
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)
from wordwinnow.infrastructure.persistence.models import (
    AnalysisRow,
    VocabularyItemRow,
)


def utc_of(
    *,
    value: datetime,
) -> datetime:
    """
    The same instant in UTC, which is how every timestamp is written.
    """

    return value.astimezone(
        tz=UTC,
    )


def analysis_to_row(
    *,
    analysis: Analysis,
) -> AnalysisRow:
    """
    The row that holds an analysis, without its items.
    """

    started_at = analysis.started_at

    finished_at = analysis.finished_at

    expires_at = analysis.expires_at

    return AnalysisRow(
        id=str(
            object=analysis.id,
        ),
        title=analysis.document.title,
        text=analysis.document.text,
        origin=analysis.document.origin,
        reference=analysis.document.reference,
        attribution=analysis.document.attribution,
        source_references=[
            {
                "title": reference.title,
                "url": reference.url,
            }
            for reference in analysis.document.references
        ],
        expires_at=(
            utc_of(
                value=expires_at,
            )
            if expires_at is not None
            else None
        ),
        learner_level=analysis.profile.level,
        target_level=analysis.profile.target_level,
        known_lemmas=sorted(
            analysis.profile.known_lemmas,
        ),
        include_dictionary=analysis.options.include_dictionary,
        status=analysis.status,
        requested_at=utc_of(
            value=analysis.requested_at,
        ),
        started_at=(
            utc_of(
                value=started_at,
            )
            if started_at is not None
            else None
        ),
        finished_at=(
            utc_of(
                value=finished_at,
            )
            if finished_at is not None
            else None
        ),
        failure_reason=analysis.failure_reason,
        stage_timings=[
            _timing_to_json(
                timing,
            )
            for timing in analysis.stage_timings
        ],
        # NOTE:
        # What a worker reported belongs to the run it reported on, and every save marks that run starting, ending, or
        # handed back, so a save forgets it.
        progress=None,
    )


def progress_to_json(
    *,
    progress: ProcessingProgress,
) -> dict[str, Any]:
    """
    The progress as the `progress` column holds it.
    """

    return {
        "stage": progress.stage,
        "steps": progress.steps,
        "done": progress.done,
        "unavailable": progress.unavailable,
        "dictionary_paused": progress.dictionary_paused,
        "reported_at": utc_of(
            value=progress.reported_at,
        ).isoformat(),
    }


def progress_from_json(
    *,
    payload: Mapping[str, Any],
) -> ProcessingProgress:
    """
    The progress the `progress` column holds.
    """

    return ProcessingProgress(
        stage=Stage(
            value=payload["stage"],
        ),
        steps=payload["steps"],
        done=payload["done"],
        unavailable=payload["unavailable"],
        dictionary_paused=payload["dictionary_paused"],
        reported_at=_aware_utc(
            datetime.fromisoformat(
                payload["reported_at"],
            ),
        ),
    )


def item_rows_of(
    *,
    analysis: Analysis,
) -> tuple[VocabularyItemRow, ...]:
    """
    One row per study item, numbered in the order the analysis holds them.
    """

    return tuple(
        _item_to_row(
            study_item,
            analysis.id,
            position,
        )
        for (
            position,
            study_item,
        ) in enumerate(
            iterable=analysis.items,
        )
    )


def analysis_from_rows(
    *,
    row: AnalysisRow,
    item_rows: Sequence[VocabularyItemRow],
) -> Analysis:
    """
    The analysis a row and its item rows describe, in the order given.
    """

    started_at = row.started_at

    finished_at = row.finished_at

    expires_at = row.expires_at

    return reconstitute(
        id=AnalysisId.parse(
            text=row.id,
        ),
        document=Document(
            title=row.title,
            text=row.text,
            origin=DocumentOrigin(
                value=row.origin,
            ),
            reference=row.reference,
            attribution=row.attribution,
            references=tuple(
                Reference(
                    title=payload["title"],
                    url=payload["url"],
                )
                for payload in row.source_references
            ),
            retention=(
                _aware_utc(
                    expires_at,
                )
                - _aware_utc(
                    row.requested_at,
                )
                if expires_at is not None
                else None
            ),
        ),
        profile=LearnerProfile(
            level=CefrLevel(
                value=row.learner_level,
            ),
            target_level=CefrLevel(
                value=row.target_level,
            ),
            known_lemmas=frozenset(
                row.known_lemmas,
            ),
        ),
        options=AnalysisOptions(
            include_dictionary=row.include_dictionary,
        ),
        requested_at=_aware_utc(
            row.requested_at,
        ),
        status=AnalysisStatus(
            value=row.status,
        ),
        started_at=(
            _aware_utc(
                started_at,
            )
            if started_at is not None
            else None
        ),
        finished_at=(
            _aware_utc(
                finished_at,
            )
            if finished_at is not None
            else None
        ),
        failure_reason=row.failure_reason,
        stage_timings=tuple(
            _timing_from_json(
                payload,
            )
            for payload in row.stage_timings
        ),
        items=tuple(
            _item_from_row(
                item_row,
            )
            for item_row in item_rows
        ),
    )


def summary_from_row(
    *,
    row: AnalysisRow,
    item_count: int,
) -> AnalysisSummary:
    """
    The listing line for an analysis row, given how many items it has.
    """

    return AnalysisSummary(
        analysis_id=AnalysisId.parse(
            text=row.id,
        ),
        title=row.title,
        origin=DocumentOrigin(
            value=row.origin,
        ),
        learner_level=CefrLevel(
            value=row.learner_level,
        ),
        status=AnalysisStatus(
            value=row.status,
        ),
        requested_at=_aware_utc(
            row.requested_at,
        ),
        item_count=item_count,
    )


def _aware_utc(
    value: datetime,
    /,
) -> datetime:
    # NOTE:
    # SQLite keeps no offset and hands back the UTC wall time it was given, while PostgreSQL hands back an aware
    # value; both become the same aware UTC instant here, so the domain never sees a naive timestamp.
    if value.tzinfo is None:
        return value.replace(
            tzinfo=UTC,
        )

    return value.astimezone(
        tz=UTC,
    )


def _item_to_row(
    study_item: StudyItem,
    analysis_id: AnalysisId,
    position: int,
    /,
) -> VocabularyItemRow:
    dictionary = study_item.dictionary

    return VocabularyItemRow(
        analysis_id=str(
            object=analysis_id,
        ),
        position=position,
        lemma=study_item.item.lemma,
        part_of_speech=study_item.item.part_of_speech,
        occurrence_count=study_item.item.occurrence_count,
        example_sentence=study_item.item.example_sentence,
        zipf_frequency=study_item.zipf_frequency,
        level=study_item.level.level,
        level_source=study_item.level.source,
        tier=study_item.tier,
        dictionary=(
            _dictionary_to_json(
                dictionary,
            )
            if dictionary is not None
            else None
        ),
        senses=[
            _sense_to_json(
                sense,
            )
            for sense in study_item.senses
        ],
    )


def _item_from_row(
    row: VocabularyItemRow,
    /,
) -> StudyItem:
    dictionary = row.dictionary

    return StudyItem(
        item=VocabularyItem(
            lemma=row.lemma,
            part_of_speech=PartOfSpeech(
                value=row.part_of_speech,
            ),
            occurrence_count=row.occurrence_count,
            example_sentence=row.example_sentence,
        ),
        zipf_frequency=row.zipf_frequency,
        level=LevelAssessment(
            level=CefrLevel(
                value=row.level,
            ),
            source=LevelSource(
                value=row.level_source,
            ),
        ),
        tier=StudyTier(
            value=row.tier,
        ),
        dictionary=(
            _dictionary_from_json(
                dictionary,
            )
            if dictionary is not None
            else None
        ),
        senses=tuple(
            _sense_from_json(
                payload,
            )
            for payload in row.senses
        ),
    )


def _timing_to_json(
    timing: StageTiming,
    /,
) -> dict[str, Any]:
    return {
        "stage": timing.stage,
        "seconds": timing.seconds,
    }


def _timing_from_json(
    payload: Mapping[str, Any],
    /,
) -> StageTiming:
    return StageTiming(
        stage=Stage(
            value=payload["stage"],
        ),
        seconds=payload["seconds"],
    )


def _dictionary_to_json(
    information: DictionaryInformation,
    /,
) -> dict[str, Any]:
    return {
        "outcome": information.outcome,
        "failure_reason": information.failure_reason,
        "phonetic": information.phonetic,
        "definitions": [
            _definition_to_json(
                definition,
            )
            for definition in information.definitions
        ],
    }


def _dictionary_from_json(
    payload: Mapping[str, Any],
    /,
) -> DictionaryInformation:
    return DictionaryInformation(
        outcome=LookupOutcome(
            value=payload["outcome"],
        ),
        failure_reason=payload["failure_reason"],
        phonetic=payload["phonetic"],
        definitions=tuple(
            _definition_from_json(
                definition,
            )
            for definition in payload["definitions"]
        ),
    )


def _definition_to_json(
    definition: SelectedDefinition,
    /,
) -> dict[str, Any]:
    return {
        "text": definition.text,
        "example": definition.example,
        "part_of_speech_matched": definition.part_of_speech_matched,
        "provenance": _provenance_to_json(
            definition.provenance,
        ),
    }


def _definition_from_json(
    payload: Mapping[str, Any],
    /,
) -> SelectedDefinition:
    # NOTE:
    # A definition stored before provenance was recorded has no such key, and it comes back without one.
    return SelectedDefinition(
        text=payload["text"],
        example=payload["example"],
        part_of_speech_matched=payload["part_of_speech_matched"],
        provenance=_provenance_from_json(
            payload.get(
                "provenance",
            ),
        ),
    )


def _provenance_to_json(
    provenance: Provenance | None,
    /,
) -> dict[str, Any] | None:
    if provenance is None:
        return None

    return {
        "license": (
            {
                "name": value_object.name,
                "url": value_object.url,
            }
            if (value_object := provenance.license) is not None
            else None
        ),
        "source_urls": list(
            provenance.source_urls,
        ),
    }


def _provenance_from_json(
    payload: Mapping[str, Any] | None,
    /,
) -> Provenance | None:
    if payload is None:
        return None

    license_payload = payload["license"]

    return Provenance(
        license=(
            License(
                name=license_payload["name"],
                url=license_payload["url"],
            )
            if license_payload is not None
            else None
        ),
        source_urls=tuple(
            payload["source_urls"],
        ),
    )


def _sense_to_json(
    sense: LexicalSense,
    /,
) -> dict[str, Any]:
    return {
        "key": sense.key,
        "part_of_speech": sense.part_of_speech,
        "gloss": sense.gloss,
        "example": sense.example,
        "synonyms": list(
            sense.synonyms,
        ),
        "hypernyms": list(
            sense.hypernyms,
        ),
        "antonyms": list(
            sense.antonyms,
        ),
        "usage_count": sense.usage_count,
        "hyponym_count": sense.hyponym_count,
        "category": sense.category,
    }


def _sense_from_json(
    payload: Mapping[str, Any],
    /,
) -> LexicalSense:
    return LexicalSense(
        key=payload["key"],
        part_of_speech=PartOfSpeech(
            value=payload["part_of_speech"],
        ),
        gloss=payload["gloss"],
        example=payload["example"],
        synonyms=tuple(
            payload["synonyms"],
        ),
        hypernyms=tuple(
            payload["hypernyms"],
        ),
        antonyms=tuple(
            payload["antonyms"],
        ),
        usage_count=payload["usage_count"],
        hyponym_count=payload["hyponym_count"],
        category=payload["category"],
    )
