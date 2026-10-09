"""
The feature matrix: its column layout per family and its rows.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.difficulty import (
    ALL_FAMILIES,
    FeatureFamily,
    LexicalFeatures,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.ml.features import (
    CATEGORIES,
    columns_for,
    encode,
)


def _features(
    *,
    category: str | None,
) -> LexicalFeatures:
    return LexicalFeatures(
        lemma="information",
        part_of_speech=PartOfSpeech.VERB,
        zipf_frequency=5.5,
        character_count=11,
        sense_count=5,
        usage_count=40,
        hyponym_count=3,
        category=category,
    )


@final
class TestColumnsFor:
    def test_every_family_adds_its_columns_in_family_order(
        self,
    ) -> None:
        assert columns_for(
            families=(FeatureFamily.FREQUENCY,),
        ) == ("zipf_frequency",)

        assert columns_for(
            families=(
                FeatureFamily.LEXICAL,
                FeatureFamily.FREQUENCY,
            ),
        ) == (
            "zipf_frequency",
            "character_count",
            "part_of_speech_noun",
            "part_of_speech_verb",
            "part_of_speech_adjective",
            "part_of_speech_adverb",
        )

        wordnet = columns_for(
            families=ALL_FAMILIES,
        )

        assert wordnet[6:9] == (
            "sense_count",
            "usage_count",
            "hyponym_count",
        )

        assert wordnet[9:] == tuple(f"category_{category}" for category in CATEGORIES)

    def test_no_family_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="at least one feature family",
        ):
            columns_for(
                families=(),
            )


@final
class TestEncode:
    def test_a_row_follows_the_column_order_of_its_families(
        self,
    ) -> None:
        matrix = encode(
            features=[
                _features(
                    category="noun.food",
                ),
            ],
            families=ALL_FAMILIES,
        )

        columns = columns_for(
            families=ALL_FAMILIES,
        )

        row = dict(
            zip(
                columns,
                matrix[0].tolist(),
                strict=True,
            ),
        )

        assert row["zipf_frequency"] == 5.5

        assert row["character_count"] == 11.0

        assert row["part_of_speech_verb"] == 1.0

        assert row["part_of_speech_noun"] == 0.0

        assert row["sense_count"] == 5.0

        assert row["usage_count"] == 40.0

        assert row["hyponym_count"] == 3.0

        assert row["category_noun.food"] == 1.0

        assert (
            sum(
                value
                for (
                    name,
                    value,
                ) in row.items()
                if name.startswith(
                    "category_",
                )
            )
            == 1.0
        )

    def test_a_word_without_a_category_has_no_category_column_set(
        self,
    ) -> None:
        matrix = encode(
            features=[
                _features(
                    category=None,
                ),
            ],
            families=(FeatureFamily.WORDNET,),
        )

        assert matrix[0][3:].sum() == 0.0

    def test_no_words_give_an_empty_matrix_with_every_column(
        self,
    ) -> None:
        assert encode(
            features=[],
            families=(FeatureFamily.FREQUENCY,),
        ).shape == (
            0,
            1,
        )
