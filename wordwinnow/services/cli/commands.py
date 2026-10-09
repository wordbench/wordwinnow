"""
What the commands do, written so a test can call them without a terminal.

The local flow runs the use cases in this process; the remote flow talks to
the intake service and rebuilds the analysis from its wire schema, so both end
in the same domain objects and the same report.
"""

from asyncio import (
    sleep,
)
from collections.abc import (
    Callable,
)
from contextlib import (
    AsyncExitStack,
    nullcontext,
)
from dataclasses import (
    dataclass,
    replace,
)
from logging import (
    getLogger,
)
from pathlib import (
    Path,
)
from time import (
    monotonic,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from fastapi import (
    FastAPI,
)
from httpx2 import (
    AsyncBaseTransport,
)
from uvicorn import (
    Config,
    Server,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    VocabularySummary,
)
from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
    SourceRejectedError,
)
from wordwinnow.application.use_cases.acquire_a_document import (
    acquire_a_document,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    process_an_analysis,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.application.use_cases.show_an_analysis import (
    list_analyses,
    show_an_analysis,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.composition import (
    configure_process,
    configured_source_names,
    enrichment_profile,
    intake_profile,
    local_profile,
    storage_profile,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    CsvReferenceLexicon,
)
from wordwinnow.infrastructure.lexicon.wordnet_semantics import (
    WordNetLexicalSemantics,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    REQUIRED_RESOURCES,
    download_resources,
    missing_resources,
)
from wordwinnow.infrastructure.linguistics.wordfreq_frequency import (
    WordfreqFrequency,
)
from wordwinnow.infrastructure.ml.model import (
    ModelFileError,
    load,
    save,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.infrastructure.observability.tracing import (
    instrument_app,
)
from wordwinnow.infrastructure.persistence.migrations import (
    upgrade_to_head,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.infrastructure.sources.custom_text import (
    document_from_text,
)
from wordwinnow.services.cli.enrichment_client import (
    PUBLISHED_URL,
    EnrichmentClient,
)
from wordwinnow.services.cli.intake_client import (
    IntakeClient,
)
from wordwinnow.services.enrichment.app import (
    EnrichmentDependencies,
    build_enrichment_app,
    lookup_observer,
    provider_hooks,
    provider_observer,
)
from wordwinnow.services.intake.app import (
    PASTED_TEXT,
    IntakeDependencies,
    build_intake_app,
)
from wordwinnow.services.intake.schemas import (
    AnalysisRequest,
)
from wordwinnow.services.ml.dataset import (
    LabeledExample,
    build_dataset,
)
from wordwinnow.services.ml.experiment import (
    render_summary,
    run_experiment,
    train_shipped_model,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    ActivityUnreadableError,
    AnalysisProgress,
    DictionaryWatch,
    ReadActivity,
    RemoteAnalysisProgress,
    WorkProgress,
    watching,
)

# NOTE:
# How often the remote flow asks whether the analysis is done.
POLL_SECONDS: Final = 1.0

# NOTE:
# Why an operator's view of a local analysis has no dictionary to show, by whether the analysis asks one.
_NOT_ASKED: Final = "not asked: --no-dictionary skips it"

_ANSWERS_LOCALLY: Final = "WordNet answers each word in this process, so nothing waits"

# NOTE:
# What the remote flow says while it waits, by the status the intake service last reported.
_WAITING: Final = MappingProxyType(
    mapping={
        AnalysisStatus.REQUESTED: "waiting for a worker to take the analysis",
        AnalysisStatus.PROCESSING: "a worker is processing the analysis",
    },
)

_logger: Final = getLogger(
    name="wordwinnow.cli",
)


@final
class AnalysisTimedOutError(
    RuntimeError,
):
    """
    Raised when a remote analysis did not finish within the wait.

    Not an error of the analysis: `wordwinnow show` finds it later.
    """


async def analyze_locally(
    *,
    request: AnalysisRequest,
    settings: Settings,
    progress: AnalysisProgress = UNWATCHED,
    watch: DictionaryWatch | None = None,
) -> Analysis:
    """
    Request and process an analysis in this process, telling `progress` how
    far it has come, and `watch`, when there is one, what the dictionary is
    doing meanwhile.
    """

    progress.begin(
        description="loading the reference lists, WordNet, and the level model",
        steps=None,
    )

    async with local_profile(
        settings=settings,
        include_dictionary=request.include_dictionary,
    ) as profile:
        if request.text is not None:
            document = document_from_text(
                text=request.text,
                title=request.title or PASTED_TEXT,
                reference=request.reference or PASTED_TEXT,
            )

        else:
            if request.source is None or request.topic is None:
                raise SourceRejectedError(
                    "an analysis request requires a source with a topic",
                )

            progress.begin(
                description=f"fetching {request.topic!r} from {request.source}",
                steps=None,
            )

            document = await acquire_a_document(
                source=request.source,
                topic=request.topic,
                limit=request.limit,
                sources=profile.sources,
            )

        analysis = await request_an_analysis(
            document=document,
            profile=request.profile(),
            options=request.options(),
            new_unit_of_work=profile.new_unit_of_work,
            publisher=profile.publisher,
            clock=profile.clock,
        )

        async with (
            watching(
                read=_local_activity(
                    profile.activity,
                    request.include_dictionary,
                ),
                watch=watch,
            )
            if watch is not None
            else nullcontext()
        ):
            processed = await process_an_analysis(
                analysis_id=analysis.id,
                pipeline=replace(
                    profile.pipeline,
                    progress=progress,
                ),
                new_unit_of_work=profile.new_unit_of_work,
                publisher=profile.publisher,
                clock=profile.clock,
            )

        return processed.analysis


def _local_activity(
    activity: Callable[[], DictionaryActivity] | None,
    asked: bool,
    /,
) -> ReadActivity:
    async def read() -> DictionaryActivity:
        if activity is None:
            raise ActivityUnreadableError(
                _ANSWERS_LOCALLY if asked else _NOT_ASKED,
            )

        return activity()

    return read


async def analyze_remotely(
    *,
    request: AnalysisRequest,
    settings: Settings,
    wait: bool,
    timeout_seconds: float,
    transport: AsyncBaseTransport | None = None,
    progress: RemoteAnalysisProgress = UNWATCHED,
    watch: DictionaryWatch | None = None,
    enrichment_transport: AsyncBaseTransport | None = None,
) -> Analysis:
    """
    Request an analysis through the intake service and, if asked, wait for it,
    telling `progress` what the service last reported, and `watch`, when there
    is one, what the enrichment service's dictionary is doing meanwhile.
    """

    async with AsyncExitStack() as stack:
        client = IntakeClient(
            base_url=settings.intake_url,
            transport=transport,
        )

        stack.push_async_callback(
            client.close,
        )

        if watch is not None:
            enrichment = EnrichmentClient(
                base_url=settings.enrichment_url or PUBLISHED_URL,
                transport=enrichment_transport,
            )

            stack.push_async_callback(
                enrichment.close,
            )

            await stack.enter_async_context(
                cm=watching(
                    read=enrichment.activity,
                    watch=watch,
                ),
            )

        # NOTE:
        # Intake fetches a source's text before it answers, so a request naming a source says where the wait goes.
        fetching = "" if request.source is None else f", which fetches {request.topic!r} from {request.source}"

        progress.begin(
            description=f"handing the analysis to the intake service{fetching}",
            steps=None,
        )

        accepted = await client.create(
            request=request,
        )

        analysis_id = AnalysisId(
            value=accepted.analysis_id,
        )

        deadline = monotonic() + timeout_seconds

        reported: AnalysisStatus | None = None

        while True:
            watched = await client.watch(
                analysis_id=analysis_id,
            )

            analysis = watched.analysis

            if not wait or analysis.status in {
                AnalysisStatus.COMPLETED,
                AnalysisStatus.FAILED,
            }:
                return analysis

            if monotonic() >= deadline:
                raise AnalysisTimedOutError(
                    f"analysis {analysis_id} is still {analysis.status} after {timeout_seconds:.0f} s",
                )

            # NOTE:
            # A worker's own report, once it has made one, says more than the status does: the stage, the count, and
            # whether the dictionary is answering, as the local mode shows them.
            if watched.progress is not None:
                reported = None

                progress.processing(
                    progress=watched.progress,
                )

            elif analysis.status is not reported:
                reported = analysis.status

                progress.begin(
                    description=_WAITING[analysis.status],
                    steps=None,
                )

            await sleep(
                delay=POLL_SECONDS,
            )


async def show(
    *,
    analysis_id: AnalysisId,
    settings: Settings,
    local: bool,
    transport: AsyncBaseTransport | None = None,
) -> Analysis:
    """
    Read one analysis from the local store or the intake service.
    """

    if local:
        async with storage_profile(
            settings=settings,
        ) as profile:
            return await show_an_analysis(
                analysis_id=analysis_id,
                new_unit_of_work=profile.new_unit_of_work,
            )

    client = IntakeClient(
        base_url=settings.intake_url,
        transport=transport,
    )

    try:
        return await client.get(
            analysis_id=analysis_id,
        )

    finally:
        await client.close()


async def list_recent(
    *,
    settings: Settings,
    local: bool,
    limit: int,
    transport: AsyncBaseTransport | None = None,
) -> tuple[AnalysisSummary, ...]:
    """
    List recent analyses from the local store or the intake service.
    """

    if local:
        async with storage_profile(
            settings=settings,
        ) as profile:
            return await list_analyses(
                limit=limit,
                new_unit_of_work=profile.new_unit_of_work,
            )

    client = IntakeClient(
        base_url=settings.intake_url,
        transport=transport,
    )

    try:
        return await client.list_recent(
            limit=limit,
        )

    finally:
        await client.close()


async def summarize(
    *,
    settings: Settings,
    focus_limit: int,
    transport: AsyncBaseTransport | None = None,
) -> VocabularySummary:
    """
    The cross-analysis summary from the intake service.
    """

    client = IntakeClient(
        base_url=settings.intake_url,
        transport=transport,
    )

    try:
        return await client.summarize(
            focus_limit=focus_limit,
        )

    finally:
        await client.close()


async def requeue(
    *,
    settings: Settings,
    transport: AsyncBaseTransport | None = None,
) -> tuple[AnalysisId, ...]:
    """
    Requeue stale analyses through the intake service.
    """

    client = IntakeClient(
        base_url=settings.intake_url,
        transport=transport,
    )

    try:
        return await client.requeue()

    finally:
        await client.close()


def upgrade_database(
    *,
    settings: Settings,
) -> None:
    """
    Apply the migrations to the configured store.
    """

    upgrade_to_head(
        database_url=settings.database_url,
    )


async def serve_intake(
    *,
    settings: Settings,
) -> None:
    """
    Run the intake service until it is stopped.
    """

    tracing = configure_process(
        settings=settings,
        service_name="wordwinnow-intake",
    )

    async with intake_profile(
        settings=settings,
    ) as profile:
        app = build_intake_app(
            dependencies=IntakeDependencies(
                stale_after_seconds=settings.stale_after_seconds,
                new_unit_of_work=profile.new_unit_of_work,
                publisher=profile.publisher,
                sources=profile.sources,
                facts=profile.facts,
                clock=profile.clock,
                metrics=profile.metrics,
            ),
        )

        await _serve(
            app,
            settings.intake_port,
        )

    if tracing is not None:
        tracing.shutdown()


async def serve_enrichment(
    *,
    settings: Settings,
) -> None:
    """
    Run the enrichment service until it is stopped.
    """

    tracing = configure_process(
        settings=settings,
        service_name="wordwinnow-enrichment",
    )

    metrics = build_metrics()

    async with enrichment_profile(
        settings=settings,
        metrics=metrics,
        on_lookup=lookup_observer(
            metrics=metrics,
        ),
        observer=provider_observer(
            metrics=metrics,
        ),
        provider_hooks=provider_hooks(
            metrics=metrics,
        ),
    ) as profile:
        app = build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=profile.dictionary,
                metrics=profile.metrics,
                activity=profile.activity,
            ),
        )

        await _serve(
            app,
            settings.enrichment_port,
        )

    if tracing is not None:
        tracing.shutdown()


async def _serve(
    app: FastAPI,
    port: int,
    /,
) -> None:
    instrument_app(
        app=app,
    )

    # NOTE:
    # Uvicorn's own logging configuration is disabled so the process keeps the one JSON handler it configured.
    server = Server(
        config=Config(
            app=app,
            host="0.0.0.0",
            port=port,
            log_config=None,
            access_log=False,
        ),
    )

    _logger.info(
        msg="service.listening",
        extra={
            "service": app.title,
            "port": port,
        },
    )

    await server.serve()


def _examples(
    settings: Settings,
    /,
) -> tuple[LabeledExample, ...]:
    return build_dataset(
        reference_lexicon=CsvReferenceLexicon.from_directory(
            directory=settings.reference_lists_dir,
        ),
        frequency=WordfreqFrequency(),
        lexical_semantics=WordNetLexicalSemantics(),
    )


def train_level_model(
    *,
    settings: Settings,
    seed: int,
    model_path: Path,
    progress: WorkProgress = UNWATCHED,
) -> str:
    """
    Fit the shipped model configuration to the reference lists and write it to
    `model_path`, returning one line saying what was written.
    """

    progress.begin(
        description="building the dataset from the reference lists",
        steps=None,
    )

    examples = _examples(
        settings,
    )

    count = len(
        examples,
    )

    progress.begin(
        description=f"training the level model on {count:,} words",
        steps=None,
    )

    estimator = train_shipped_model(
        examples=examples,
        seed=seed,
    )

    save(
        estimator=estimator,
        path=model_path,
    )

    families = "+".join(
        estimator.metadata.families,
    )

    return (
        f"wrote {estimator.metadata.model_kind} on {families}, trained on {estimator.metadata.train_size} words, to "
        f"{model_path}"
    )


def run_level_experiment(
    *,
    settings: Settings,
    seed: int,
    save_model: bool,
    output_dir: Path | None,
    progress: WorkProgress = UNWATCHED,
) -> str:
    """
    Build the dataset from the reference lists, run the experiment, and return
    its summary, writing its outputs and saving the chosen model when asked.
    """

    progress.begin(
        description="building the dataset from the reference lists",
        steps=None,
    )

    examples = _examples(
        settings,
    )

    result = run_experiment(
        examples=examples,
        seed=seed,
        output_dir=output_dir,
        progress=progress,
    )

    if save_model:
        save(
            estimator=result.estimator,
            path=settings.level_model_path,
        )

    return render_summary(
        result=result,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DoctorReport:
    """
    What the installation can do, line by line.
    """

    lines: tuple[str, ...]

    healthy: bool


def doctor(
    *,
    settings: Settings,
    install_nltk_data: bool,
    progress: WorkProgress = UNWATCHED,
) -> DoctorReport:
    """
    Check the NLTK data, the reference lists, WordNet, the level model, and
    the configured sources, and install the NLTK data when asked.
    """

    lines: list[str] = []

    healthy = True

    if install_nltk_data:
        absent = missing_resources()

        progress.begin(
            description="downloading the NLTK packages that are missing",
            steps=len(
                absent,
            ),
        )

        # NOTE:
        # One package at a time, so the count moves as each one arrives; the data path is added once however often it
        # is asked for.
        for resource in absent:
            progress.describe(
                description=f"downloading the NLTK package {resource.package}",
            )

            for downloaded in download_resources(
                resources=(resource,),
                target=settings.nltk_data,
            ):
                lines.append(
                    f"downloaded NLTK package {downloaded.package}",
                )

            progress.advance()

    progress.begin(
        description="checking the NLTK data, the lists, WordNet, and the level model",
        steps=None,
    )

    missing = missing_resources()

    for resource in REQUIRED_RESOURCES:
        present = resource not in missing

        healthy = healthy and present

        lines.append(
            f"{'ok     ' if present else 'MISSING'} NLTK package {resource.package}",
        )

    if missing:
        lines.append(
            "run `wordwinnow doctor --install-nltk-data` to install the missing packages",
        )

    try:
        lexicon = CsvReferenceLexicon.from_directory(
            directory=settings.reference_lists_dir,
        )

        count = len(
            lexicon.entries(),
        )

        lines.append(
            f"ok      reference lists: {count} entries from {settings.reference_lists_dir}",
        )

    except (
        OSError,
        LinguisticResourcesMissingError,
    ) as exception:
        healthy = False

        lines.append(
            f"MISSING reference lists: {exception}",
        )

    if not missing:
        lines.append(
            f"ok      WordNet {WordNetLexicalSemantics().version}",
        )

    try:
        estimator = load(
            path=settings.level_model_path,
        )

    except ModelFileError as exception:
        healthy = False

        lines.append(
            f"BROKEN  level model at {settings.level_model_path}: {exception}; `make model` trains a new one",
        )

    else:
        if estimator is not None:
            families = "+".join(
                estimator.metadata.families,
            )

            lines.append(
                f"ok      level model at {settings.level_model_path}: {estimator.metadata.model_kind} on {families}",
            )

        else:
            lines.append(
                f"none    level model at {settings.level_model_path}; the frequency heuristic is used (`make model`)",
            )

    names = ", ".join(
        configured_source_names(
            settings=settings,
        ),
    )

    lines.append(
        f"ok      sources: {names}",
    )

    if settings.nyt_api_key is None:
        lines.append(
            "        the New York Times APIs need WORDWINNOW_NYT_API_KEY; the feed needs nothing",
        )

    lines.append(
        f"        dictionary: {settings.dictionary}",
    )

    store = _redacted_url(
        settings.database_url,
    )

    lines.append(
        f"        analysis store: {store}",
    )

    lines.append(
        f"        intake service: {settings.intake_url}",
    )

    return DoctorReport(
        lines=tuple(
            lines,
        ),
        healthy=healthy,
    )


def _redacted_url(
    url: str,
    /,
) -> str:
    if "@" not in url:
        return url

    (
        head,
        tail,
    ) = url.rsplit(
        sep="@",
        maxsplit=1,
    )

    scheme = head.split(
        sep="://",
    )[0]

    return f"{scheme}://[REDACTED]@{tail}"
