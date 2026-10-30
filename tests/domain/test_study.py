"""
Winnowing: where a word's level meets the learner's, and what a learner is
shown about a word.
"""

from dataclasses import (
    replace,
)
from typing import (
    Final,
    final,
)

from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
    SelectedDefinition,
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
    STUDY_TIERS,
    DictionaryInformation,
    EnrichedItem,
    Gloss,
    GlossSource,
    StudyItem,
    StudyTier,
    study_tier,
    winnow,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)

# NOTE:
# WordNet 3.0's first sense of `mask`, the King's, with its real counts.
_MASK_SENSE: Final = LexicalSense(
    key="mask.n.01",
    part_of_speech=PartOfSpeech.NOUN,
    gloss="a covering to disguise or conceal the face",
    example=None,
    synonyms=(),
    hypernyms=(
        "disguise",
        "covering",
    ),
    antonyms=(),
    usage_count=1,
    hyponym_count=2,
    category="noun.artifact",
)

# NOTE:
# WordNet 3.0's two senses of `landau`, both never tagged in its corpus, so the physicist comes first by the order of
# the synsets alone.
_LANDAU_THE_PHYSICIST: Final = LexicalSense(
    key="landau.n.01",
    part_of_speech=PartOfSpeech.NOUN,
    gloss="Soviet physicist who worked on low temperature physics (1908-1968)",
    example=None,
    synonyms=("Lev Davidovich Landau",),
    hypernyms=(),
    antonyms=(),
    usage_count=0,
    hyponym_count=0,
    category="noun.person",
    is_instance=True,
)

_LANDAU_THE_CARRIAGE: Final = LexicalSense(
    key="landau.n.02",
    part_of_speech=PartOfSpeech.NOUN,
    gloss=(
        "a four-wheel covered carriage with a roof divided into two parts (front and back) that can be let down "
        "separately"
    ),
    example=None,
    synonyms=(),
    hypernyms=("carriage",),
    antonyms=(),
    usage_count=0,
    hyponym_count=0,
    category="noun.artifact",
)


def _assessment(
    level: CefrLevel,
    /,
) -> LevelAssessment:
    return LevelAssessment(
        level=level,
        source=LevelSource.REFERENCE_LIST,
    )


# NOTE:
# The lemma and the level are what a case is about; the counts are defaults a case overrides only when it is about
# ordering.
def _enriched(
    lemma: str,
    level: CefrLevel,
    /,
    *,
    occurrence_count: int = 1,
    zipf_frequency: float = 4.0,
) -> EnrichedItem:
    return EnrichedItem(
        item=VocabularyItem(
            lemma=lemma,
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=occurrence_count,
            example_sentence=f"A sentence with {lemma}.",
        ),
        zipf_frequency=zipf_frequency,
        level=_assessment(
            level,
        ),
        dictionary=None,
        senses=(),
    )


def _study_item(
    *,
    dictionary: DictionaryInformation | None,
    senses: tuple[LexicalSense, ...],
) -> StudyItem:
    return StudyItem(
        item=VocabularyItem(
            lemma="mask",
            part_of_speech=PartOfSpeech.NOUN,
            occurrence_count=1,
            example_sentence="The King wore a black mask.",
        ),
        zipf_frequency=4.31,
        level=_assessment(
            CefrLevel.B2,
        ),
        tier=StudyTier.FOCUS,
        dictionary=dictionary,
        senses=senses,
    )


@final
class TestStudyTier:
    def test_a_known_word_is_known_whatever_its_level(
        self,
    ) -> None:
        profile = LearnerProfile(
            level=CefrLevel.A1,
            target_level=CefrLevel.A2,
            known_lemmas=frozenset(
                ("treachery",),
            ),
        )

        assert (
            study_tier(
                lemma="Treachery",
                level=_assessment(
                    CefrLevel.C2,
                ),
                profile=profile,
            )
            is StudyTier.KNOWN
        )

    def test_between_the_level_and_the_target_is_focus(
        self,
    ) -> None:
        profile = LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.C1,
        )

        for level in (
            CefrLevel.B1,
            CefrLevel.B2,
            CefrLevel.C1,
        ):
            assert (
                study_tier(
                    lemma="mask",
                    level=_assessment(
                        level,
                    ),
                    profile=profile,
                )
                is StudyTier.FOCUS
            )

    def test_above_the_target_is_stretch(
        self,
    ) -> None:
        profile = LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
        )

        for level in (
            CefrLevel.C1,
            CefrLevel.C2,
        ):
            assert (
                study_tier(
                    lemma="mask",
                    level=_assessment(
                        level,
                    ),
                    profile=profile,
                )
                is StudyTier.STRETCH
            )

    def test_below_the_level_is_review(
        self,
    ) -> None:
        profile = LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
        )

        for level in (
            CefrLevel.A1,
            CefrLevel.A2,
        ):
            assert (
                study_tier(
                    lemma="mask",
                    level=_assessment(
                        level,
                    ),
                    profile=profile,
                )
                is StudyTier.REVIEW
            )

    def test_the_tiers_to_study_are_focus_and_stretch_and_no_others(
        self,
    ) -> None:
        assert (
            frozenset(
                (
                    StudyTier.FOCUS,
                    StudyTier.STRETCH,
                ),
            )
            == STUDY_TIERS
        )


@final
class TestWinnow:
    def test_tiers_come_in_study_order_and_frequent_words_lead_within_a_tier(
        self,
    ) -> None:
        profile = LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
            known_lemmas=frozenset(
                ("photograph",),
            ),
        )

        enriched = (
            _enriched(
                "king",
                CefrLevel.A1,
                occurrence_count=9,
            ),
            _enriched(
                "photograph",
                CefrLevel.A2,
                occurrence_count=9,
            ),
            _enriched(
                "treachery",
                CefrLevel.C2,
                occurrence_count=9,
            ),
            _enriched(
                "groom",
                CefrLevel.B1,
                occurrence_count=1,
                zipf_frequency=3.63,
            ),
            _enriched(
                "marriage",
                CefrLevel.B1,
                occurrence_count=1,
                zipf_frequency=4.91,
            ),
            _enriched(
                "mask",
                CefrLevel.B2,
                occurrence_count=3,
            ),
        )

        winnowed = winnow(
            enriched=enriched,
            profile=profile,
        )

        assert tuple(
            (
                study_item.item.lemma,
                study_item.tier,
            )
            for study_item in winnowed
        ) == (
            (
                "mask",
                StudyTier.FOCUS,
            ),
            (
                "marriage",
                StudyTier.FOCUS,
            ),
            (
                "groom",
                StudyTier.FOCUS,
            ),
            (
                "treachery",
                StudyTier.STRETCH,
            ),
            (
                "king",
                StudyTier.REVIEW,
            ),
            (
                "photograph",
                StudyTier.KNOWN,
            ),
        )

    def test_ties_break_by_lemma_for_determinism(
        self,
    ) -> None:
        winnowed = winnow(
            enriched=(
                _enriched(
                    "groom",
                    CefrLevel.B1,
                ),
                _enriched(
                    "carriage",
                    CefrLevel.B1,
                ),
            ),
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
            ),
        )

        assert tuple(study_item.item.lemma for study_item in winnowed) == (
            "carriage",
            "groom",
        )


@final
class TestGloss:
    def test_the_dictionary_definition_comes_first(
        self,
    ) -> None:
        study_item = _study_item(
            dictionary=DictionaryInformation(
                outcome=LookupOutcome.FOUND,
                failure_reason=None,
                phonetic=None,
                definitions=(
                    SelectedDefinition(
                        text="A covering that hides the face.",
                        example=None,
                        part_of_speech_matched=False,
                    ),
                ),
            ),
            senses=(_MASK_SENSE,),
        )

        assert study_item.gloss == Gloss(
            text="A covering that hides the face.",
            source=GlossSource.DICTIONARY,
            part_of_speech_matched=False,
        )

    def test_the_network_gloss_stands_in_when_the_dictionary_has_nothing(
        self,
    ) -> None:
        for dictionary in (
            None,
            DictionaryInformation(
                outcome=LookupOutcome.UNAVAILABLE,
                failure_reason="timed_out",
                phonetic=None,
                definitions=(),
            ),
        ):
            assert _study_item(
                dictionary=dictionary,
                senses=(_MASK_SENSE,),
            ).gloss == Gloss(
                text="a covering to disguise or conceal the face",
                source=GlossSource.LEXICAL_NETWORK,
                part_of_speech_matched=True,
            )

    def test_no_source_means_no_gloss(
        self,
    ) -> None:
        assert (
            _study_item(
                dictionary=None,
                senses=(),
            ).gloss
            is None
        )

    def test_a_sense_that_names_someone_gives_way_to_the_first_that_names_a_kind_of_thing(
        self,
    ) -> None:
        study_item = _study_item(
            dictionary=None,
            senses=(
                _LANDAU_THE_PHYSICIST,
                _LANDAU_THE_CARRIAGE,
            ),
        )

        assert study_item.leading_sense == _LANDAU_THE_CARRIAGE

        assert study_item.other_senses == (_LANDAU_THE_PHYSICIST,)

        assert study_item.gloss == Gloss(
            text=_LANDAU_THE_CARRIAGE.gloss,
            source=GlossSource.LEXICAL_NETWORK,
            part_of_speech_matched=True,
        )

    def test_a_word_whose_every_sense_names_someone_leads_with_the_most_used(
        self,
    ) -> None:
        detective = replace(
            _LANDAU_THE_PHYSICIST,
            key="sherlock_holmes.n.01",
            gloss="a fictitious detective in stories by A. Conan Doyle",
            usage_count=19,
        )

        study_item = _study_item(
            dictionary=None,
            senses=(
                detective,
                _LANDAU_THE_PHYSICIST,
            ),
        )

        assert study_item.leading_sense == detective

        assert study_item.other_senses == (_LANDAU_THE_PHYSICIST,)

    def test_without_senses_nothing_leads_and_nothing_else_is_listed(
        self,
    ) -> None:
        study_item = _study_item(
            dictionary=None,
            senses=(),
        )

        assert study_item.leading_sense is None

        assert study_item.other_senses == ()
