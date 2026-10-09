"""
The three scenarios: stage timings of the pipeline, dictionary concurrency
against a replay provider, and throughput of the distributed mode.

The replay provider stands in for the real dictionary because the real one
answers in twenty seconds and fails at will, and an experiment nobody can
repeat is not an experiment.
"""

from asyncio import (
    create_task,
    gather,
    sleep,
)
from collections.abc import (
    AsyncIterator,
    Sequence,
)
from contextlib import (
    asynccontextmanager,
)
from dataclasses import (
    dataclass,
)
from pathlib import (
    Path,
)
from socket import (
    AF_INET,
    SOCK_STREAM,
    socket,
)
from statistics import (
    mean,
    quantiles,
)
from time import (
    perf_counter,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    AsyncClient,
    Timeout,
)
from uvicorn import (
    Config,
    Server,
)

from wordwinnow.application.use_cases.process_an_analysis import (
    Pipeline,
    run_pipeline,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Stage,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.infrastructure.composition import (
    LinguisticAdapters,
    build_linguistic_adapters,
    build_pipeline,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
)
from wordwinnow.infrastructure.dictionary.disabled import (
    DisabledDictionary,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.infrastructure.sources.custom_text import (
    read_custom_text,
)
from wordwinnow.services.bench.replay_provider import (
    build_replay_provider,
)
from wordwinnow.services.bench.results import (
    Measurement,
)
from wordwinnow.services.cli.commands import (
    analyze_remotely,
)
from wordwinnow.services.cli.intake_client import (
    IntakeClient,
    IntakeUnavailableError,
)
from wordwinnow.services.intake.schemas import (
    AnalysisRequest,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    WorkProgress,
)

STAGES_SCENARIO: Final = "stages"

ENRICHMENT_SCENARIO: Final = "enrichment"

THROUGHPUT_SCENARIO: Final = "throughput"

# NOTE:
# A budget high enough never to bind, for the runs that measure concurrency alone.
UNLIMITED_RATE: Final = 1_000_000.0

# NOTE:
# The learner every in-process scenario analyzes for; the profile only decides tiers, which no scenario measures.
PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)


def _free_port() -> int:
    with socket(
        family=AF_INET,
        type=SOCK_STREAM,
    ) as probe:
        probe.bind(
            (
                "127.0.0.1",
                0,
            ),
        )

        return probe.getsockname()[1]


@asynccontextmanager
async def replay_provider(
    *,
    latency_seconds: float,
    not_found_fraction: float,
) -> AsyncIterator[str]:
    """
    Run the replay provider in this process and yield its base URL.
    """

    port = _free_port()

    server = Server(
        config=Config(
            app=build_replay_provider(
                latency_seconds=latency_seconds,
                not_found_fraction=not_found_fraction,
            ),
            port=port,
            log_config=None,
            access_log=False,
        ),
    )

    task = create_task(
        coro=server.serve(),
    )

    while not server.started:
        await sleep(
            delay=0.01,
        )

    try:
        yield f"http://127.0.0.1:{port}"

    finally:
        server.should_exit = True

        await task


async def _provider_requests(
    base_url: str,
    /,
) -> int:
    async with AsyncClient() as client:
        response = await client.get(
            url=f"{base_url}/stats",
        )

    return int(
        response.json()["requests"],
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class StageRun:
    """
    One text's stage timings from one in-process run.
    """

    text: str

    words: int

    items: int

    timings: dict[Stage, float]

    total_seconds: float


def paragraph_prefix(
    *,
    text: str,
    share: float,
) -> str:
    """
    The shortest run of whole paragraphs from the start of a text that holds
    at least `share` of its words.

    Cutting at a paragraph keeps every prefix a text a reader could have
    stopped at, so one text can be timed at several lengths.
    """

    if not 0.0 < share <= 1.0:
        raise ValueError(
            f"a prefix requires a share above 0 and at most 1, got {share}",
        )

    wanted = share * len(
        text.split(),
    )

    kept: list[str] = []

    words = 0

    for paragraph in text.split(
        sep="\n\n",
    ):
        kept.append(
            paragraph,
        )

        words += len(
            paragraph.split(),
        )

        if words >= wanted:
            break

    return "\n\n".join(
        kept,
    )


async def measure_stages(
    *,
    texts: Sequence[Path],
    shares: Sequence[float],
    repetitions: int,
    adapters: LinguisticAdapters,
) -> tuple[Measurement, ...]:
    """
    Run the pipeline without the dictionary over each text, at each share of
    its length, several times, and record every stage's duration.
    """

    pipeline = build_pipeline(
        adapters=adapters,
        dictionary=DisabledDictionary(),
        dictionary_concurrency=1,
    )

    measurements: list[Measurement] = []

    for path in texts:
        document = read_custom_text(
            path=path,
        )

        for text in dict.fromkeys(
            paragraph_prefix(
                text=document.text,
                share=share,
            )
            for share in shares
        ):
            words = len(
                text.split(),
            )

            for repetition in range(
                repetitions,
            ):
                started = perf_counter()

                result = await run_pipeline(
                    text=text,
                    profile=PROFILE,
                    include_dictionary=False,
                    pipeline=pipeline,
                )

                total = perf_counter() - started

                for timing in result.stage_timings:
                    measurements.append(
                        Measurement(
                            scenario=STAGES_SCENARIO,
                            label=timing.stage,
                            text=path.name,
                            parameter="words",
                            value=str(
                                object=words,
                            ),
                            repetition=repetition,
                            metric="stage_seconds",
                            seconds=timing.seconds,
                            count=None,
                        ),
                    )

                measurements.append(
                    Measurement(
                        scenario=STAGES_SCENARIO,
                        label="total",
                        text=path.name,
                        parameter="words",
                        value=str(
                            object=words,
                        ),
                        repetition=repetition,
                        metric="total_seconds",
                        seconds=total,
                        count=len(
                            result.items,
                        ),
                    ),
                )

    return tuple(
        measurements,
    )


async def measure_enrichment(
    *,
    text: Path,
    settings: Settings,
    concurrencies: Sequence[int],
    latency_seconds: float,
    requests_per_second: float,
    warm: bool,
    repetitions: int,
    label: str,
    adapters: LinguisticAdapters,
    progress: WorkProgress = UNWATCHED,
) -> tuple[Measurement, ...]:
    """
    Run the pipeline with the dictionary against the replay provider at each
    concurrency, and record the enrichment stage's duration and the provider
    requests it took.

    With `warm`, one run fills the cache before each measured run, so the
    measured runs show what a repeated text costs.

    `progress` is told of each run before it starts and after it ends, never
    from inside the stage being timed.
    """

    document = read_custom_text(
        path=text,
    )

    measurements: list[Measurement] = []

    progress.begin(
        description="timing the dictionary stage against the replay provider",
        steps=len(
            concurrencies,
        )
        * repetitions,
    )

    async with replay_provider(
        latency_seconds=latency_seconds,
        not_found_fraction=0.1,
    ) as base_url:
        for concurrency in concurrencies:
            for repetition in range(
                repetitions,
            ):
                progress.describe(
                    description=f"timing the dictionary stage with {concurrency} in flight, run {repetition + 1}",
                )

                async with AsyncClient(
                    timeout=Timeout(
                        timeout=settings.dictionary_timeout_seconds,
                    ),
                ) as client:
                    dictionary = CachingDictionary(
                        inner=FreeDictionaryClient(
                            base_url=f"{base_url}/api/v2/entries/en",
                            timeout_seconds=settings.dictionary_timeout_seconds,
                            requests_per_second=requests_per_second,
                            burst=max(
                                settings.dictionary_burst,
                                concurrency,
                            ),
                            client=client,
                        ),
                        found_ttl_seconds=settings.dictionary_found_ttl_seconds,
                        not_found_ttl_seconds=settings.dictionary_not_found_ttl_seconds,
                        cache=InMemoryDictionaryCache(),
                    )

                    pipeline = build_pipeline(
                        adapters=adapters,
                        dictionary=dictionary,
                        dictionary_concurrency=concurrency,
                    )

                    if warm:
                        await _enrichment_seconds(
                            document.text,
                            pipeline,
                        )

                    before = await _provider_requests(
                        base_url,
                    )

                    (
                        seconds,
                        items,
                    ) = await _enrichment_seconds(
                        document.text,
                        pipeline,
                    )

                    requests = (
                        await _provider_requests(
                            base_url,
                        )
                        - before
                    )

                measurements.append(
                    Measurement(
                        scenario=ENRICHMENT_SCENARIO,
                        label=label,
                        text=text.name,
                        parameter="concurrency",
                        value=str(
                            object=concurrency,
                        ),
                        repetition=repetition,
                        metric="enrichment_seconds",
                        seconds=seconds,
                        count=items,
                    ),
                )

                measurements.append(
                    Measurement(
                        scenario=ENRICHMENT_SCENARIO,
                        label=label,
                        text=text.name,
                        parameter="concurrency",
                        value=str(
                            object=concurrency,
                        ),
                        repetition=repetition,
                        metric="provider_requests",
                        seconds=None,
                        count=requests,
                    ),
                )

                progress.advance()

    return tuple(
        measurements,
    )


async def _enrichment_seconds(
    text: str,
    pipeline: Pipeline,
    /,
) -> tuple[float, int]:
    result = await run_pipeline(
        text=text,
        profile=PROFILE,
        include_dictionary=True,
        pipeline=pipeline,
    )

    seconds = next(timing.seconds for timing in result.stage_timings if timing.stage is Stage.DICTIONARY_ENRICHMENT)

    return (
        seconds,
        len(
            result.items,
        ),
    )


async def measure_throughput(
    *,
    text: Path,
    settings: Settings,
    analyses: int,
    label: str,
    include_dictionary: bool,
    timeout_seconds: float,
    progress: WorkProgress = UNWATCHED,
) -> tuple[Measurement, ...]:
    """
    Submit `analyses` copies of one text to the intake service at once and
    wait for all of them.

    The label names what the operator changed outside this process, such as
    the number of worker replicas.

    `progress` counts the analyses as each one finishes.
    """

    document = read_custom_text(
        path=text,
    )

    request = AnalysisRequest(
        text=document.text,
        title=document.title,
        reference=document.reference,
        level=PROFILE.level,
        target_level=PROFILE.target_level,
        include_dictionary=include_dictionary,
    )

    progress.begin(
        description="waiting for the running services to finish every analysis",
        steps=analyses,
    )

    async def analyze_one() -> Analysis:
        analysis = await analyze_remotely(
            request=request,
            settings=settings,
            wait=True,
            timeout_seconds=timeout_seconds,
        )

        progress.advance()

        return analysis

    started = perf_counter()

    completed: Sequence[Analysis] = await gather(
        *(
            analyze_one()
            for _ in range(
                analyses,
            )
        ),
    )

    makespan = perf_counter() - started

    finished = tuple(analysis for analysis in completed if analysis.status is AnalysisStatus.COMPLETED)

    latencies = tuple(
        (value - analysis.requested_at).total_seconds()
        for analysis in finished
        if (value := analysis.finished_at) is not None
    )

    waits = tuple(value for analysis in finished if (value := analysis.queue_wait_seconds) is not None)

    processing = tuple(
        (former_value - latter_value).total_seconds()
        for analysis in finished
        if (former_value := analysis.finished_at) is not None and (latter_value := analysis.started_at) is not None
    )

    def measurement(
        metric: str,
        seconds: float | None,
        count: int | None,
        /,
    ) -> Measurement:
        return Measurement(
            scenario=THROUGHPUT_SCENARIO,
            label=label,
            text=text.name,
            parameter="analyses",
            value=str(
                object=analyses,
            ),
            repetition=0,
            metric=metric,
            seconds=seconds,
            count=count,
        )

    percentiles = (
        quantiles(
            data=latencies,
            n=20,
        )
        if len(
            latencies,
        )
        > 1
        else latencies * 19
    )

    return (
        measurement(
            "makespan_seconds",
            makespan,
            len(
                finished,
            ),
        ),
        measurement(
            "throughput_per_minute",
            None,
            round(
                number=len(
                    finished,
                )
                / makespan
                * 60,
            ),
        ),
        measurement(
            "latency_p50_seconds",
            percentiles[9] if percentiles else None,
            None,
        ),
        measurement(
            "latency_p95_seconds",
            percentiles[18] if percentiles else None,
            None,
        ),
        measurement(
            "queue_wait_mean_seconds",
            mean(
                data=waits,
            )
            if waits
            else None,
            None,
        ),
        measurement(
            "processing_mean_seconds",
            mean(
                data=processing,
            )
            if processing
            else None,
            None,
        ),
    )


async def load_adapters(
    *,
    settings: Settings,
) -> LinguisticAdapters:
    """
    The linguistic stack the in-process scenarios share.
    """

    return build_linguistic_adapters(
        settings=settings,
    )


async def wait_for_intake(
    *,
    settings: Settings,
    timeout_seconds: float,
) -> None:
    """
    Wait until the intake service answers, or give up.
    """

    deadline = perf_counter() + timeout_seconds

    client = IntakeClient(
        base_url=settings.intake_url,
    )

    try:
        while True:
            try:
                await client.list_recent(
                    limit=1,
                )

                return

            except IntakeUnavailableError:
                if perf_counter() >= deadline:
                    raise

                await sleep(
                    delay=1.0,
                )

    finally:
        await client.close()
