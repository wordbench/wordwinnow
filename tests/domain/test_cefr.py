"""
The CEFR scale and the precedence of level sources.
"""

from typing import (
    final,
)

from pytest import (
    mark,
    raises,
)

from wordwinnow.domain.cefr import (
    DEFAULT_FREQUENCY_THRESHOLDS,
    CefrLevel,
    FrequencyThresholds,
    InvalidLevelError,
    LevelSource,
    assess_level,
    estimate_level_from_frequency,
    lowest_level,
)


@final
class TestCefrLevel:
    def test_ranks_ascend_from_a1_to_c2(
        self,
    ) -> None:
        assert tuple(level.rank for level in CefrLevel) == (
            1,
            2,
            3,
            4,
            5,
            6,
        )

    def test_steps_above_is_signed(
        self,
    ) -> None:
        assert (
            CefrLevel.B2.steps_above(
                other=CefrLevel.A2,
            )
            == 2
        )

        assert (
            CefrLevel.A1.steps_above(
                other=CefrLevel.B1,
            )
            == -2
        )

    def test_a_stored_label_round_trips(
        self,
    ) -> None:
        assert (
            CefrLevel(
                value="B1",
            )
            is CefrLevel.B1
        )


@final
class TestFrequencyHeuristic:
    @mark.parametrize(
        argnames=(
            "zipf_frequency",
            "expected",
        ),
        argvalues=(
            (
                7.5,
                CefrLevel.A1,
            ),
            (
                5.10,
                CefrLevel.A1,
            ),
            (
                5.09,
                CefrLevel.A2,
            ),
            (
                4.65,
                CefrLevel.A2,
            ),
            (
                4.00,
                CefrLevel.B1,
            ),
            (
                3.30,
                CefrLevel.B2,
            ),
            (
                2.75,
                CefrLevel.C1,
            ),
            (
                2.74,
                CefrLevel.C2,
            ),
            (
                0.0,
                CefrLevel.C2,
            ),
        ),
    )
    def test_a_more_frequent_word_gets_a_lower_level(
        self,
        *,
        zipf_frequency: float,
        expected: CefrLevel,
    ) -> None:
        assert (
            estimate_level_from_frequency(
                zipf_frequency=zipf_frequency,
            )
            is expected
        )

    def test_boundaries_must_descend(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidLevelError,
        ):
            FrequencyThresholds(
                boundaries=(
                    6.0,
                    6.0,
                    4.5,
                    3.8,
                    3.0,
                ),
            )

    def test_custom_thresholds_are_honored(
        self,
    ) -> None:
        thresholds = FrequencyThresholds(
            boundaries=(
                5.0,
                4.0,
                3.0,
                2.0,
                1.0,
            ),
        )

        assert (
            estimate_level_from_frequency(
                zipf_frequency=4.5,
                thresholds=thresholds,
            )
            is CefrLevel.A2
        )

        assert (
            estimate_level_from_frequency(
                zipf_frequency=4.5,
                thresholds=DEFAULT_FREQUENCY_THRESHOLDS,
            )
            is CefrLevel.B1
        )


@final
class TestAssessLevel:
    def test_a_reference_list_wins_over_everything(
        self,
    ) -> None:
        assessment = assess_level(
            reference_level=CefrLevel.C1,
            model_level=CefrLevel.A1,
            zipf_frequency=7.0,
        )

        assert assessment.level is CefrLevel.C1

        assert assessment.source is LevelSource.REFERENCE_LIST

    def test_a_model_wins_over_the_heuristic(
        self,
    ) -> None:
        assessment = assess_level(
            reference_level=None,
            model_level=CefrLevel.B2,
            zipf_frequency=7.0,
        )

        assert assessment.level is CefrLevel.B2

        assert assessment.source is LevelSource.MODEL

    def test_the_heuristic_always_has_an_answer(
        self,
    ) -> None:
        assessment = assess_level(
            reference_level=None,
            model_level=None,
            zipf_frequency=7.0,
        )

        assert assessment.level is CefrLevel.A1

        assert assessment.source is LevelSource.FREQUENCY_HEURISTIC


@final
class TestLowestLevel:
    def test_the_most_accessible_level_is_chosen(
        self,
    ) -> None:
        assert (
            lowest_level(
                levels=(
                    CefrLevel.C1,
                    CefrLevel.A2,
                    CefrLevel.B2,
                ),
            )
            is CefrLevel.A2
        )

    def test_no_levels_means_no_answer(
        self,
    ) -> None:
        assert (
            lowest_level(
                levels=(),
            )
            is None
        )
