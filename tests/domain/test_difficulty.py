"""
Tests for the features a word's difficulty is judged by: its length, its
frequency, and its senses.
"""

from typing import (
    Final,
    final,
)

from wordwinnow.domain.difficulty import (
    ALL_FAMILIES,
    FeatureFamily,
    build_lexical_features,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)

# NOTE:
# The first two of WordNet 3.0's senses of `note`, with their real counts: a written record, which SemCor saw fourteen
# times, and a short letter, which it saw thirteen.
_RECORD: Final = LexicalSense(
    key="note.n.01",
    part_of_speech=PartOfSpeech.NOUN,
    gloss="a brief written record",
    example="he made a note of the appointment",
    synonyms=(),
    hypernyms=("written record",),
    antonyms=(),
    usage_count=14,
    hyponym_count=4,
    category="noun.communication",
)

_LETTER: Final = LexicalSense(
    key="note.n.02",
    part_of_speech=PartOfSpeech.NOUN,
    gloss="a short personal letter",
    example="drop me a line when you get there",
    synonyms=(
        "short letter",
        "line",
        "billet",
    ),
    hypernyms=("personal letter",),
    antonyms=(),
    usage_count=13,
    hyponym_count=1,
    category="noun.communication",
)


@final
class TestBuildLexicalFeatures:
    def test_the_features_are_derived_from_the_lemma_and_its_senses(
        self,
    ) -> None:
        features = build_lexical_features(
            lemma="note",
            part_of_speech=PartOfSpeech.NOUN,
            zipf_frequency=5.04,
            senses=(
                _RECORD,
                _LETTER,
            ),
        )

        assert features.character_count == 4

        assert features.sense_count == 2

        assert features.usage_count == 27

        assert features.hyponym_count == 4

        assert features.category == "noun.communication"

    def test_a_word_the_network_lacks_has_no_category_and_zero_counts(
        self,
    ) -> None:
        features = build_lexical_features(
            lemma="egria",
            part_of_speech=PartOfSpeech.NOUN,
            zipf_frequency=0.0,
            senses=(),
        )

        assert features.sense_count == 0

        assert features.usage_count == 0

        assert features.hyponym_count == 0

        assert features.category is None

    def test_the_families_are_the_three_the_model_can_be_given(
        self,
    ) -> None:
        assert ALL_FAMILIES == (
            FeatureFamily.FREQUENCY,
            FeatureFamily.LEXICAL,
            FeatureFamily.WORDNET,
        )
