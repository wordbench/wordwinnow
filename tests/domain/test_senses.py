"""
Tests for `LexicalSense`: one of a word's senses, as the lexical network lists
it.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    InvalidLexicalSenseError,
    LexicalSense,
)


def _mask(
    *,
    key: str = "mask.n.01",
    gloss: str = "a covering to disguise or conceal the face",
    usage_count: int = 1,
    hyponym_count: int = 2,
    category: str = "noun.artifact",
) -> LexicalSense:
    return LexicalSense(
        key=key,
        part_of_speech=PartOfSpeech.NOUN,
        gloss=gloss,
        example=None,
        synonyms=(),
        hypernyms=(),
        antonyms=(),
        usage_count=usage_count,
        hyponym_count=hyponym_count,
        category=category,
    )


@final
class TestLexicalSense:
    def test_a_sense_as_wordnet_lists_it_is_accepted(
        self,
    ) -> None:
        sense = _mask()

        assert sense.key == "mask.n.01"

        assert sense.category == "noun.artifact"

    def test_a_sense_needs_a_key_a_gloss_a_category_and_non_negative_counts(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidLexicalSenseError,
        ):
            _mask(
                key="",
            )

        with raises(
            expected_exception=InvalidLexicalSenseError,
        ):
            _mask(
                gloss=" ",
            )

        with raises(
            expected_exception=InvalidLexicalSenseError,
        ):
            _mask(
                usage_count=-1,
            )

        with raises(
            expected_exception=InvalidLexicalSenseError,
        ):
            _mask(
                hyponym_count=-1,
            )

        with raises(
            expected_exception=InvalidLexicalSenseError,
        ):
            _mask(
                category=" ",
            )
