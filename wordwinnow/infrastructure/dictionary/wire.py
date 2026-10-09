"""
A lookup as it travels: from the enrichment service to its clients, and into
the cache and back.

The payload mirrors the domain model field for field, so a lookup read back
equals the lookup written, and the failure reasons are the one vocabulary
every client of the service shares.
"""

from enum import (
    auto,
)

from pydantic import (
    BaseModel,
)

from wordwinnow.domain.dictionary import (
    Definition,
    DictionaryEntry,
    DictionaryLookup,
    License,
    LookupOutcome,
    Meaning,
    Provenance,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)


class FailureReason(
    UnorderedStrEnum,
):
    """
    Why a dictionary was unavailable, as every client spells it.

    The reasons follow a request's life: refused before it is sent, lost on
    the way, or answered with a refusal, an error, or a body nobody can read.
    """

    CIRCUIT_OPEN = auto()

    TIMED_OUT = auto()

    TRANSPORT_ERROR = auto()

    RATE_LIMITED = auto()

    PROVIDER_ERROR = auto()

    INVALID_RESPONSE = auto()


def unavailable(
    *,
    reason: FailureReason,
) -> DictionaryLookup:
    """
    The lookup that says the dictionary could not answer, and why.
    """

    return DictionaryLookup(
        outcome=LookupOutcome.UNAVAILABLE,
        failure_reason=reason,
    )


class DefinitionPayload(
    BaseModel,
):
    """
    One definition on the wire.
    """

    text: str

    example: str | None = None


class MeaningPayload(
    BaseModel,
):
    """
    One meaning on the wire, with the part of speech as its enum value or
    null.
    """

    word_class: str

    part_of_speech: PartOfSpeech | None = None

    definitions: list[DefinitionPayload]


class LicensePayload(
    BaseModel,
):
    """
    A license on the wire.
    """

    name: str

    url: str


class ProvenancePayload(
    BaseModel,
):
    """
    An entry's provenance on the wire.
    """

    license: LicensePayload | None = None

    source_urls: list[str] = []


class EntryPayload(
    BaseModel,
):
    """
    One entry on the wire.
    """

    headword: str

    phonetic: str | None = None

    meanings: list[MeaningPayload]

    provenance: ProvenancePayload | None = None


class LookupPayload(
    BaseModel,
):
    """
    A whole lookup on the wire.
    """

    outcome: LookupOutcome

    failure_reason: str | None = None

    entries: list[EntryPayload] = []


def to_wire(
    *,
    lookup: DictionaryLookup,
) -> LookupPayload:
    """
    The payload that carries `lookup`.
    """

    return LookupPayload(
        outcome=lookup.outcome,
        failure_reason=lookup.failure_reason,
        entries=[
            EntryPayload(
                headword=entry.headword,
                phonetic=entry.phonetic,
                meanings=[
                    MeaningPayload(
                        word_class=meaning.word_class,
                        part_of_speech=meaning.part_of_speech,
                        definitions=[
                            DefinitionPayload(
                                text=definition.text,
                                example=definition.example,
                            )
                            for definition in meaning.definitions
                        ],
                    )
                    for meaning in entry.meanings
                ],
                provenance=_provenance_to_wire(
                    entry.provenance,
                ),
            )
            for entry in lookup.entries
        ],
    )


def from_wire(
    *,
    payload: LookupPayload,
) -> DictionaryLookup:
    """
    The lookup `payload` carries.

    Raises `InvalidDictionaryDataError` when the payload is inconsistent, such
    as a found lookup without entries.
    """

    return DictionaryLookup(
        outcome=payload.outcome,
        entries=tuple(
            DictionaryEntry(
                headword=entry.headword,
                phonetic=entry.phonetic,
                meanings=tuple(
                    Meaning(
                        word_class=meaning.word_class,
                        part_of_speech=meaning.part_of_speech,
                        definitions=tuple(
                            Definition(
                                text=definition.text,
                                example=definition.example,
                            )
                            for definition in meaning.definitions
                        ),
                    )
                    for meaning in entry.meanings
                ),
                provenance=_provenance_from_wire(
                    entry.provenance,
                ),
            )
            for entry in payload.entries
        ),
        failure_reason=payload.failure_reason,
    )


def _provenance_to_wire(
    provenance: Provenance | None,
    /,
) -> ProvenancePayload | None:
    if provenance is None:
        return None

    return ProvenancePayload(
        license=(
            LicensePayload(
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


def _provenance_from_wire(
    payload: ProvenancePayload | None,
    /,
) -> Provenance | None:
    if payload is None:
        return None

    return Provenance(
        license=(
            License(
                name=value_object.name,
                url=value_object.url,
            )
            if (value_object := payload.license) is not None
            else None
        ),
        source_urls=tuple(
            payload.source_urls,
        ),
    )
