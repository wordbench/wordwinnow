"""
Dictionary information about a word, and how to choose what a learner sees.

The model is the project's own: a dictionary entry has meanings, a meaning
belongs to a part of speech and has definitions, and a definition may carry an
example.

A lookup has three outcomes, and they are not interchangeable: the word was
found, the dictionary does not have it, or the dictionary could not be
reached.

Only the first two are facts about the word.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from enum import (
    auto,
)
from typing import (
    final,
)

from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)


@final
class InvalidDictionaryDataError(
    ValueError,
):
    """
    Raised when dictionary data would be inconsistent.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Definition:
    """
    One definition, with an example sentence when the dictionary has one.
    """

    text: str

    example: str | None = None

    def __post_init__(
        self,
    ) -> None:
        if not self.text.strip():
            raise InvalidDictionaryDataError(
                "a definition requires non-blank text",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Meaning:
    """
    The definitions of a headword in one part of speech.

    `part_of_speech` is `None` when the dictionary's word class is outside the
    four the project studies, and `word_class` keeps the dictionary's own
    label either way.
    """

    word_class: str

    part_of_speech: PartOfSpeech | None

    definitions: tuple[Definition, ...]

    def __post_init__(
        self,
    ) -> None:
        if not self.definitions:
            raise InvalidDictionaryDataError(
                "a meaning requires at least one definition",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class License:
    """
    The license a dictionary publishes its content under: its name, and where
    to read it.
    """

    name: str

    url: str

    def __post_init__(
        self,
    ) -> None:
        if not self.name.strip():
            raise InvalidDictionaryDataError(
                "a license requires a non-blank name",
            )

        if not self.url.strip():
            raise InvalidDictionaryDataError(
                "a license requires a non-blank url",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Provenance:
    """
    Where a dictionary entry comes from and the terms it may be shown under,
    as far as the dictionary says.
    """

    license: License | None = None

    source_urls: tuple[str, ...] = ()

    def __post_init__(
        self,
    ) -> None:
        if self.license is None and not self.source_urls:
            raise InvalidDictionaryDataError(
                "a provenance requires a license or a source url",
            )

        if any(not url.strip() for url in self.source_urls):
            raise InvalidDictionaryDataError(
                "a provenance's source urls must be non-blank",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DictionaryEntry:
    """
    One headword with its meanings and, when known, its pronunciation and its
    provenance.
    """

    headword: str

    phonetic: str | None

    meanings: tuple[Meaning, ...]

    provenance: Provenance | None = None

    def __post_init__(
        self,
    ) -> None:
        if not self.headword.strip():
            raise InvalidDictionaryDataError(
                "a dictionary entry requires a non-blank headword",
            )

        if not self.meanings:
            raise InvalidDictionaryDataError(
                "a dictionary entry requires at least one meaning",
            )


class LookupOutcome(
    UnorderedStrEnum,
):
    """
    What a dictionary lookup established.
    """

    FOUND = auto()

    NOT_FOUND = auto()

    UNAVAILABLE = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DictionaryLookup:
    """
    The result of asking a dictionary about a word.

    Entries exist exactly when the word was found, and a failure reason exists
    exactly when the dictionary was unavailable.
    """

    outcome: LookupOutcome

    entries: tuple[DictionaryEntry, ...] = ()

    failure_reason: str | None = None

    def __post_init__(
        self,
    ) -> None:
        if self.outcome is LookupOutcome.FOUND and not self.entries:
            raise InvalidDictionaryDataError(
                "a found lookup requires at least one entry",
            )

        if self.outcome is not LookupOutcome.FOUND and self.entries:
            raise InvalidDictionaryDataError(
                f"only a found lookup has entries, and this {self.outcome} one has {
                    len(
                        self.entries,
                    )
                }",
            )

        if self.outcome is LookupOutcome.UNAVAILABLE and not self.failure_reason:
            raise InvalidDictionaryDataError(
                "an unavailable lookup requires a failure reason",
            )

        if self.outcome is not LookupOutcome.UNAVAILABLE and self.failure_reason:
            raise InvalidDictionaryDataError(
                f"only an unavailable lookup has a failure reason, and this one is {self.outcome}",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class SelectedDefinition:
    """
    A definition chosen for a learner, whether it matched the part of speech
    the word had in the text, and the provenance of the entry it came from.
    """

    text: str

    example: str | None

    part_of_speech_matched: bool

    provenance: Provenance | None = None


def select_definitions(
    *,
    entries: Sequence[DictionaryEntry],
    part_of_speech: PartOfSpeech,
    limit: int,
) -> tuple[SelectedDefinition, ...]:
    """
    Choose the definitions a learner should see for a word as it was used.

    Definitions in the word's own part of speech come first, across every
    entry, in dictionary order; when the dictionary has none, the first
    definition of any other part of speech is shown and marked as such,
    because an unrelated definition is still better than silence.
    """

    if limit < 1:
        raise ValueError(
            f"the definition limit must be positive, got {limit}",
        )

    matched = tuple(
        SelectedDefinition(
            text=definition.text,
            example=definition.example,
            part_of_speech_matched=True,
            provenance=entry.provenance,
        )
        for entry in entries
        for meaning in entry.meanings
        if meaning.part_of_speech is part_of_speech
        for definition in meaning.definitions
    )

    if matched:
        return matched[:limit]

    for entry in entries:
        for meaning in entry.meanings:
            first = meaning.definitions[0]

            return (
                SelectedDefinition(
                    text=first.text,
                    example=first.example,
                    part_of_speech_matched=False,
                    provenance=entry.provenance,
                ),
            )

    return ()
