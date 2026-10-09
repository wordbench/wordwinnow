"""
The Common European Framework of Reference scale, and how a level is assigned
to a word.

A level has three possible sources, and the source is part of the answer: a
level read from a reference list is an external judgment, a level from a model
is a prediction whose measured quality is documented, and a level from the
frequency heuristic is a fallback whose measured quality is documented too.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from enum import (
    StrEnum,
    auto,
)
from types import (
    MappingProxyType,
)
from typing import (
    Any,
    Final,
    final,
    override,
)

from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)


@final
class InvalidLevelError(
    ValueError,
):
    """
    Raised when frequency thresholds are not strictly descending.
    """


class CefrLevel(
    StrEnum,
):
    """
    A CEFR level, from A1 (beginner) to C2 (mastery).

    Members compare equal to their labels so a stored level round-trips, and
    they order by their place on the scale, which `rank` gives as a number.
    """

    @staticmethod
    @override
    def _generate_next_value_(
        name: str,
        start: int,
        count: int,
        last_values: list[Any],
    ) -> str:
        return name

    A1 = auto()

    A2 = auto()

    B1 = auto()

    B2 = auto()

    C1 = auto()

    C2 = auto()

    @property
    def rank(
        self,
    ) -> int:
        """
        The level's position on the scale, from 1 for A1 to 6 for C2.
        """

        return _RANKS[self]

    def steps_above(
        self,
        *,
        other: CefrLevel,
    ) -> int:
        """
        How many levels this one lies above `other`; negative when below.
        """

        return self.rank - other.rank

    @override
    def __lt__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(
            other,
            CefrLevel,
        ):
            return NotImplemented

        return self.rank < other.rank

    @override
    def __le__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(
            other,
            CefrLevel,
        ):
            return NotImplemented

        return self.rank <= other.rank

    @override
    def __gt__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(
            other,
            CefrLevel,
        ):
            return NotImplemented

        return self.rank > other.rank

    @override
    def __ge__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(
            other,
            CefrLevel,
        ):
            return NotImplemented

        return self.rank >= other.rank


_RANKS: Final = MappingProxyType(
    mapping={
        level: index
        for (
            index,
            level,
        ) in enumerate(
            iterable=CefrLevel,
            start=1,
        )
    },
)

LEVELS_ASCENDING: Final = tuple(
    CefrLevel,
)


class LevelSource(
    UnorderedStrEnum,
):
    """
    Where a word's level came from, in order of precedence.
    """

    REFERENCE_LIST = auto()

    MODEL = auto()

    FREQUENCY_HEURISTIC = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LevelAssessment:
    """
    A level together with the source that assigned it.
    """

    level: CefrLevel

    source: LevelSource


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class FrequencyThresholds:
    """
    The Zipf-frequency boundaries of the heuristic, from A1 downward.

    A word at or above the first boundary is A1, at or above the second is A2,
    and so on; a word below the last boundary is C2.
    """

    boundaries: tuple[float, float, float, float, float]

    def __post_init__(
        self,
    ) -> None:
        for (
            higher,
            lower,
        ) in zip(
            self.boundaries,
            self.boundaries[1:],
            strict=False,
        ):
            if higher <= lower:
                raise InvalidLevelError(
                    f"frequency thresholds require strictly descending boundaries, got {self.boundaries}",
                )


# PERF:
# These boundaries were fitted once on the training split of the reference lists, moving one boundary at a time in
# steps of 0.05 to raise the macro-F1, from the academic prototype's 6.0, 5.2, 4.5, 3.8, and 3.0, and nothing fits
# them again; the same search reaches them from other starting points too.
#
# On the held-out split of the reference lists (`notebooks/cefr_level_classification.ipynb`) these boundaries have a
# level error of 0.843, a within-one-level accuracy of 0.809, and a macro-F1 of 0.376, and they recall 0.455 of the C2
# words; boundaries refitted for the level error lower it to 0.811 but recall only 0.116 of C2, so these stay.
#
# They answer for a word outside the lists only when no level model is installed: the model's level error is lower
# still, 0.732, but it recalls 0.074 of C2, so a product with the model places fewer words at C2 than these would.
DEFAULT_FREQUENCY_THRESHOLDS: Final = FrequencyThresholds(
    boundaries=(
        5.10,
        4.65,
        4.00,
        3.30,
        2.75,
    ),
)


def estimate_level_from_frequency(
    *,
    zipf_frequency: float,
    thresholds: FrequencyThresholds = DEFAULT_FREQUENCY_THRESHOLDS,
) -> CefrLevel:
    """
    Assign a level from how common a word is in general English.

    The more frequent the word, the lower the level.
    """

    for (
        level,
        boundary,
    ) in zip(
        LEVELS_ASCENDING,
        thresholds.boundaries,
        strict=False,
    ):
        if zipf_frequency >= boundary:
            return level

    return CefrLevel.C2


def assess_level(
    *,
    reference_level: CefrLevel | None,
    model_level: CefrLevel | None,
    zipf_frequency: float,
    thresholds: FrequencyThresholds = DEFAULT_FREQUENCY_THRESHOLDS,
) -> LevelAssessment:
    """
    Combine the available judgments into one assessment.

    A reference list wins over a model, and a model wins over the frequency
    heuristic, which always has an answer.
    """

    if reference_level is not None:
        return LevelAssessment(
            level=reference_level,
            source=LevelSource.REFERENCE_LIST,
        )

    if model_level is not None:
        return LevelAssessment(
            level=model_level,
            source=LevelSource.MODEL,
        )

    return LevelAssessment(
        level=estimate_level_from_frequency(
            zipf_frequency=zipf_frequency,
            thresholds=thresholds,
        ),
        source=LevelSource.FREQUENCY_HEURISTIC,
    )


def lowest_level(
    *,
    levels: Sequence[CefrLevel],
) -> CefrLevel | None:
    """
    The most accessible of several levels, or `None` when there are none.
    """

    if not levels:
        return None

    return min(
        levels,
    )
