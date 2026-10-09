"""
Winnowing: deciding which words deserve a learner's attention, in what order,
and what a learner is shown about each.

The learner profile is the only input besides the words themselves, and its
effect is entirely here, so it can be read and tested in one place.
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
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.domain.cefr import (
    LevelAssessment,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
    Provenance,
    SelectedDefinition,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)


class StudyTier(
    UnorderedStrEnum,
):
    """
    Where a word stands relative to the learner.

    FOCUS words lie between the learner's level and their target level and are
    the ones to study first; STRETCH words lie above the target; REVIEW words
    lie below the learner's level; KNOWN words are the ones the learner said
    they already know.
    """

    FOCUS = auto()

    STRETCH = auto()

    REVIEW = auto()

    KNOWN = auto()


# NOTE:
# The order tiers are reported in, which is the order a learner works through them.
_TIER_ORDER: Final = MappingProxyType(
    mapping={
        tier: index
        for (
            index,
            tier,
        ) in enumerate(
            iterable=StudyTier,
        )
    }
)


class GlossSource(
    UnorderedStrEnum,
):
    """
    Which source a word's one-line meaning came from.
    """

    DICTIONARY = auto()

    LEXICAL_NETWORK = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Gloss:
    """
    The one-line meaning a learner sees for a word, and where it came from.

    `part_of_speech_matched` is `False` when the only meaning available is for
    another part of speech than the word had in the text, and `provenance` is
    the dictionary's own account of a definition it supplied.
    """

    text: str

    source: GlossSource

    part_of_speech_matched: bool

    provenance: Provenance | None = None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DictionaryInformation:
    """
    What the dictionary said about a word, reduced to what a learner sees.

    `definitions` is empty unless the outcome is FOUND, and a learner can
    always tell an absent definition from an unreachable dictionary.
    """

    outcome: LookupOutcome

    failure_reason: str | None

    phonetic: str | None

    definitions: tuple[SelectedDefinition, ...]


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class EnrichedItem:
    """
    A vocabulary item with everything learned about it, before winnowing.

    `dictionary` is `None` when the analysis was run without the dictionary.
    """

    item: VocabularyItem

    zipf_frequency: float

    level: LevelAssessment

    dictionary: DictionaryInformation | None

    senses: tuple[LexicalSense, ...]


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class StudyItem:
    """
    A vocabulary item placed in a study tier for one learner.
    """

    item: VocabularyItem

    zipf_frequency: float

    level: LevelAssessment

    tier: StudyTier

    dictionary: DictionaryInformation | None

    senses: tuple[LexicalSense, ...]

    @property
    def gloss(
        self,
    ) -> Gloss | None:
        """
        The meaning a learner is shown: the dictionary's definition when the
        dictionary had one, otherwise the lexical network's gloss of the most
        used sense, otherwise nothing.
        """

        if self.dictionary is not None and self.dictionary.definitions:
            first = self.dictionary.definitions[0]

            return Gloss(
                text=first.text,
                source=GlossSource.DICTIONARY,
                part_of_speech_matched=first.part_of_speech_matched,
                provenance=first.provenance,
            )

        if self.senses:
            return Gloss(
                text=self.senses[0].gloss,
                source=GlossSource.LEXICAL_NETWORK,
                part_of_speech_matched=True,
            )

        return None


def study_tier(
    *,
    lemma: str,
    level: LevelAssessment,
    profile: LearnerProfile,
) -> StudyTier:
    """
    Place one word relative to the learner.
    """

    if profile.knows(
        lemma=lemma,
    ):
        return StudyTier.KNOWN

    if level.level < profile.level:
        return StudyTier.REVIEW

    if level.level <= profile.target_level:
        return StudyTier.FOCUS

    return StudyTier.STRETCH


def winnow(
    *,
    enriched: Sequence[EnrichedItem],
    profile: LearnerProfile,
) -> tuple[StudyItem, ...]:
    """
    Place every word in its tier and order the result for study.

    Within a tier, a word that occurs more often in the text comes first,
    because it matters more for reading that text; ties go to the word that is
    more common in general English, because it will be met again sooner.
    """

    placed = tuple(
        StudyItem(
            item=entry.item,
            zipf_frequency=entry.zipf_frequency,
            level=entry.level,
            tier=study_tier(
                lemma=entry.item.lemma,
                level=entry.level,
                profile=profile,
            ),
            dictionary=entry.dictionary,
            senses=entry.senses,
        )
        for entry in enriched
    )

    return tuple(
        sorted(
            placed,
            key=lambda study_item: (
                _TIER_ORDER[study_item.tier],
                -study_item.item.occurrence_count,
                -study_item.zipf_frequency,
                study_item.item.lemma,
            ),
        ),
    )
