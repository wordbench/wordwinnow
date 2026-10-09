"""
Lexical difficulty: what a word carries that bears on how hard it is, and the
families those facts belong to.

Difficulty here is general, a property of the word rather than of any learner;
the learner's own situation enters only when the study tiers are decided.
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
    Final,
    final,
)

from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)


class FeatureFamily(
    UnorderedStrEnum,
):
    """
    The families a level model can be given, each switchable on its own so an
    experiment can say what each one adds.

    FREQUENCY is how common the word is in general English, LEXICAL is what
    the word looks like on the page, and WORDNET is what the lexical network
    knows about it.
    """

    FREQUENCY = auto()

    LEXICAL = auto()

    WORDNET = auto()


ALL_FAMILIES: Final = tuple(
    FeatureFamily,
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LexicalFeatures:
    """
    Everything a level model is allowed to see about a word.

    Nothing here says anything about the word's level or about a learner.

    `sense_count`, `usage_count`, `hyponym_count`, and `category` come from
    the lexical network: how many senses the word has in its part of speech,
    how often it was tagged in the network's corpus, how general its most used
    sense is, and which semantic category that sense belongs to.
    """

    lemma: str

    part_of_speech: PartOfSpeech

    zipf_frequency: float

    character_count: int

    sense_count: int

    usage_count: int

    hyponym_count: int

    category: str | None


def build_lexical_features(
    *,
    lemma: str,
    part_of_speech: PartOfSpeech,
    zipf_frequency: float,
    senses: Sequence[LexicalSense],
) -> LexicalFeatures:
    """
    Derive a word's features from its lemma, its frequency, and its senses,
    most used first.
    """

    first = senses[0] if senses else None

    return LexicalFeatures(
        lemma=lemma,
        part_of_speech=part_of_speech,
        zipf_frequency=zipf_frequency,
        character_count=len(
            lemma,
        ),
        sense_count=len(
            senses,
        ),
        usage_count=sum(sense.usage_count for sense in senses),
        hyponym_count=first.hyponym_count if first is not None else 0,
        category=first.category if first is not None else None,
    )
