"""
What ordering means for the domain's enumerations: a level is a place on a
scale, every other member is a label.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.analysis import (
    AnalysisStatus,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    lowest_level,
)
from wordwinnow.domain.study import (
    StudyTier,
)


@final
class TestCefrLevelOrdering:
    def test_levels_order_by_their_place_on_the_scale(
        self,
    ) -> None:
        assert CefrLevel.A1 < CefrLevel.A2 < CefrLevel.B1 < CefrLevel.B2 < CefrLevel.C1 < CefrLevel.C2

        assert CefrLevel.C2 >= CefrLevel.C2

        assert not CefrLevel.B1 > CefrLevel.B2

        assert sorted(
            (
                CefrLevel.C1,
                CefrLevel.A2,
                CefrLevel.B1,
            ),
        ) == [
            CefrLevel.A2,
            CefrLevel.B1,
            CefrLevel.C1,
        ]

    def test_a_level_still_compares_equal_to_its_label(
        self,
    ) -> None:
        assert CefrLevel.B2 == "B2"

        assert (
            lowest_level(
                levels=(
                    CefrLevel.C2,
                    CefrLevel.A1,
                ),
            )
            is CefrLevel.A1
        )


@final
class TestUnorderedEnums:
    def test_labels_refuse_to_order(
        self,
    ) -> None:
        with raises(
            expected_exception=TypeError,
        ):
            _ = StudyTier.FOCUS < StudyTier.KNOWN

        with raises(
            expected_exception=TypeError,
        ):
            _ = AnalysisStatus.REQUESTED <= AnalysisStatus.COMPLETED

    def test_labels_still_serialize_as_their_values(
        self,
    ) -> None:
        assert f"{StudyTier.FOCUS}" == "focus"

        assert StudyTier.FOCUS == "focus"

        assert (
            AnalysisStatus(
                value="completed",
            )
            is AnalysisStatus.COMPLETED
        )
