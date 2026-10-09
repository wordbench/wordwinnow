"""
The learner: a level, a target, and the words they already know.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.learner import (
    InvalidLearnerProfileError,
    LearnerProfile,
    default_target_level,
    known_lemmas_from_lines,
)


@final
class TestLearnerProfile:
    def test_the_target_defaults_to_one_level_above_and_c2_stays_c2(
        self,
    ) -> None:
        assert (
            default_target_level(
                level=CefrLevel.B1,
            )
            is CefrLevel.B2
        )

        assert (
            default_target_level(
                level=CefrLevel.C2,
            )
            is CefrLevel.C2
        )

    def test_a_target_below_the_level_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidLearnerProfileError,
        ):
            LearnerProfile(
                level=CefrLevel.B2,
                target_level=CefrLevel.A1,
            )

    def test_a_known_lemma_not_in_lowercase_is_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidLearnerProfileError,
            match="Treachery",
        ):
            LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
                known_lemmas=frozenset(
                    {
                        "Treachery",
                    },
                ),
            )


@final
class TestKnownLemmasFromLines:
    def test_blank_lines_and_comments_are_ignored_and_case_is_folded(
        self,
    ) -> None:
        assert known_lemmas_from_lines(
            lines=(
                "# the words I already know",
                "",
                "  Photograph ",
                "mask",
            ),
        ) == frozenset(
            {
                "photograph",
                "mask",
            },
        )
