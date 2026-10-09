"""
Which tokens are vocabulary, and how they are grouped.
"""

from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
    Sentence,
    Token,
)
from wordwinnow.domain.vocabulary import (
    InvalidVocabularyItemError,
    VocabularyItem,
    collect_vocabulary,
    is_vocabulary,
)

_FUNCTION_WORDS: Final = frozenset(
    {
        "the",
        "very",
    },
)


# NOTE:
# The surface and the class are what a case is about; the lemma and the name flag are overrides a case rarely needs,
# which is the structural-versus-configuration split the mixed signature declares.
def _token(
    surface: str,
    part_of_speech: PartOfSpeech | None,
    /,
    *,
    lemma: str | None = None,
    is_proper_noun: bool = False,
) -> Token:
    return Token(
        surface=surface,
        lemma=lemma if lemma is not None else surface.lower(),
        part_of_speech=part_of_speech,
        is_proper_noun=is_proper_noun,
    )


@final
class TestIsVocabulary:
    def test_a_content_word_is_vocabulary(
        self,
    ) -> None:
        assert is_vocabulary(
            token=_token(
                "grooms",
                PartOfSpeech.NOUN,
                lemma="groom",
            ),
            function_words=_FUNCTION_WORDS,
        )

    def test_a_closed_class_word_is_not(
        self,
    ) -> None:
        assert not is_vocabulary(
            token=_token(
                "the",
                None,
            ),
            function_words=_FUNCTION_WORDS,
        )

    def test_a_function_word_in_an_open_class_is_not(
        self,
    ) -> None:
        assert not is_vocabulary(
            token=_token(
                "very",
                PartOfSpeech.ADVERB,
            ),
            function_words=_FUNCTION_WORDS,
        )

    def test_a_name_is_not(
        self,
    ) -> None:
        assert not is_vocabulary(
            token=_token(
                "Holmes",
                PartOfSpeech.NOUN,
                is_proper_noun=True,
            ),
            function_words=_FUNCTION_WORDS,
        )

    def test_a_number_or_a_symbol_is_not(
        self,
    ) -> None:
        for surface in (
            "3rd",
            "'s",
            "--",
            "well_being",
        ):
            assert not is_vocabulary(
                token=_token(
                    surface,
                    PartOfSpeech.NOUN,
                ),
                function_words=_FUNCTION_WORDS,
            )

    def test_a_hyphenated_word_is(
        self,
    ) -> None:
        assert is_vocabulary(
            token=_token(
                "smoke-rocket",
                PartOfSpeech.NOUN,
            ),
            function_words=_FUNCTION_WORDS,
        )


@final
class TestCollectVocabulary:
    def test_items_are_grouped_by_lemma_and_part_of_speech_in_first_appearance_order(
        self,
    ) -> None:
        sentences = (
            Sentence(
                index=0,
                text="The cabs wait.",
                tokens=(
                    _token(
                        "The",
                        None,
                    ),
                    _token(
                        "cabs",
                        PartOfSpeech.NOUN,
                        lemma="cab",
                    ),
                    _token(
                        "wait",
                        PartOfSpeech.VERB,
                    ),
                ),
            ),
            Sentence(
                index=1,
                text="A cab waits very patiently.",
                tokens=(
                    _token(
                        "cab",
                        PartOfSpeech.NOUN,
                    ),
                    _token(
                        "waits",
                        PartOfSpeech.VERB,
                        lemma="wait",
                    ),
                    _token(
                        "very",
                        PartOfSpeech.ADVERB,
                    ),
                    _token(
                        "patiently",
                        PartOfSpeech.ADVERB,
                    ),
                ),
            ),
        )

        items = collect_vocabulary(
            sentences=sentences,
            function_words=_FUNCTION_WORDS,
        )

        assert items == (
            VocabularyItem(
                lemma="cab",
                part_of_speech=PartOfSpeech.NOUN,
                occurrence_count=2,
                example_sentence="The cabs wait.",
            ),
            VocabularyItem(
                lemma="wait",
                part_of_speech=PartOfSpeech.VERB,
                occurrence_count=2,
                example_sentence="The cabs wait.",
            ),
            VocabularyItem(
                lemma="patiently",
                part_of_speech=PartOfSpeech.ADVERB,
                occurrence_count=1,
                example_sentence="A cab waits very patiently.",
            ),
        )

    def test_the_same_lemma_in_two_parts_of_speech_is_two_items(
        self,
    ) -> None:
        sentences = (
            Sentence(
                index=0,
                text="Holmes had to witness the marriage as its witness.",
                tokens=(
                    _token(
                        "witness",
                        PartOfSpeech.VERB,
                    ),
                    _token(
                        "witness",
                        PartOfSpeech.NOUN,
                    ),
                ),
            ),
        )

        items = collect_vocabulary(
            sentences=sentences,
            function_words=_FUNCTION_WORDS,
        )

        assert tuple(item.part_of_speech for item in items) == (
            PartOfSpeech.VERB,
            PartOfSpeech.NOUN,
        )


@final
class TestInvariants:
    def test_a_vocabulary_item_needs_an_occurrence(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidVocabularyItemError,
        ):
            VocabularyItem(
                lemma="photograph",
                part_of_speech=PartOfSpeech.NOUN,
                occurrence_count=0,
                example_sentence="Irene Adler kept the photograph.",
            )
