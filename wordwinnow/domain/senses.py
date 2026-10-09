"""
Lexical-semantic information about a word: its senses and their relations.

A sense is one meaning of a word within a lexical network.

The senses of a word are an ordering by how often each sense is used, never an
assertion about which sense the text meant; the project does not disambiguate
senses in context and does not claim to.
"""

from dataclasses import (
    dataclass,
)
from typing import (
    final,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
)


@final
class InvalidLexicalSenseError(
    ValueError,
):
    """
    Raised when a lexical sense would contain invalid data.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LexicalSense:
    """
    One sense of a word, with the relations a learner can use to place it and
    the facts about it that bear on the word's difficulty.

    `synonyms` are the other words that share the sense, `hypernyms` name the
    broader concepts one step up, and `antonyms` name the opposites, each as
    plain words rather than as keys into the lexical network.

    `usage_count` is how often the sense was observed in the network's tagged
    corpus, which is what orders senses.

    `hyponym_count` is how many narrower senses sit one step below, which is
    how general the sense is.

    `category` is the network's own semantic category of the sense, such as
    `noun.food`.
    """

    key: str

    part_of_speech: PartOfSpeech

    gloss: str

    example: str | None

    synonyms: tuple[str, ...]

    hypernyms: tuple[str, ...]

    antonyms: tuple[str, ...]

    usage_count: int

    hyponym_count: int

    category: str

    def __post_init__(
        self,
    ) -> None:
        if not self.key:
            raise InvalidLexicalSenseError(
                "a sense requires a non-empty key",
            )

        if not self.gloss.strip():
            raise InvalidLexicalSenseError(
                "a sense requires a non-blank gloss",
            )

        if self.usage_count < 0:
            raise InvalidLexicalSenseError(
                f"a sense's usage count cannot be negative, got {self.usage_count}",
            )

        if self.hyponym_count < 0:
            raise InvalidLexicalSenseError(
                f"a sense's hyponym count cannot be negative, got {self.hyponym_count}",
            )

        if not self.category.strip():
            raise InvalidLexicalSenseError(
                "a sense requires a non-blank category",
            )
