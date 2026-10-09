"""
The benchmark scenarios' pure parts.
"""

from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from wordwinnow.services.bench.scenarios import (
    paragraph_prefix,
)

# NOTE:
# Four paragraphs of three, two, four, and two words: eleven in all.
_STORY: Final = "The King arrived.\n\nHe wore\n\na black vizard mask.\n\nHolmes laughed."


@final
class TestParagraphPrefix:
    def test_a_share_keeps_the_whole_paragraphs_that_reach_it(
        self,
    ) -> None:
        assert (
            paragraph_prefix(
                text=_STORY,
                share=0.25,
            )
            == "The King arrived."
        )

        assert (
            paragraph_prefix(
                text=_STORY,
                share=0.5,
            )
            == "The King arrived.\n\nHe wore\n\na black vizard mask."
        )

    def test_the_whole_share_is_the_whole_text(
        self,
    ) -> None:
        assert (
            paragraph_prefix(
                text=_STORY,
                share=1.0,
            )
            == _STORY
        )

    def test_a_share_outside_the_text_is_refused(
        self,
    ) -> None:
        for share in (
            0.0,
            1.5,
        ):
            with raises(
                expected_exception=ValueError,
            ):
                paragraph_prefix(
                    text=_STORY,
                    share=share,
                )
