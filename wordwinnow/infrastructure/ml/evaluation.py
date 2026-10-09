"""
How a set of level predictions is scored against a set of labels, and how the
heuristic's boundaries are fitted to labels.

Every score here is relative to whatever labels are passed in.

Against the reference lists it measures validity, against the heuristic's own
pseudo-labels it measures fidelity, and the caller is the one who knows which.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from math import (
    inf,
)
from typing import (
    Final,
    final,
)

from numpy import (
    array,
    float64,
    int64,
)
from numpy.typing import (
    NDArray,
)
from scipy.stats import (
    spearmanr,
)
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

from wordwinnow.domain.cefr import (
    DEFAULT_FREQUENCY_THRESHOLDS,
    LEVELS_ASCENDING,
    CefrLevel,
    FrequencyThresholds,
)

# NOTE:
# The Zipf scale runs from 0 for an unknown word to about 8 for `the`.
#
# The boundary search covers 1.5 to 7.5 in steps of one twentieth, which brackets every default boundary with room on
# both sides and is finer than the differences the defaults were set with.
_GRID_START: Final = 1.5

_GRID_STEP: Final = 0.05

_GRID_STEPS: Final = 121

_GRID: Final = tuple(
    round(
        number=_GRID_START + _GRID_STEP * step,
        ndigits=2,
    )
    for step in range(
        _GRID_STEPS,
    )
)

# NOTE:
# A pass that moves no boundary ends the search, and a bound on passes keeps a pathological dataset from cycling.
_MAX_PASSES: Final = 20


@final
class EvaluationError(
    ValueError,
):
    """
    Raised when predictions cannot be scored, or boundaries fitted, against
    labels.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ClassReport:
    """
    How the predictions did on one level.
    """

    level: CefrLevel

    precision: float

    recall: float

    f1: float

    support: int


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class EvaluationReport:
    """
    How a set of predictions compares with a set of labels.

    `mean_absolute_level_error` is the average distance in levels and the
    project's primary measure, because the scale is ordinal and B1 for a B2
    word is a smaller mistake than A1 for it; `within_one_level_accuracy`
    counts a prediction one step off as a hit for the same reason.

    `spearman` is the rank correlation between predictions and labels.

    `confusion` has one row per expected level and one column per predicted
    level, both in the order `labels` gives.
    """

    accuracy: float

    macro_f1: float

    within_one_level_accuracy: float

    mean_absolute_level_error: float

    spearman: float

    per_class: tuple[ClassReport, ...]

    confusion: tuple[tuple[int, ...], ...]

    labels: tuple[CefrLevel, ...]

    n: int


def evaluate(
    *,
    expected: Sequence[CefrLevel],
    predicted: Sequence[CefrLevel],
) -> EvaluationReport:
    """
    Score `predicted` against `expected`, pair by pair.
    """

    if len(
        expected,
    ) != len(
        predicted,
    ):
        raise EvaluationError(
            f"an evaluation requires one prediction per label, got {
                len(
                    expected,
                )
            } labels and {
                len(
                    predicted,
                )
            } predictions",
        )

    if not expected:
        raise EvaluationError(
            "an evaluation requires at least one label",
        )

    truth = _ranks(
        expected,
    )

    guess = _ranks(
        predicted,
    )

    ranks = tuple(level.rank for level in LEVELS_ASCENDING)

    distance = abs(
        truth - guess,
    )

    (
        precisions,
        recalls,
        f1s,
        supports,
    ) = precision_recall_fscore_support(
        y_true=truth,
        y_pred=guess,
        labels=ranks,
        zero_division=0.0,
    )

    return EvaluationReport(
        accuracy=float(
            accuracy_score(
                y_true=truth,
                y_pred=guess,
            ),
        ),
        macro_f1=float(
            f1_score(
                y_true=truth,
                y_pred=guess,
                average="macro",
                zero_division=0.0,
            ),
        ),
        within_one_level_accuracy=float(
            (distance <= 1).mean(),
        ),
        mean_absolute_level_error=float(
            distance.mean(),
        ),
        spearman=_spearman(
            truth,
            guess,
        ),
        per_class=tuple(
            ClassReport(
                level=level,
                precision=float(
                    precision,
                ),
                recall=float(
                    recall,
                ),
                f1=float(
                    f1,
                ),
                support=int(
                    support,
                ),
            )
            for (
                level,
                precision,
                recall,
                f1,
                support,
            ) in zip(
                LEVELS_ASCENDING,
                precisions,
                recalls,
                f1s,
                supports,
                strict=True,
            )
        ),
        confusion=tuple(
            tuple(
                row,
            )
            for row in confusion_matrix(
                y_true=truth,
                y_pred=guess,
                labels=ranks,
            ).tolist()
        ),
        labels=LEVELS_ASCENDING,
        n=len(
            expected,
        ),
    )


def _ranks(
    levels: Sequence[CefrLevel],
    /,
) -> NDArray[int64]:
    return array(
        object=[level.rank for level in levels],
        dtype=int64,
    )


def _spearman(
    truth: NDArray[int64],
    guess: NDArray[int64],
    /,
) -> float:
    # NOTE:
    # A constant array has no rank correlation with anything, and scipy answers `nan`; zero is the honest number for a
    # predictor that always says the same level.
    if truth.min() == truth.max() or guess.min() == guess.max():
        return 0.0

    return float(
        spearmanr(
            a=truth,
            b=guess,
        ).statistic,
    )


def tune_thresholds(
    *,
    zipf_frequencies: Sequence[float],
    levels: Sequence[CefrLevel],
    starting_point: FrequencyThresholds = DEFAULT_FREQUENCY_THRESHOLDS,
) -> FrequencyThresholds:
    """
    Fit the heuristic's five boundaries to the labels of these words, never
    scoring worse on them than `starting_point` and keeping the boundaries
    strictly descending.

    A score on the words the boundaries were fitted to is a training score,
    and only a score on other words says how the tuned heuristic generalizes.
    """

    if not levels:
        raise EvaluationError(
            "threshold tuning requires at least one label",
        )

    if len(
        zipf_frequencies,
    ) != len(
        levels,
    ):
        raise EvaluationError(
            f"threshold tuning requires one frequency per label, got {
                len(
                    zipf_frequencies,
                )
            } frequencies and {
                len(
                    levels,
                )
            } labels",
        )

    zipf = array(
        object=zipf_frequencies,
        dtype=float64,
    )

    expected = array(
        object=[level.rank - 1 for level in levels],
        dtype=int64,
    )

    # NOTE:
    # The search moves one boundary at a time along the grid and keeps a move only when the mean absolute level error
    # strictly falls, so it can only improve on the starting point and always ends.
    boundaries = list(
        starting_point.boundaries,
    )

    count = len(
        boundaries,
    )

    best = _mean_absolute_error(
        expected,
        _predict_indices(
            zipf,
            boundaries,
        ),
    )

    for _ in range(
        _MAX_PASSES,
    ):
        moved = False

        for position in range(
            count,
        ):
            upper = boundaries[position - 1] if position > 0 else inf

            lower = boundaries[position + 1] if position + 1 < count else -inf

            for candidate in _GRID:
                if not lower < candidate < upper or candidate == boundaries[position]:
                    continue

                trial = boundaries.copy()

                trial[position] = candidate

                score = _mean_absolute_error(
                    expected,
                    _predict_indices(
                        zipf,
                        trial,
                    ),
                )

                if score < best:
                    best = score

                    boundaries = trial

                    moved = True

        if not moved:
            break

    return FrequencyThresholds(
        boundaries=(
            boundaries[0],
            boundaries[1],
            boundaries[2],
            boundaries[3],
            boundaries[4],
        ),
    )


def _predict_indices(
    zipf: NDArray[float64],
    boundaries: Sequence[float],
    /,
) -> NDArray[int64]:
    # NOTE:
    # A word's index is the number of boundaries strictly above its frequency, which is the same answer
    # `estimate_level_from_frequency` gives one word at a time.
    above = (
        zipf[:, None]
        < array(
            object=boundaries,
            dtype=float64,
        )[None, :]
    )

    return above.sum(
        axis=1,
        dtype=int64,
    )


def _mean_absolute_error(
    expected: NDArray[int64],
    predicted: NDArray[int64],
    /,
) -> float:
    return float(
        abs(
            expected - predicted,
        ).mean(),
    )
