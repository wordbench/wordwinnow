"""
How domain events become integration messages, and how a completed message
becomes facts again.

The publisher hands the codec a domain event with its analysis and gets back
the topic and the versioned message; the aggregation side hands it a completed
message and gets back the facts the analysis produced.
"""

from typing import (
    Final,
)

from wordwinnow.application.dto import (
    VocabularyFact,
    facts_of,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisCompleted,
    AnalysisEvent,
    AnalysisRequested,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.messaging.messages import (
    AnalysisCompletedV1,
    AnalysisRequestedV1,
    FactV1,
    Message,
)
from wordwinnow.infrastructure.messaging.topics import (
    ANALYSIS_COMPLETED_V1,
    ANALYSIS_REQUESTED_V1,
)

# NOTE:
# The record header that carries the analysis identifier, so a consumer binds it before it decodes anything.
CORRELATION_ID_HEADER: Final = "correlation_id"


def encode(
    *,
    message: Message,
) -> bytes:
    """
    Serialize a message to the bytes that travel as a record's value.
    """

    return message.model_dump_json().encode()


def decode[M: Message](
    *,
    data: bytes,
    model: type[M],
) -> M:
    """
    Read a message of one known version back from a record's value.

    Raises `pydantic.ValidationError` when the bytes do not hold that message.
    """

    return model.model_validate_json(
        json_data=data,
    )


def message_for(
    *,
    analysis: Analysis,
    event: AnalysisEvent,
) -> tuple[str, Message]:
    """
    Translate a domain event into the topic and the message that carry it.

    Raises `ValueError` when the event belongs to another analysis.
    """

    if event.analysis_id != analysis.id:
        raise ValueError(
            f"an event of analysis {event.analysis_id} cannot make a message about analysis {analysis.id}",
        )

    match event:
        case AnalysisRequested():
            return (
                ANALYSIS_REQUESTED_V1,
                AnalysisRequestedV1(
                    occurred_at=event.occurred_at,
                    analysis_id=analysis.id.value,
                ),
            )

        case AnalysisCompleted():
            return (
                ANALYSIS_COMPLETED_V1,
                AnalysisCompletedV1(
                    occurred_at=event.occurred_at,
                    analysis_id=analysis.id.value,
                    origin=analysis.document.origin,
                    learner_level=analysis.profile.level,
                    facts=tuple(
                        FactV1(
                            lemma=fact.lemma,
                            part_of_speech=fact.part_of_speech,
                            level=fact.level,
                            level_source=fact.level_source,
                            tier=fact.tier,
                            occurrence_count=fact.occurrence_count,
                            lookup_outcome=fact.lookup_outcome,
                        )
                        for fact in facts_of(
                            analysis=analysis,
                        )
                    ),
                ),
            )


def facts_from(
    *,
    message: AnalysisCompletedV1,
) -> tuple[VocabularyFact, ...]:
    """
    Rebuild the facts a completed message carries, for the aggregation side.
    """

    analysis_id = AnalysisId(
        value=message.analysis_id,
    )

    return tuple(
        VocabularyFact(
            analysis_id=analysis_id,
            completed_at=message.occurred_at,
            origin=message.origin,
            learner_level=message.learner_level,
            lemma=fact.lemma,
            part_of_speech=fact.part_of_speech,
            level=fact.level,
            level_source=fact.level_source,
            tier=fact.tier,
            occurrence_count=fact.occurrence_count,
            lookup_outcome=fact.lookup_outcome,
        )
        for fact in message.facts
    )
