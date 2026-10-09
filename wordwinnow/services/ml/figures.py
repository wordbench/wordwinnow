"""
The figures an experiment run writes: every method's error and macro-F1 side
by side, the confusion matrix of the chosen model, and the cross-validated
error of every candidate by family set.

Every figure is rendered to a PNG file through matplotlib's Agg backend.
"""

from collections.abc import (
    Sequence,
)
from pathlib import (
    Path,
)
from statistics import (
    pstdev,
)
from typing import (
    Final,
)

from matplotlib.axes import (
    Axes,
)
from matplotlib.backends.backend_agg import (
    FigureCanvasAgg,
)
from matplotlib.colors import (
    LinearSegmentedColormap,
)
from matplotlib.figure import (
    Figure,
)
from numpy import (
    arange,
    array,
    int64,
)

from wordwinnow.infrastructure.ml.evaluation import (
    EvaluationReport,
)
from wordwinnow.infrastructure.ml.model import (
    ModelKind,
)
from wordwinnow.services.ml.experiment import (
    DEFAULT_HEURISTIC_METHOD,
    FAMILY_SETS,
    MAJORITY_METHOD,
    MODEL_METHOD,
    TUNED_HEURISTIC_METHOD,
    Candidate,
    ExperimentResult,
    families_label,
)

COMPARISON_FIGURE: Final = "comparison.png"

CONFUSION_FIGURE: Final = "confusion_matrix.png"

ABLATION_FIGURE: Final = "ablation.png"

# NOTE:
# One hue for every mark and a light-to-dark ramp of the same hue for magnitude, from a palette validated for
# color-vision deficiency; text stays in ink colors so no label carries meaning by color alone.
_SERIES: Final = (
    # NOTE:
    # Medium blue.
    "#2a78d6",
    # NOTE:
    # Light blue.
    "#7fb0e6",
    # NOTE:
    # Dark navy blue.
    "#0d366b",
)

_SEQUENTIAL_RAMP: Final = (
    # NOTE:
    # Very pale blue.
    "#cde2fb",
    # NOTE:
    # Dark navy blue.
    "#0d366b",
)

# NOTE:
# Off-white.
_SURFACE: Final = "#fcfcfb"

# NOTE:
# Near-black.
_INK: Final = "#0b0b0b"

# NOTE:
# Dark warm gray.
_INK_SECONDARY: Final = "#52514e"

# NOTE:
# Medium warm gray.
_INK_MUTED: Final = "#898781"

# NOTE:
# Light warm gray.
_GRIDLINE: Final = "#e1e0d9"

_DOTS_PER_INCH: Final = 150


def write_figures(
    *,
    result: ExperimentResult,
    output_dir: Path,
) -> tuple[Path, ...]:
    """
    Draw the three figures into `output_dir` and return their paths.
    """

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    paths = (
        output_dir / COMPARISON_FIGURE,
        output_dir / CONFUSION_FIGURE,
        output_dir / ABLATION_FIGURE,
    )

    _save(
        _comparison_figure(
            result,
        ),
        paths[0],
    )

    _save(
        _confusion_figure(
            result.chosen.validity,
            result.chosen.name,
        ),
        paths[1],
    )

    _save(
        _ablation_figure(
            result.candidates,
            result.chosen,
        ),
        paths[2],
    )

    return paths


def _comparison_figure(
    result: ExperimentResult,
    /,
) -> Figure:
    figure = _figure(
        9.6,
        4.2,
    )

    (
        error_axes,
        f1_axes,
    ) = figure.subplots(
        nrows=1,
        ncols=2,
    )

    methods = (
        (
            MAJORITY_METHOD,
            result.majority_baseline,
        ),
        (
            DEFAULT_HEURISTIC_METHOD,
            result.heuristic_default,
        ),
        (
            TUNED_HEURISTIC_METHOD,
            result.heuristic_tuned,
        ),
        (
            MODEL_METHOD,
            result.chosen.validity,
        ),
    )

    names = tuple(
        name
        for (
            name,
            _,
        ) in methods
    )

    _bars(
        error_axes,
        names,
        [
            report.mean_absolute_level_error
            for (
                _,
                report,
            ) in methods
        ],
        "Mean absolute level error, lower is better",
        1.5,
    )

    _bars(
        f1_axes,
        names,
        [
            report.macro_f1
            for (
                _,
                report,
            ) in methods
        ],
        "Macro-F1 against the external labels",
        1.0,
    )

    figure.suptitle(
        t="Validity on the test split, by method",
        color=_INK,
    )

    return figure


def _bars(
    axes: Axes,
    names: Sequence[str],
    values: Sequence[float],
    label: str,
    top: float,
    /,
) -> None:
    bars = axes.bar(
        x=list(
            names,
        ),
        height=list(
            values,
        ),
        color=_SERIES[0],
        width=0.6,
    )

    axes.bar_label(
        container=bars,
        fmt="%.3f",
        padding=3,
        color=_INK_SECONDARY,
        fontsize=9,
    )

    axes.set_ylim(
        bottom=0.0,
        top=top,
    )

    axes.set_ylabel(
        ylabel=label,
        color=_INK_SECONDARY,
    )

    axes.tick_params(
        axis="x",
        labelrotation=15,
    )

    _recede(
        axes,
    )

    _hairline_grid(
        axes,
    )


def _confusion_figure(
    report: EvaluationReport,
    name: str,
    /,
) -> Figure:
    figure = _figure(
        6.4,
        5.6,
    )

    axes = figure.subplots()

    counts = array(
        object=report.confusion,
        dtype=int64,
    )

    image = axes.imshow(
        X=counts,
        cmap=LinearSegmentedColormap.from_list(
            name="sequential",
            colors=_SEQUENTIAL_RAMP,
        ),
    )

    positions = range(
        len(
            report.labels,
        ),
    )

    axes.set_xticks(
        ticks=positions,
        labels=report.labels,
    )

    axes.set_yticks(
        ticks=positions,
        labels=report.labels,
    )

    axes.set_xlabel(
        xlabel="Predicted level",
        color=_INK_SECONDARY,
    )

    axes.set_ylabel(
        ylabel="External label",
        color=_INK_SECONDARY,
    )

    # NOTE:
    # The title is the figure's, not the axes', so it centers on the matrix and its colorbar together; the method's
    # name is wider than the matrix, and centered on the axes it ran past the left edge and under the colorbar.
    figure.suptitle(
        t=f"Confusion matrix on the test split\n{name}, n = {report.n}",
        color=_INK,
    )

    threshold = counts.max() * 0.55

    for (
        row,
        values,
    ) in enumerate(
        iterable=report.confusion,
    ):
        for (
            column,
            value,
        ) in enumerate(
            iterable=values,
        ):
            axes.text(
                x=column,
                y=row,
                s=str(
                    object=value,
                ),
                ha="center",
                va="center",
                color=_SURFACE if value > threshold else _INK,
                fontsize=9,
            )

    figure.colorbar(
        mappable=image,
        ax=axes,
    ).set_label(
        label="Words",
        color=_INK_SECONDARY,
    )

    _recede(
        axes,
    )

    return figure


def _ablation_figure(
    candidates: Sequence[Candidate],
    chosen: Candidate,
    /,
) -> Figure:
    figure = _figure(
        8.0,
        4.4,
    )

    axes = figure.subplots()

    kinds = tuple(
        ModelKind,
    )

    count = len(
        kinds,
    )

    width = 0.8 / count

    positions = arange(
        len(
            FAMILY_SETS,
        ),
    )

    for (
        offset,
        kind,
    ) in enumerate(
        iterable=kinds,
    ):
        column = tuple(
            next(
                candidate
                for candidate in candidates
                if candidate.model_kind is kind and candidate.families == families
            )
            for families in FAMILY_SETS
        )

        bars = axes.bar(
            x=positions + (offset - (count - 1) / 2) * width,
            height=[candidate.cross_validated_error for candidate in column],
            width=width,
            color=_SERIES[offset],
            label=kind,
            yerr=[
                pstdev(
                    data=[report.mean_absolute_level_error for report in candidate.cross_validation],
                )
                for candidate in column
            ],
            capsize=3,
            ecolor=_INK_MUTED,
        )

        axes.bar_label(
            container=bars,
            labels=[
                f"{candidate.cross_validated_error:.3f}" + (" *" if candidate is chosen else "")
                for candidate in column
            ],
            padding=4,
            color=_INK_SECONDARY,
            fontsize=8,
        )

    axes.set_xticks(
        ticks=list(
            positions,
        ),
        labels=[
            families_label(
                families=families,
            )
            for families in FAMILY_SETS
        ],
    )

    axes.set_ylim(
        bottom=0.0,
        top=1.2,
    )

    axes.set_ylabel(
        ylabel="Cross-validated mean absolute level error",
        color=_INK_SECONDARY,
    )

    axes.set_title(
        label="What each feature family adds, by estimator kind (* chosen)",
        color=_INK,
    )

    axes.legend(
        loc="upper right",
        frameon=False,
    )

    _recede(
        axes,
    )

    _hairline_grid(
        axes,
    )

    return figure


def _figure(
    width: float,
    height: float,
    /,
) -> Figure:
    figure = Figure(
        figsize=(
            width,
            height,
        ),
    )

    FigureCanvasAgg(
        figure=figure,
    )

    figure.set_facecolor(
        color=_SURFACE,
    )

    return figure


def _recede(
    axes: Axes,
    /,
) -> None:
    axes.set_facecolor(
        color=_SURFACE,
    )

    for spine in axes.spines.values():
        spine.set_color(
            c=_INK_MUTED,
        )

    axes.tick_params(
        colors=_INK_MUTED,
        labelcolor=_INK_SECONDARY,
    )


def _hairline_grid(
    axes: Axes,
    /,
) -> None:
    axes.spines["top"].set_visible(
        b=False,
    )

    axes.spines["right"].set_visible(
        b=False,
    )

    axes.grid(
        visible=True,
        axis="y",
        color=_GRIDLINE,
        linewidth=0.8,
    )

    axes.set_axisbelow(
        b=True,
    )


def _save(
    figure: Figure,
    path: Path,
    /,
) -> None:
    figure.tight_layout()

    figure.savefig(
        fname=path,
        dpi=_DOTS_PER_INCH,
    )
