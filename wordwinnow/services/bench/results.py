"""
Measurements: the rows the scenarios write, and the figures drawn from them.
"""

from collections.abc import (
    Sequence,
)
from csv import (
    DictReader,
    DictWriter,
)
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import (
    Path,
)
from statistics import (
    median,
)
from typing import (
    Final,
    final,
)

FIELDS: Final = (
    "scenario",
    "label",
    "text",
    "parameter",
    "value",
    "repetition",
    "metric",
    "seconds",
    "count",
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Measurement:
    """
    One measured number, with everything needed to reproduce it.

    `parameter` and `value` name the variable the scenario changed; `metric`
    names what was measured, in seconds or as a count.
    """

    scenario: str

    label: str

    text: str

    parameter: str

    value: str

    repetition: int

    metric: str

    seconds: float | None

    count: int | None


def append_measurements(
    *,
    measurements: Sequence[Measurement],
    path: Path,
) -> None:
    """
    Add rows to a CSV file, writing the header when the file is new.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    is_new = not path.exists()

    with path.open(
        mode="a",
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = DictWriter(
            f=handle,
            fieldnames=FIELDS,
        )

        if is_new:
            rows.writeheader()

        for measurement in measurements:
            rows.writerow(
                rowdict=asdict(
                    obj=measurement,
                ),
            )


def read_measurements(
    *,
    path: Path,
) -> tuple[Measurement, ...]:
    """
    Every row of a results file.
    """

    with path.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        return tuple(
            Measurement(
                scenario=row["scenario"],
                label=row["label"],
                text=row["text"],
                parameter=row["parameter"],
                value=row["value"],
                repetition=int(
                    row["repetition"],
                ),
                metric=row["metric"],
                seconds=(
                    float(
                        row["seconds"],
                    )
                    if row["seconds"]
                    else None
                ),
                count=(
                    int(
                        row["count"],
                    )
                    if row["count"]
                    else None
                ),
            )
            for row in DictReader(
                f=handle,
            )
        )


def median_seconds(
    *,
    measurements: Sequence[Measurement],
    metric: str,
) -> dict[tuple[str, str], float]:
    """
    The median of one metric, per label and parameter value.
    """

    groups: dict[tuple[str, str], list[float]] = {}

    for measurement in measurements:
        if measurement.metric != metric or measurement.seconds is None:
            continue

        groups.setdefault(
            (
                measurement.label,
                measurement.value,
            ),
            [],
        ).append(
            measurement.seconds,
        )

    return {
        key: median(
            data=values,
        )
        for (
            key,
            values,
        ) in groups.items()
    }


def _sort_key(
    value: str,
    /,
) -> tuple[int, float | str]:
    try:
        return (
            0,
            float(
                value,
            ),
        )

    except ValueError:
        return (
            1,
            value,
        )


def _axis_value(
    value: str,
    /,
) -> float | str:
    (
        kind,
        parsed,
    ) = _sort_key(
        value,
    )

    return parsed if kind == 0 else value


def plot_medians(
    *,
    measurements: Sequence[Measurement],
    metric: str,
    title: str,
    x_label: str,
    y_label: str,
    logarithmic: bool = False,
    path: Path,
) -> None:
    """
    Draw the median of one metric against the parameter, one line per label,
    to a PNG file.
    """

    # NOTE:
    # matplotlib is a development dependency, so it is imported where the figure is drawn and nowhere else.
    from matplotlib.backends.backend_agg import (
        FigureCanvasAgg,
    )
    from matplotlib.figure import (
        Figure,
    )

    medians = median_seconds(
        measurements=measurements,
        metric=metric,
    )

    labels = sorted(
        {
            label
            for (
                label,
                _,
            ) in medians
        },
    )

    figure = Figure(
        figsize=(
            7,
            4,
        ),
    )

    FigureCanvasAgg(
        figure=figure,
    )

    axes = figure.add_subplot()

    for label in labels:
        points = sorted(
            (
                (
                    value,
                    seconds,
                )
                for (
                    (
                        point_label,
                        value,
                    ),
                    seconds,
                ) in medians.items()
                if point_label == label
            ),
            key=lambda point: _sort_key(
                point[0],
            ),
        )

        axes.plot(
            [
                _axis_value(
                    value,
                )
                for (
                    value,
                    _,
                ) in points
            ],
            [
                seconds
                for (
                    _,
                    seconds,
                ) in points
            ],
            marker="o",
            label=label,
        )

    axes.set_title(
        label=title,
    )

    axes.set_xlabel(
        xlabel=x_label,
    )

    axes.set_ylabel(
        ylabel=y_label,
    )

    if logarithmic:
        axes.set_yscale(
            value="log",
        )

    axes.grid(
        visible=True,
        alpha=0.3,
    )

    if (
        len(
            labels,
        )
        > 1
    ):
        axes.legend()

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    figure.savefig(
        fname=path,
        dpi=120,
        bbox_inches="tight",
    )


def plot_bars(
    *,
    measurements: Sequence[Measurement],
    metric: str,
    title: str,
    y_label: str,
    path: Path,
) -> None:
    """
    Draw one bar per label for a metric measured once per label, to a PNG
    file.

    A count metric is drawn from `count`, a duration from `seconds`.
    """

    from matplotlib.backends.backend_agg import (
        FigureCanvasAgg,
    )
    from matplotlib.figure import (
        Figure,
    )

    values: dict[str, list[float]] = {}

    for measurement in measurements:
        if measurement.metric != metric:
            continue

        number = value if (value := measurement.seconds) is not None else measurement.count

        if number is None:
            continue

        values.setdefault(
            measurement.label,
            [],
        ).append(
            float(
                number,
            ),
        )

    labels = sorted(
        values,
        key=_sort_key,
    )

    figure = Figure(
        figsize=(
            7,
            4,
        ),
    )

    FigureCanvasAgg(
        figure=figure,
    )

    axes = figure.add_subplot()

    axes.bar(
        x=labels,
        height=[
            median(
                data=values[label],
            )
            for label in labels
        ],
    )

    axes.set_title(
        label=title,
    )

    axes.set_ylabel(
        ylabel=y_label,
    )

    axes.grid(
        visible=True,
        axis="y",
        alpha=0.3,
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    figure.savefig(
        fname=path,
        dpi=120,
        bbox_inches="tight",
    )
