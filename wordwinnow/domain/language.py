"""
The linguistic units an analysis is made of: parts of speech, tokens, and
sentences.

The domain is tagset-agnostic: a token arrives already carrying the part of
speech and the lemma the linguistic adapter derived, so nothing here knows
about Penn Treebank tags or any other tagger vocabulary.
"""

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


@final
class InvalidTokenError(
    ValueError,
):
    """
    Raised when a token or a sentence would contain invalid data.
    """


class PartOfSpeech(
    UnorderedStrEnum,
):
    """
    The four open word classes a learner studies as vocabulary.

    Members compare equal to their lowercase names so a stored value
    round-trips through a database column or a message.
    """

    NOUN = auto()

    VERB = auto()

    ADJECTIVE = auto()

    ADVERB = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Token:
    """
    One word-like unit of a sentence, as the linguistic adapter analyzed it.

    `part_of_speech` is `None` for anything outside the four open classes,
    such as a determiner, a number, or punctuation.

    `is_proper_noun` marks a name, which is never vocabulary to study.
    """

    surface: str

    lemma: str

    part_of_speech: PartOfSpeech | None

    is_proper_noun: bool = False

    def __post_init__(
        self,
    ) -> None:
        if not self.surface:
            raise InvalidTokenError(
                "a token requires a non-empty surface form",
            )

        if not self.lemma:
            raise InvalidTokenError(
                "a token requires a non-empty lemma",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Sentence:
    """
    One sentence of a document with its tokens, in reading order.
    """

    index: int

    text: str

    tokens: tuple[Token, ...]

    def __post_init__(
        self,
    ) -> None:
        if self.index < 0:
            raise InvalidTokenError(
                f"a sentence's index cannot be negative, got {self.index}",
            )

        if not self.text.strip():
            raise InvalidTokenError(
                "a sentence requires non-blank text",
            )
