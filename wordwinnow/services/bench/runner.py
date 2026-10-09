"""
The bench commands' substance: where results go, which texts are used, and how
the figures are named.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.bench.results import (
    Measurement,
    append_measurements,
    plot_bars,
    plot_medians,
    read_measurements,
)
from wordwinnow.services.bench.scenarios import (
    ENRICHMENT_SCENARIO,
    STAGES_SCENARIO,
    THROUGHPUT_SCENARIO,
    UNLIMITED_RATE,
    load_adapters,
    measure_enrichment,
    measure_stages,
    measure_throughput,
    wait_for_intake,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    WorkProgress,
)

RESULTS_DIR: Final = Path(
    "benchmarks/results",
)

FIGURES_DIR: Final = Path(
    "benchmarks/figures",
)

CORPUS_DIR: Final = Path(
    "data/corpus",
)

SAMPLE_STORY: Final = CORPUS_DIR / "a-scandal-in-bohemia.txt"

# NOTE:
# One story timed at a quarter, a half, and the whole of its length, so a stage's time is read against length alone,
# with the vocabulary and the style held fixed.
STAGE_SHARES: Final = (
    0.25,
    0.5,
    1.0,
)

DEFAULT_CONCURRENCIES: Final = (
    1,
    2,
    4,
    8,
    16,
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Written:
    """
    Where a scenario's rows went, and how many.
    """

    path: Path

    rows: int


def _write(
    measurements: Sequence[Measurement],
    scenario: str,
    /,
) -> Written:
    path = RESULTS_DIR / f"{scenario}.csv"

    append_measurements(
        measurements=measurements,
        path=path,
    )

    return Written(
        path=path,
        rows=len(
            measurements,
        ),
    )


async def run_stages(
    *,
    texts: Sequence[Path],
    settings: Settings,
    repetitions: int,
    series: str,
) -> Written:
    """
    The stage-timing scenario, appended to the results file of its series.

    A series names what was true of the process, such as whether a level model
    was installed.
    """

    adapters = await load_adapters(
        settings=settings,
    )

    return _write(
        await measure_stages(
            texts=texts,
            shares=STAGE_SHARES,
            repetitions=repetitions,
            adapters=adapters,
        ),
        f"{STAGES_SCENARIO}-{series}",
    )


async def run_enrichment(
    *,
    text: Path,
    settings: Settings,
    concurrencies: Sequence[int],
    latency_seconds: float,
    requests_per_second: float | None,
    warm: bool,
    repetitions: int,
    label: str | None,
    progress: WorkProgress = UNWATCHED,
) -> Written:
    """
    The enrichment-concurrency scenario, appended to its results file.
    """

    progress.begin(
        description="loading the reference lists, WordNet, and the level model",
        steps=None,
    )

    adapters = await load_adapters(
        settings=settings,
    )

    series = (
        label
        if label is not None
        else _enrichment_label(
            latency_seconds,
            requests_per_second,
            warm,
        )
    )

    return _write(
        await measure_enrichment(
            text=text,
            settings=settings,
            concurrencies=concurrencies,
            latency_seconds=latency_seconds,
            requests_per_second=requests_per_second if requests_per_second is not None else UNLIMITED_RATE,
            warm=warm,
            repetitions=repetitions,
            label=series,
            adapters=adapters,
            progress=progress,
        ),
        ENRICHMENT_SCENARIO,
    )


def _enrichment_label(
    latency_seconds: float,
    requests_per_second: float | None,
    warm: bool,
    /,
) -> str:
    budget = f"budget {requests_per_second:g}/s" if requests_per_second is not None else "no budget"

    cache = "warm cache" if warm else "cold cache"

    return f"latency {latency_seconds:g}s, {budget}, {cache}"


async def run_throughput(
    *,
    text: Path,
    settings: Settings,
    analyses: int,
    label: str,
    include_dictionary: bool,
    timeout_seconds: float,
    progress: WorkProgress = UNWATCHED,
) -> Written:
    """
    The throughput scenario against the running services, appended to its
    results file.
    """

    progress.begin(
        description="waiting for the intake service to answer",
        steps=None,
    )

    await wait_for_intake(
        settings=settings,
        timeout_seconds=30.0,
    )

    return _write(
        await measure_throughput(
            text=text,
            settings=settings,
            analyses=analyses,
            label=label,
            include_dictionary=include_dictionary,
            timeout_seconds=timeout_seconds,
            progress=progress,
        ),
        THROUGHPUT_SCENARIO,
    )


def plot_all() -> tuple[Path, ...]:
    """
    Draw a figure for every results file that exists.
    """

    drawn: list[Path] = []

    for stages in sorted(
        RESULTS_DIR.glob(
            pattern=f"{STAGES_SCENARIO}-*.csv",
        ),
    ):
        target = FIGURES_DIR / f"{stages.stem}.png"

        series = stages.stem.removeprefix(
            "stages-",
        )

        plot_medians(
            measurements=read_measurements(
                path=stages,
            ),
            metric="stage_seconds",
            title=f"Pipeline stage duration by text length ({series})",
            x_label="words in the text",
            y_label="median seconds",
            logarithmic=True,
            path=target,
        )

        drawn.append(
            target,
        )

    enrichment = RESULTS_DIR / f"{ENRICHMENT_SCENARIO}.csv"

    if enrichment.exists():
        target = FIGURES_DIR / "enrichment.png"

        plot_medians(
            measurements=read_measurements(
                path=enrichment,
            ),
            metric="enrichment_seconds",
            title="Dictionary stage duration by lookup concurrency",
            x_label="concurrent lookups",
            y_label="median seconds",
            logarithmic=True,
            path=target,
        )

        drawn.append(
            target,
        )

    throughput = RESULTS_DIR / f"{THROUGHPUT_SCENARIO}.csv"

    if throughput.exists():
        measurements = read_measurements(
            path=throughput,
        )

        for (
            metric,
            title,
            y_label,
            name,
        ) in (
            (
                "throughput_per_minute",
                "Analyses completed per minute",
                "analyses per minute",
                "throughput.png",
            ),
            (
                "latency_p95_seconds",
                "Analysis latency, 95th percentile",
                "seconds",
                "latency.png",
            ),
            (
                "queue_wait_mean_seconds",
                "Mean queue wait",
                "seconds",
                "queue-wait.png",
            ),
        ):
            target = FIGURES_DIR / name

            plot_bars(
                measurements=measurements,
                metric=metric,
                title=title,
                y_label=y_label,
                path=target,
            )

            drawn.append(
                target,
            )

    return tuple(
        drawn,
    )
