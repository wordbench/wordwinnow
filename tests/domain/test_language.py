"""
A token and a sentence, as the linguistic analysis produces them.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.language import (
    InvalidTokenError,
    Sentence,
    Token,
)


@final
class TestTokenAndSentence:
    def test_a_token_needs_a_lemma(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidTokenError,
        ):
            Token(
                surface="photograph",
                lemma="",
                part_of_speech=None,
            )

    def test_a_sentence_needs_text(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidTokenError,
        ):
            Sentence(
                index=0,
                text="   ",
                tokens=(),
            )
