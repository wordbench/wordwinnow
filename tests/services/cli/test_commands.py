"""
The commands behind the CLI: the local flow through the real adapters, the
remote flow against the intake app in-process, and the doctor.

The local tests need the NLTK data `make nltk-data` installs.
"""

from asyncio import (
    create_task,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Sequence,
)
from contextlib import (
    asynccontextmanager,
)
from io import (
    StringIO,
)
from logging import (
    INFO,
)
from pathlib import (
    Path,
)
from types import (
    MappingProxyType,
    SimpleNamespace,
)
from typing import (
    Any,
    Final,
    final,
)

from fastapi import (
    FastAPI,
)
from httpx2 import (
    ASGITransport,
    AsyncBaseTransport,
    AsyncClient,
    ConnectError,
    MockTransport,
    Request,
    Response,
)
from pytest import (
    LogCaptureFixture,
    MonkeyPatch,
    fixture,
    mark,
    raises,
)
from rich.console import (
    Console,
)
from uvicorn import (
    Config,
)

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    FakeDictionary,
    InMemoryStorage,
    RecordingPublisher,
)
from tests.fakes.progress import (
    RecordingWatch,
    RecordingWorkProgress,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
    VocabularySummary,
)
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    LinguisticResourcesMissingError,
    SourceNotConfiguredError,
    SourceRejectedError,
)
from wordwinnow.domain.analysis import (
    AnalysisStatus,
    Stage,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelSource,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.study import (
    StudyTier,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    CachingDictionary,
    InMemoryDictionaryCache,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    FreeDictionaryClient,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    REFERENCE_FILES,
)
from wordwinnow.infrastructure.lexicon.reference_sources import (
    REFERENCE_CITATIONS,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    REQUIRED_RESOURCES,
    NltkResource,
)
from wordwinnow.infrastructure.ml.model import (
    SklearnLevelEstimator,
    installed_sklearn_version,
    load,
    metadata_path,
    save,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.cli import (
    commands,
)
from wordwinnow.services.cli.commands import (
    AnalysisTimedOutError,
    AnalysisWaitInterruptedError,
    analyze_locally,
    analyze_remotely,
    doctor,
    list_recent,
    requeue,
    serve_enrichment,
    serve_intake,
    set_up,
    show,
    summarize,
    wait_remotely,
)
from wordwinnow.services.cli.enrichment_client import (
    EnrichmentClient,
)
from wordwinnow.services.cli.intake_client import (
    IntakeRejectedError,
    IntakeUnavailableError,
)
from wordwinnow.services.cli.progress import (
    TerminalProgress,
)
from wordwinnow.services.cli.report import (
    render_listing,
)
from wordwinnow.services.enrichment.app import (
    EnrichmentDependencies,
    build_enrichment_app,
)
from wordwinnow.services.intake.app import (
    IntakeDependencies,
    build_intake_app,
)
from wordwinnow.services.intake.schemas import (
    AnalysisRequest,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    ActivityUnreadableError,
    WorkProgress,
)

_TEXT: Final = (
    "The King wanted the photograph back. The groom looked very drunken, but Irene Adler kept the photograph."
)

# NOTE:
# The sentence that every command's no-connection message carries, as the decided text writes it; the commands differ
# in what comes before it and what follows.
_NEEDS: Final = (
    "needs the distributed mode's services, started from a clone of github.com/wordbench/wordwinnow as its "
    "getting-started guide describes and reached at WORDWINNOW_INTAKE_URL"
)


@final
class EmptyFactQuery:
    """
    A fact store with nothing in it.
    """

    async def summarize(
        self,
        *,
        focus_limit: int,
    ) -> VocabularySummary:
        return VocabularySummary(
            analysis_count=0,
            fact_count=0,
            levels=(),
            focus_lemmas=(),
        )


# NOTE:
# Every parameter is an optional override of one canonical request, so they are named rather than positional.
def _request(
    *,
    text: str = _TEXT,
    title: str = "a-scandal-in-bohemia",
    reference: str = "a-scandal-in-bohemia.txt",
    include_dictionary: bool = False,
) -> AnalysisRequest:
    return AnalysisRequest(
        text=text,
        title=title,
        reference=reference,
        level=CefrLevel.A2,
        known_lemmas=[
            "photograph",
        ],
        include_dictionary=include_dictionary,
    )


def _refused(
    request: Request,
    /,
) -> Response:
    """
    The provider's answer when nothing is meant to ask it.
    """

    return Response(
        status_code=503,
    )


def _enrichment(
    *,
    watched: bool,
) -> ASGITransport:
    """
    An enrichment service in this process, whose dictionary asks a provider
    and says what it is doing when `watched`, and otherwise answers locally
    and says nothing.
    """

    if not watched:
        return ASGITransport(
            app=build_enrichment_app(
                dependencies=EnrichmentDependencies(
                    dictionary=FakeDictionary(),
                    metrics=build_metrics(),
                ),
            ),
        )

    client = FreeDictionaryClient(
        base_url="https://provider.test/api/v2/entries/en",
        timeout_seconds=5.0,
        requests_per_second=2.0,
        burst=4,
        client=AsyncClient(
            transport=MockTransport(
                handler=_refused,
            ),
        ),
    )

    dictionary = CachingDictionary(
        inner=client,
        found_ttl_seconds=60,
        not_found_ttl_seconds=60,
        cache=InMemoryDictionaryCache(),
    )

    def activity() -> DictionaryActivity:
        return DictionaryActivity(
            counts=dictionary.counts(),
            provider=client.activity(),
        )

    return ASGITransport(
        app=build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=dictionary,
                metrics=build_metrics(),
                activity=activity,
            ),
        ),
    )


@fixture
def settings(
    *,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Settings:
    monkeypatch.chdir(
        path=tmp_path,
    )

    monkeypatch.setenv(
        name="WORDWINNOW_DATABASE_URL",
        value=f"sqlite+aiosqlite:///{tmp_path / 'wordwinnow.db'}",
    )

    monkeypatch.setenv(
        name="WORDWINNOW_REFERENCE_LISTS_DIR",
        value=str(
            object=Path(
                __file__,
            ).parents[3]
            / "data/reference",
        ),
    )

    monkeypatch.setenv(
        name="WORDWINNOW_LEVEL_MODEL_PATH",
        value=str(
            object=tmp_path / "nonexistent.joblib",
        ),
    )

    return Settings()


@final
class TestLocalFlow:
    async def test_a_text_is_analyzed_stored_and_readable_on_a_fresh_store(
        self,
        *,
        settings: Settings,
    ) -> None:
        analysis = await analyze_locally(
            request=_request(),
            settings=settings,
        )

        assert analysis.status is AnalysisStatus.COMPLETED

        assert analysis.document.title == "a-scandal-in-bohemia"

        assert analysis.document.origin is DocumentOrigin.CUSTOM_TEXT

        assert analysis.profile.target_level is CefrLevel.B1

        by_lemma = MappingProxyType(
            mapping={study_item.item.lemma: study_item for study_item in analysis.items},
        )

        assert by_lemma["photograph"].tier is StudyTier.KNOWN

        assert by_lemma["photograph"].item.occurrence_count == 2

        assert by_lemma["drunken"].level.level is CefrLevel.B2

        assert by_lemma["drunken"].level.source is LevelSource.FREQUENCY_HEURISTIC

        assert by_lemma["drunken"].senses[0].category == "adj.all"

        assert "adler" not in by_lemma

        assert all(study_item.dictionary is None for study_item in analysis.items)

        watched = await show(
            analysis_id=analysis.id,
            settings=settings,
            local=True,
        )

        stored = watched.analysis

        assert watched.progress is None

        assert stored == analysis

        assert stored.items == analysis.items

        summaries = await list_recent(
            settings=settings,
            local=True,
            limit=5,
        )

        assert tuple(summary.analysis_id for summary in summaries) == (analysis.id,)

        console = Console(
            width=80,
            record=True,
        )

        render_listing(
            summaries=summaries,
            console=console,
        )

        listed = console.export_text()

        assert "a-scandal-in-bohemia" in listed

        assert (
            str(
                object=analysis.id,
            )
            in listed
        )

    async def test_a_watch_hears_why_a_local_analysis_has_no_dictionary_to_show(
        self,
        *,
        settings: Settings,
        monkeypatch: MonkeyPatch,
    ) -> None:
        skipped = RecordingWatch()

        await analyze_locally(
            request=_request(),
            settings=settings,
            watch=skipped,
        )

        monkeypatch.setenv(
            name="WORDWINNOW_DICTIONARY",
            value="wordnet",
        )

        answered_here = RecordingWatch()

        await analyze_locally(
            request=_request(
                include_dictionary=True,
            ),
            settings=Settings(),
            watch=answered_here,
        )

        assert "not asked: --no-dictionary skips it" in skipped.heard

        assert "WordNet answers each word in this process, so nothing waits" in answered_here.heard

    async def test_a_topic_without_a_configured_source_is_refused(
        self,
        *,
        settings: Settings,
    ) -> None:
        with raises(
            expected_exception=SourceNotConfiguredError,
        ):
            await analyze_locally(
                request=AnalysisRequest(
                    source="nyt-top-stories",
                    topic="science",
                    level=CefrLevel.B1,
                    include_dictionary=False,
                ),
                settings=settings,
            )

    async def test_an_unknown_id_is_not_found(
        self,
        *,
        settings: Settings,
    ) -> None:
        analysis_id = AnalysisId.new()

        with raises(
            expected_exception=AnalysisNotFoundError,
            match=f"^no analysis {analysis_id} in the local store; --remote looks for it in the distributed mode$",
        ):
            await show(
                analysis_id=analysis_id,
                settings=settings,
                local=True,
            )


@fixture
async def intake() -> AsyncIterator[tuple[InMemoryStorage, ASGITransport]]:
    storage = InMemoryStorage()

    app = build_intake_app(
        dependencies=IntakeDependencies(
            stale_after_seconds=60,
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            sources={},
            facts=EmptyFactQuery(),
            clock=FakeClock(
                at=EPOCH,
            ),
            metrics=build_metrics(),
        ),
    )

    transport = ASGITransport(
        app=app,
    )

    try:
        yield (
            storage,
            transport,
        )

    finally:
        await transport.aclose()


async def _asked(
    command: str,
    settings: Settings,
    transport: AsyncBaseTransport,
    /,
) -> None:
    """
    Run `command`, as the command line names it, against `transport`.
    """

    match command:
        case "analyze":
            await analyze_remotely(
                request=_request(),
                settings=settings,
                wait=False,
                timeout_seconds=5,
                transport=transport,
            )

        case "show":
            await show(
                analysis_id=AnalysisId.new(),
                settings=settings,
                local=False,
                transport=transport,
            )

        case "show --wait":
            await wait_remotely(
                analysis_id=AnalysisId.new(),
                settings=settings,
                timeout_seconds=5,
                transport=transport,
            )

        case "list":
            await list_recent(
                settings=settings,
                local=False,
                limit=5,
                transport=transport,
            )

        case "stats":
            await summarize(
                settings=settings,
                focus_limit=5,
                transport=transport,
            )

        case "admin requeue":
            await requeue(
                settings=settings,
                transport=transport,
            )

        case _:
            raise ValueError(
                f"no such command: {command}",
            )


@final
class TestRemoteFlow:
    async def test_without_waiting_the_request_is_accepted(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            storage,
            transport,
        ) = intake

        analysis = await analyze_remotely(
            request=_request(),
            settings=settings,
            wait=False,
            timeout_seconds=5,
            transport=transport,
        )

        assert analysis.status is AnalysisStatus.REQUESTED

        assert analysis.id in storage.analyses

    async def test_waiting_for_a_result_nobody_produces_times_out(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            _,
            transport,
        ) = intake

        with raises(
            expected_exception=AnalysisTimedOutError,
        ):
            await analyze_remotely(
                request=_request(),
                settings=settings,
                wait=True,
                timeout_seconds=0.5,
                transport=transport,
            )

    async def test_a_wait_that_ends_leaves_the_analysis_going_and_says_how_to_wait_again(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            storage,
            transport,
        ) = intake

        with raises(
            expected_exception=AnalysisTimedOutError,
        ) as caught:
            await analyze_remotely(
                request=_request(),
                settings=settings,
                wait=True,
                timeout_seconds=0.5,
                transport=transport,
            )

        (analysis_id,) = storage.analyses

        assert "goes on without this command" in str(
            object=caught.value,
        )

        assert (
            f"`wordwinnow show {analysis_id} --remote --wait` waits for it again, and "
            f"`wordwinnow show {analysis_id} --remote` shows how far it has come"
        ) in str(
            object=caught.value,
        )

        assert storage.analyses[analysis_id].status is AnalysisStatus.REQUESTED

    async def test_waiting_again_takes_up_the_same_analysis_and_requests_nothing(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            storage,
            transport,
        ) = intake

        with raises(
            expected_exception=AnalysisTimedOutError,
        ):
            await analyze_remotely(
                request=_request(),
                settings=settings,
                wait=True,
                timeout_seconds=0.5,
                transport=transport,
            )

        (analysis_id,) = storage.analyses

        # NOTE:
        # A worker that takes the analysis up only after the first command stopped waiting, and completes it while the
        # second one waits.
        async def work() -> None:
            await asyncio_sleep(
                delay=0.2,
            )

            async with storage.new_unit_of_work() as uow:
                analysis = await uow.analyses.get(
                    analysis_id=analysis_id,
                )

                assert analysis is not None

                analysis.start(
                    at=EPOCH,
                )

                await uow.analyses.save(
                    analysis=analysis,
                )

                await uow.commit()

            async with storage.new_unit_of_work() as uow:
                analysis = await uow.analyses.get(
                    analysis_id=analysis_id,
                )

                assert analysis is not None

                analysis.complete(
                    at=EPOCH,
                    items=(),
                    stage_timings=(),
                )

                await uow.analyses.save(
                    analysis=analysis,
                )

                await uow.commit()

        working = create_task(
            coro=work(),
        )

        analysis = await wait_remotely(
            analysis_id=analysis_id,
            settings=settings,
            timeout_seconds=5,
            transport=transport,
        )

        await working

        assert analysis.id == analysis_id

        assert analysis.status is AnalysisStatus.COMPLETED

        assert tuple(
            storage.analyses,
        ) == (analysis_id,)

    async def test_an_interrupted_wait_names_the_analysis_and_how_to_wait_again(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            storage,
            transport,
        ) = intake

        waiting = create_task(
            coro=analyze_remotely(
                request=_request(),
                settings=settings,
                wait=True,
                timeout_seconds=60,
                transport=transport,
            ),
        )

        # NOTE:
        # Ctrl-C reaches a command as the cancellation of its task, here once the analysis is requested.
        while not storage.analyses:
            await asyncio_sleep(
                delay=0.01,
            )

        waiting.cancel()

        with raises(
            expected_exception=AnalysisWaitInterruptedError,
        ) as caught:
            await waiting

        (analysis_id,) = storage.analyses

        assert f"stopped waiting for analysis {analysis_id}, which goes on" in str(
            object=caught.value,
        )

        assert (
            f"`wordwinnow show {analysis_id} --remote --wait` waits for it again, and "
            f"`wordwinnow show {analysis_id} --remote` shows how far it has come"
        ) in str(
            object=caught.value,
        )

        assert storage.analyses[analysis_id].status is AnalysisStatus.REQUESTED

    async def test_showing_an_analysis_in_progress_brings_its_workers_report(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            storage,
            transport,
        ) = intake

        await analyze_remotely(
            request=_request(),
            settings=settings,
            wait=False,
            timeout_seconds=5,
            transport=transport,
        )

        (analysis_id,) = storage.analyses

        async with storage.new_unit_of_work() as uow:
            analysis = await uow.analyses.get(
                analysis_id=analysis_id,
            )

            assert analysis is not None

            analysis.start(
                at=EPOCH,
            )

            await uow.analyses.save(
                analysis=analysis,
            )

            await uow.analyses.record_progress(
                analysis_id=analysis_id,
                progress=ProcessingProgress(
                    stage=Stage.DICTIONARY_ENRICHMENT,
                    steps=2,
                    done=1,
                    unavailable=0,
                    dictionary_paused=False,
                    reported_at=EPOCH,
                ),
            )

            await uow.commit()

        watched = await show(
            analysis_id=analysis_id,
            settings=settings,
            local=False,
            transport=transport,
        )

        assert watched.analysis.status is AnalysisStatus.PROCESSING

        assert watched.progress is not None

        assert watched.progress.done == 1

    async def test_while_it_waits_progress_says_what_the_service_last_reported(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            _,
            transport,
        ) = intake

        progress = RecordingWorkProgress()

        with raises(
            expected_exception=AnalysisTimedOutError,
        ):
            await analyze_remotely(
                request=_request(),
                settings=settings,
                wait=True,
                timeout_seconds=1.5,
                transport=transport,
                progress=progress,
            )

        # NOTE:
        # Nothing processes the analysis here, so it stays requested through every poll, and a status reported again
        # is not a new phase.
        assert progress.events == [
            "handing the analysis to the intake service",
            "waiting for a worker to take the analysis",
        ]

    async def test_once_a_worker_reports_progress_says_how_far_it_has_come(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            storage,
            transport,
        ) = intake

        progress = RecordingWorkProgress()

        # NOTE:
        # A worker that takes the analysis once the command has seen it requested, and reports at once, so the next
        # poll finds the report.
        async def work() -> None:
            while "waiting for a worker to take the analysis" not in progress.events:
                await asyncio_sleep(
                    delay=0.01,
                )

            (analysis_id,) = storage.analyses

            async with storage.new_unit_of_work() as uow:
                analysis = await uow.analyses.get(
                    analysis_id=analysis_id,
                )

                assert analysis is not None

                analysis.start(
                    at=EPOCH,
                )

                await uow.analyses.save(
                    analysis=analysis,
                )

                await uow.analyses.record_progress(
                    analysis_id=analysis_id,
                    progress=ProcessingProgress(
                        stage=Stage.DICTIONARY_ENRICHMENT,
                        steps=156,
                        done=63,
                        unavailable=20,
                        dictionary_paused=False,
                        reported_at=EPOCH,
                    ),
                )

                await uow.commit()

        working = create_task(
            coro=work(),
        )

        with raises(
            expected_exception=AnalysisTimedOutError,
        ):
            await analyze_remotely(
                request=_request(),
                settings=settings,
                wait=True,
                timeout_seconds=1.5,
                transport=transport,
                progress=progress,
            )

        await working

        assert progress.events == [
            "handing the analysis to the intake service",
            "waiting for a worker to take the analysis",
            "dictionary_enrichment: 63 of 156, 20 unavailable",
        ]

    async def test_a_watch_hears_what_the_enrichment_service_says_or_why_it_cannot(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
        monkeypatch: MonkeyPatch,
    ) -> None:
        (
            _,
            transport,
        ) = intake

        # NOTE:
        # The last run has no service in this process and reads the setting, which names a port nothing listens on.
        monkeypatch.setenv(
            name="WORDWINNOW_ENRICHMENT_URL",
            value="http://127.0.0.1:9",
        )

        watches = (
            RecordingWatch(),
            RecordingWatch(),
            RecordingWatch(),
        )

        # NOTE:
        # Nothing processes the analysis, so each run waits out its timeout while its watch hears what the enrichment
        # service says, or why it says nothing.
        for (
            watch,
            enrichment,
        ) in zip(
            watches,
            (
                _enrichment(
                    watched=True,
                ),
                _enrichment(
                    watched=False,
                ),
                None,
            ),
            strict=True,
        ):
            with raises(
                expected_exception=AnalysisTimedOutError,
            ):
                await analyze_remotely(
                    request=_request(),
                    settings=Settings() if enrichment is None else settings,
                    wait=True,
                    timeout_seconds=0.5,
                    transport=transport,
                    watch=watch,
                    enrichment_transport=enrichment,
                )

        (
            answering,
            local,
            unreachable,
        ) = watches

        assert "circuit closed" in answering.heard

        assert "the enrichment service's dictionary asks no provider, so there is nothing to show" in local.heard

        assert "the enrichment service does not answer at http://127.0.0.1:9" in unreachable.heard

    async def test_a_refusal_comes_back_as_the_error_the_service_named(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            _,
            transport,
        ) = intake

        with raises(
            expected_exception=SourceNotConfiguredError,
        ):
            await analyze_remotely(
                request=AnalysisRequest(
                    source="nyt-top-stories",
                    topic="science",
                    level=CefrLevel.B1,
                    include_dictionary=False,
                ),
                settings=settings,
                wait=False,
                timeout_seconds=5,
                transport=transport,
            )

        with raises(
            expected_exception=SourceRejectedError,
        ):
            await analyze_remotely(
                request=_request(
                    text="   ",
                ),
                settings=settings,
                wait=False,
                timeout_seconds=5,
                transport=transport,
            )

    async def test_the_listing_and_the_requeue_go_through_the_service(
        self,
        *,
        settings: Settings,
        intake: tuple[InMemoryStorage, ASGITransport],
    ) -> None:
        (
            _,
            transport,
        ) = intake

        await analyze_remotely(
            request=_request(),
            settings=settings,
            wait=False,
            timeout_seconds=5,
            transport=transport,
        )

        summaries = await list_recent(
            settings=settings,
            local=False,
            limit=5,
            transport=transport,
        )

        assert (
            len(
                summaries,
            )
            == 1
        )

        assert (
            await requeue(
                settings=settings,
                transport=transport,
            )
            == ()
        )

    async def test_an_unreachable_intake_is_reported_as_such(
        self,
        *,
        settings: Settings,
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_INTAKE_URL",
            value="http://127.0.0.1:9",
        )

        # NOTE:
        # A refused connection is what a learner meets when a command asked for the distributed mode with --remote and
        # its services are not running, so the error names the address it tried and says what the command does without
        # it.
        with raises(
            expected_exception=IntakeUnavailableError,
        ) as caught:
            await list_recent(
                settings=Settings(),
                local=False,
                limit=5,
            )

        message = str(
            object=caught.value,
        )

        assert message.startswith(
            "no intake service answered at http://127.0.0.1:9 (",
        )

        assert message.endswith(
            "; without --remote, list shows the analyses stored on this machine",
        )

        assert "make" not in message

        assert "Without --remote, analyze, show, and list" not in message

    @mark.parametrize(
        argnames=(
            "command",
            "ending",
            "names_remote",
        ),
        argvalues=(
            (
                "analyze",
                f"--remote {_NEEDS}; to analyze on this machine instead, run the command without --remote",
                True,
            ),
            (
                "show",
                f"--remote {_NEEDS}; without --remote, show reads the analyses stored on this machine",
                True,
            ),
            (
                "show --wait",
                f"--remote {_NEEDS}; without --remote, show reads the analyses stored on this machine",
                True,
            ),
            (
                "list",
                f"--remote {_NEEDS}; without --remote, list shows the analyses stored on this machine",
                True,
            ),
            (
                "stats",
                f"stats {_NEEDS}",
                False,
            ),
            (
                "admin requeue",
                f"admin requeue {_NEEDS}",
                False,
            ),
        ),
    )
    async def test_each_command_ends_no_connection_with_its_own_sentence(
        self,
        *,
        settings: Settings,
        command: str,
        ending: str,
        names_remote: bool,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            raise ConnectError(
                message="foo",
            )

        with raises(
            expected_exception=IntakeUnavailableError,
        ) as caught:
            await _asked(
                command,
                settings,
                MockTransport(
                    handler=handler,
                ),
            )

        message = str(
            object=caught.value,
        )

        assert message == f"no intake service answered at http://127.0.0.1:8010 (foo); {ending}"

        assert ("--remote" in message) is names_remote


@final
class TestDoctor:
    def test_the_report_names_every_resource_and_no_secret(
        self,
        *,
        settings: Settings,
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_DATABASE_URL",
            value="postgresql+asyncpg://wordwinnow:hidden@db.example.com:5432/wordwinnow",
        )

        report = doctor(
            settings=Settings(),
            install_nltk_data=False,
        )

        text = "\n".join(
            report.lines,
        )

        assert report.healthy

        assert "NLTK package wordnet" in text

        assert "reference lists: 9" in text

        assert "WordNet 3.0" in text

        assert "frequency heuristic" in text

        assert "hidden" not in text

        assert "[REDACTED]@db.example.com" in text

    def test_missing_packages_are_downloaded_and_counted_one_at_a_time(
        self,
        *,
        settings: Settings,
        monkeypatch: MonkeyPatch,
    ) -> None:
        absent = list(
            REQUIRED_RESOURCES[:2],
        )

        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return tuple(resource for resource in resources if resource in absent)

        def download_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
            target: Path | None = None,
            client: object = None,
        ) -> tuple[NltkResource, ...]:
            fetched = missing_resources(
                resources=resources,
            )

            for resource in fetched:
                absent.remove(
                    resource,
                )

            return fetched

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="download_resources",
            value=download_resources,
        )

        progress = RecordingWorkProgress()

        report = doctor(
            settings=settings,
            install_nltk_data=True,
            progress=progress,
        )

        assert report.healthy

        assert progress.events == [
            "downloading the NLTK packages that are missing of 2",
            "downloading the NLTK package punkt_tab",
            "step",
            "downloading the NLTK package averaged_perceptron_tagger_eng",
            "step",
            "checking the NLTK data, the lists, WordNet, and the level model",
        ]

        assert report.lines[:2] == (
            "downloaded NLTK package punkt_tab",
            "downloaded NLTK package averaged_perceptron_tagger_eng",
        )

    def test_the_report_names_the_command_that_sets_up_in_every_remedy_and_never_make(
        self,
        *,
        settings: Settings,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        missing = REQUIRED_RESOURCES[:1]

        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return tuple(resource for resource in resources if resource in missing)

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        settings.level_model_path.write_bytes(
            data=b"foo",
        )

        monkeypatch.setenv(
            name="WORDWINNOW_REFERENCE_LISTS_DIR",
            value=str(
                object=tmp_path / "nonexistent",
            ),
        )

        report = doctor(
            settings=Settings(),
            install_nltk_data=False,
        )

        assert "run `wordwinnow setup` to install the missing packages" in report.lines

        assert any(
            line.startswith(
                "MISSING reference lists:",
            )
            and line.endswith(
                "run `wordwinnow setup`, or set WORDWINNOW_REFERENCE_LISTS_DIR",
            )
            for line in report.lines
        )

        assert any(
            line.startswith(
                "BROKEN  level model",
            )
            and line.endswith(
                "; `wordwinnow setup` trains a new one",
            )
            for line in report.lines
        )

        assert not any("make " in line for line in report.lines)

    def test_without_a_model_the_heuristic_is_named_with_the_command_that_trains_one(
        self,
        *,
        settings: Settings,
    ) -> None:
        report = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        assert report.healthy

        assert (
            f"none    level model at {settings.level_model_path}; the frequency heuristic is used "
            "(`wordwinnow setup`)" in report.lines
        )

        assert not any("make " in line for line in report.lines)

    def test_a_model_trained_with_the_installed_scikit_learn_is_ok(
        self,
        *,
        settings: Settings,
        fitted_model: SklearnLevelEstimator,
    ) -> None:
        save(
            estimator=fitted_model,
            path=settings.level_model_path,
        )

        report = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        assert report.healthy

        assert (
            f"ok      level model at {settings.level_model_path}: gradient_boosting on frequency+lexical+wordnet"
            in report.lines
        )

    def test_a_model_trained_with_another_scikit_learn_is_stale_and_makes_the_report_unhealthy(
        self,
        *,
        settings: Settings,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        save(
            estimator=stale_model,
            path=settings.level_model_path,
        )

        report = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        assert not report.healthy

        assert (
            f"STALE   level model at {settings.level_model_path}: trained with scikit-learn 0.0.0, and "
            f"{installed_sklearn_version()} is installed; `wordwinnow setup` trains a new one"
        ) in report.lines

        assert not any(
            line.startswith(
                "ok      level model",
            )
            for line in report.lines
        )

    @mark.parametrize(
        argnames=(
            "state",
            "prefix",
            "healthy",
        ),
        argvalues=(
            (
                "absent",
                "none    level model",
                True,
            ),
            (
                "current",
                "ok      level model",
                True,
            ),
            (
                "without a sidecar",
                "BROKEN  level model",
                False,
            ),
            (
                "a sidecar that is not JSON",
                "BROKEN  level model",
                False,
            ),
            (
                "cut short",
                "BROKEN  level model",
                False,
            ),
            (
                "another scikit-learn",
                "STALE   level model",
                False,
            ),
            (
                "another scikit-learn and unreadable",
                "STALE   level model",
                False,
            ),
        ),
    )
    def test_every_state_of_the_model_file_is_named_without_an_exception(
        self,
        *,
        settings: Settings,
        put_model: Callable[..., None],
        state: str,
        prefix: str,
        healthy: bool,
    ) -> None:
        put_model(
            path=settings.level_model_path,
            state=state,
        )

        report = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        assert report.healthy is healthy

        assert (
            sum(
                1
                for line in report.lines
                if "level model" in line
                and line.startswith(
                    prefix,
                )
            )
            == 1
        )

        assert not any(
            "level model" in line
            and not line.startswith(
                prefix,
            )
            for line in report.lines
        )

    def test_a_pickle_this_scikit_learn_cannot_read_is_never_opened_when_the_sidecar_names_another_version(
        self,
        *,
        settings: Settings,
        stale_model: SklearnLevelEstimator,
        unreadable_pickle: bytes,
    ) -> None:
        save(
            estimator=stale_model,
            path=settings.level_model_path,
        )

        settings.level_model_path.write_bytes(
            data=unreadable_pickle,
        )

        with raises(
            expected_exception=ModuleNotFoundError,
        ):
            load(
                path=settings.level_model_path,
            )

        report = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        assert not report.healthy

        assert (
            f"STALE   level model at {settings.level_model_path}: trained with scikit-learn 0.0.0, and "
            f"{installed_sklearn_version()} is installed; `wordwinnow setup` trains a new one"
        ) in report.lines

    def test_a_damaged_sidecar_and_a_cut_short_model_say_what_is_wrong_in_one_line_that_names_the_remedy(
        self,
        *,
        settings: Settings,
        put_model: Callable[..., None],
    ) -> None:
        sidecar = metadata_path(
            path=settings.level_model_path,
        )

        put_model(
            path=settings.level_model_path,
            state="a sidecar that is not JSON",
        )

        damaged_sidecar = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        assert (
            f"BROKEN  level model at {settings.level_model_path}: the metadata sidecar {sidecar} does not hold a "
            "model's metadata; `wordwinnow setup` trains a new one"
        ) in damaged_sidecar.lines

        put_model(
            path=settings.level_model_path,
            state="cut short",
        )

        cut_short = tuple(
            line
            for line in doctor(
                settings=settings,
                install_nltk_data=False,
            ).lines
            if line.startswith(
                "BROKEN  level model",
            )
        )

        assert (
            len(
                cut_short,
            )
            == 1
        )

        assert cut_short[0].startswith(
            f"BROKEN  level model at {settings.level_model_path}: the model at {settings.level_model_path} cannot be "
            "read (",
        )

        assert cut_short[0].endswith(
            "); `wordwinnow setup` trains a new one",
        )

    def test_an_earlier_store_in_the_current_directory_is_named_after_the_store_in_use_and_never_moved(
        self,
        *,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        before = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        earlier = tmp_path / "data" / "local" / "wordwinnow.db"

        earlier.parent.mkdir(
            parents=True,
        )

        earlier.write_bytes(
            data=b"foo",
        )

        after = doctor(
            settings=settings,
            install_nltk_data=False,
        )

        position = after.lines.index(
            f"        analysis store: {settings.database_url}",
        )

        assert after.lines[position + 1] == (
            "        an earlier store is at data/local/wordwinnow.db; "
            "WORDWINNOW_DATABASE_URL=sqlite+aiosqlite:///data/local/wordwinnow.db keeps using it"
        )

        assert after.healthy == before.healthy

        assert earlier.read_bytes() == b"foo"

        assert not any("an earlier store" in line for line in before.lines)

    def test_the_earlier_store_is_not_named_when_the_configured_store_is_that_one(
        self,
        *,
        settings: Settings,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "data" / "local").mkdir(
            parents=True,
        )

        (tmp_path / "data" / "local" / "wordwinnow.db").write_bytes(
            data=b"foo",
        )

        monkeypatch.setenv(
            name="WORDWINNOW_DATABASE_URL",
            value="sqlite+aiosqlite:///data/local/wordwinnow.db",
        )

        report = doctor(
            settings=Settings(),
            install_nltk_data=False,
        )

        assert not any("an earlier store" in line for line in report.lines)


@fixture
def unprepared(
    *,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Settings:
    monkeypatch.chdir(
        path=tmp_path,
    )

    monkeypatch.setenv(
        name="WORDWINNOW_REFERENCE_LISTS_DIR",
        value=str(
            object=tmp_path / "reference",
        ),
    )

    monkeypatch.setenv(
        name="WORDWINNOW_LEVEL_MODEL_PATH",
        value=str(
            object=tmp_path / "model.joblib",
        ),
    )

    return Settings()


# TEST:
# Training takes seconds, and what these tests ask is whether `set_up` trains, so a recording stands in for it; the
# real training runs once, through the command line.
@fixture
def trained(
    *,
    monkeypatch: MonkeyPatch,
) -> list[tuple[Path, int]]:
    calls: list[tuple[Path, int]] = []

    def train_level_model(
        *,
        settings: Settings,
        seed: int,
        model_path: Path,
        progress: WorkProgress = UNWATCHED,
    ) -> str:
        calls.append(
            (
                model_path,
                seed,
            ),
        )

        return f"wrote a model to {model_path}"

    monkeypatch.setattr(
        target=commands,
        name="train_level_model",
        value=train_level_model,
    )

    return calls


def _ready(
    reference_lists: Path,
    level_model: Path,
    /,
) -> tuple[str, ...]:
    """
    What `setup` prints once everything is ready, before the citations.
    """

    return (
        "ready   NLTK data: punkt_tab, averaged_perceptron_tagger_eng, stopwords, wordnet",
        f"        reference lists: {reference_lists}",
        f"        level model: {level_model}",
        "        next: wordwinnow analyze FILE --level LEVEL",
    )


@final
class _PhaseTotals:
    """
    A terminal's progress line that notes, as each phase begins, the total its
    bar is drawn against: a number for a counted phase, or `None`, which Rich
    draws as a pulse.
    """

    def __init__(
        self,
        line: TerminalProgress,
        /,
    ) -> None:
        self.line: Final = line

        self.totals: Final[list[tuple[str, float | None]]] = []

    def begin(
        self,
        *,
        description: str,
        steps: int | None,
    ) -> None:
        self.line.begin(
            description=description,
            steps=steps,
        )

        for task in self.line.line.tasks:
            self.totals.append(
                (
                    task.description,
                    task.total,
                ),
            )

    def describe(
        self,
        *,
        description: str,
    ) -> None:
        self.line.describe(
            description=description,
        )

    def advance(
        self,
    ) -> None:
        self.line.advance()


@final
class TestSetUp:
    def test_it_installs_downloads_trains_and_reports_in_that_order(
        self,
        *,
        unprepared: Settings,
        requested_lists: list[str],
        trained: list[tuple[Path, int]],
        monkeypatch: MonkeyPatch,
    ) -> None:
        absent = list(
            REQUIRED_RESOURCES[:2],
        )

        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return tuple(resource for resource in resources if resource in absent)

        def download_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
            target: Path | None = None,
            client: object = None,
        ) -> tuple[NltkResource, ...]:
            fetched = missing_resources(
                resources=resources,
            )

            for resource in fetched:
                absent.remove(
                    resource,
                )

            return fetched

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="download_resources",
            value=download_resources,
        )

        lines = set_up(
            settings=unprepared,
        )

        directory = unprepared.reference_lists_dir

        assert lines == (
            "downloaded NLTK package punkt_tab",
            "downloaded NLTK package averaged_perceptron_tagger_eng",
            f"downloaded reference list {REFERENCE_FILES[0]} to {directory}",
            f"downloaded reference list {REFERENCE_FILES[1]} to {directory}",
            f"wrote a model to {unprepared.level_model_path}",
            *_ready(
                directory,
                unprepared.level_model_path,
            ),
            *REFERENCE_CITATIONS,
        )

        assert trained == [
            (
                unprepared.level_model_path,
                42,
            ),
        ]

        assert len(
            requested_lists,
        ) == len(
            REFERENCE_FILES,
        )

    def test_every_phase_whose_steps_are_unknown_pulses_after_the_counted_downloads(
        self,
        *,
        unprepared: Settings,
        requested_lists: list[str],
        fitted_model: SklearnLevelEstimator,
        monkeypatch: MonkeyPatch,
    ) -> None:
        absent = list(
            REQUIRED_RESOURCES[:2],
        )

        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return tuple(resource for resource in resources if resource in absent)

        def download_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
            target: Path | None = None,
            client: object = None,
        ) -> tuple[NltkResource, ...]:
            fetched = missing_resources(
                resources=resources,
            )

            for resource in fetched:
                absent.remove(
                    resource,
                )

            return fetched

        # TEST:
        # The dataset and the fit stand in for seconds of work, so that the real training reports its two phases.
        def build_dataset(
            **_: object,
        ) -> tuple[None, ...]:
            return (None,) * 9_520

        def train_shipped_model(
            **_: object,
        ) -> SklearnLevelEstimator:
            return fitted_model

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="download_resources",
            value=download_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="build_dataset",
            value=build_dataset,
        )

        monkeypatch.setattr(
            target=commands,
            name="train_shipped_model",
            value=train_shipped_model,
        )

        with TerminalProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=StringIO(),
                width=160,
            ),
        ) as line:
            phases = _PhaseTotals(
                line,
            )

            set_up(
                settings=unprepared,
                progress=phases,
            )

        assert phases.totals == [
            (
                "downloading the NLTK packages that are missing",
                2,
            ),
            (
                "downloading the reference lists that are missing",
                None,
            ),
            (
                "checking the level model",
                None,
            ),
            (
                "building the dataset from the reference lists",
                None,
            ),
            (
                "training the level model on 9,520 words",
                None,
            ),
        ]

    def test_a_second_run_changes_nothing_and_says_only_what_is_ready(
        self,
        *,
        unprepared: Settings,
        requested_lists: list[str],
        trained: list[tuple[Path, int]],
        fitted_model: SklearnLevelEstimator,
    ) -> None:
        save(
            estimator=fitted_model,
            path=unprepared.level_model_path,
        )

        first = set_up(
            settings=unprepared,
        )

        assert first[:2] == (
            f"downloaded reference list {REFERENCE_FILES[0]} to {unprepared.reference_lists_dir}",
            f"downloaded reference list {REFERENCE_FILES[1]} to {unprepared.reference_lists_dir}",
        )

        before = {
            path: path.stat().st_mtime_ns
            for path in unprepared.reference_lists_dir.parent.rglob(
                pattern="*",
            )
            if path.is_file()
        }

        second = set_up(
            settings=unprepared,
        )

        assert second == (
            *_ready(
                unprepared.reference_lists_dir,
                unprepared.level_model_path,
            ),
            *REFERENCE_CITATIONS,
        )

        assert before == {
            path: path.stat().st_mtime_ns
            for path in unprepared.reference_lists_dir.parent.rglob(
                pattern="*",
            )
            if path.is_file()
        }

        assert trained == []

        assert len(
            requested_lists,
        ) == len(
            REFERENCE_FILES,
        )

    @mark.parametrize(
        argnames="state",
        argvalues=(
            "absent",
            "without a sidecar",
            "a sidecar that is not JSON",
            "cut short",
            "another scikit-learn",
            "another scikit-learn and unreadable",
        ),
    )
    def test_a_model_that_is_absent_unusable_or_from_another_scikit_learn_is_trained_again(
        self,
        *,
        unprepared: Settings,
        requested_lists: list[str],
        trained: list[tuple[Path, int]],
        put_model: Callable[..., None],
        state: str,
    ) -> None:
        put_model(
            path=unprepared.level_model_path,
            state=state,
        )

        lines = set_up(
            settings=unprepared,
        )

        assert f"wrote a model to {unprepared.level_model_path}" in lines

        assert trained == [
            (
                unprepared.level_model_path,
                42,
            ),
        ]

    def test_a_list_that_cannot_be_had_stops_it_before_a_model_is_trained(
        self,
        *,
        unprepared: Settings,
        trained: list[tuple[Path, int]],
        monkeypatch: MonkeyPatch,
    ) -> None:
        def download_reference_lists(
            *,
            directory: Path,
            client: object = None,
        ) -> tuple[str, ...]:
            raise LinguisticResourcesMissingError(
                "the reference list foo could not be fetched from https://example.com/foo (HTTPStatusError)",
            )

        monkeypatch.setattr(
            target=commands,
            name="download_reference_lists",
            value=download_reference_lists,
        )

        with raises(
            expected_exception=LinguisticResourcesMissingError,
            match="could not be fetched",
        ):
            set_up(
                settings=unprepared,
            )

        assert trained == []


@final
class TestAnAddressIntakeDoesNotAnswerTo:
    # NOTE:
    # `0.0.0.0` reaches a service on this host on most systems, and a browser would send it as the name, so a command
    # that names the host that way reads the refusal as an error it can act on, with the names to use instead.
    async def test_a_command_that_names_the_host_as_0_0_0_0_prints_what_to_use_instead(
        self,
        *,
        intake: tuple[InMemoryStorage, ASGITransport],
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (
            _,
            transport,
        ) = intake

        monkeypatch.chdir(
            path=tmp_path,
        )

        monkeypatch.setenv(
            name="WORDWINNOW_INTAKE_URL",
            value="http://0.0.0.0:8010",
        )

        with raises(
            expected_exception=IntakeRejectedError,
        ) as caught:
            await list_recent(
                settings=Settings(),
                local=False,
                limit=5,
                transport=transport,
            )

        assert (
            str(
                object=caught.value,
            )
            == 'intake does not answer to the name "0.0.0.0", so that a web page cannot reach it under a name of its '
            "own; address it as localhost, 127.0.0.1, or [::1]"
        )


@final
class Listening:
    """
    What the services were given to listen on, and what they reached before
    that, with uvicorn and every store replaced by a double.
    """

    def __init__(
        self,
    ) -> None:
        self.configs: Final[list[Config]] = []

        self.reached: Final[list[str]] = []


@fixture
def listening(
    *,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> Listening:
    monkeypatch.chdir(
        path=tmp_path,
    )

    seen = Listening()

    @final
    class Server:
        def __init__(
            self,
            *,
            config: Config,
        ) -> None:
            seen.configs.append(
                config,
            )

        async def serve(
            self,
        ) -> None:
            return None

    def configure_process(
        *,
        settings: Settings,
        service_name: str,
        tracing: bool = True,
        default_log_level: str = "INFO",
    ) -> None:
        return None

    @asynccontextmanager
    async def intake_profile(
        *,
        settings: Settings,
    ) -> AsyncGenerator[SimpleNamespace]:
        seen.reached.append(
            "intake",
        )

        yield SimpleNamespace(
            new_unit_of_work=InMemoryStorage().new_unit_of_work,
            publisher=RecordingPublisher(),
            sources={},
            facts=EmptyFactQuery(),
            clock=FakeClock(
                at=EPOCH,
            ),
            metrics=build_metrics(),
        )

    @asynccontextmanager
    async def enrichment_profile(
        *,
        settings: Settings,
        metrics: object,
        on_lookup: object,
        observer: object,
        provider_hooks: object,
    ) -> AsyncGenerator[SimpleNamespace]:
        seen.reached.append(
            "enrichment",
        )

        yield SimpleNamespace(
            dictionary=FakeDictionary(),
            metrics=build_metrics(),
            activity=None,
        )

    for (
        name,
        value,
    ) in (
        (
            "Server",
            Server,
        ),
        (
            "configure_process",
            configure_process,
        ),
        (
            "intake_profile",
            intake_profile,
        ),
        (
            "enrichment_profile",
            enrichment_profile,
        ),
    ):
        monkeypatch.setattr(
            target=commands,
            name=name,
            value=value,
        )

    return seen


_SERVICES: Final[tuple[tuple[Callable[..., Coroutine[Any, Any, None]], str, int], ...]] = (
    (
        serve_intake,
        "intake",
        8010,
    ),
    (
        serve_enrichment,
        "enrichment",
        8100,
    ),
)


@final
class TestServing:
    @mark.parametrize(
        argnames=(
            "serve",
            "service",
            "port",
        ),
        argvalues=_SERVICES,
    )
    async def test_a_service_listens_on_loopback_unless_the_settings_name_an_address_the_stack_allows(
        self,
        *,
        serve: Callable[..., Coroutine[Any, Any, None]],
        service: str,
        port: int,
        listening: Listening,
    ) -> None:
        for settings in (
            Settings(),
            Settings(
                listen_address="::1",
            ),
            Settings(
                listen_address="0.0.0.0",
                compose_stack=True,
            ),
        ):
            await serve(
                settings=settings,
            )

        assert [
            (
                config.host,
                config.port,
            )
            for config in listening.configs
        ] == [
            (
                "127.0.0.1",
                port,
            ),
            (
                "::1",
                port,
            ),
            (
                "0.0.0.0",
                port,
            ),
        ]

    @mark.parametrize(
        argnames=(
            "serve",
            "service",
            "port",
        ),
        argvalues=_SERVICES,
    )
    async def test_the_address_is_named_in_the_listening_event(
        self,
        *,
        serve: Callable[..., Coroutine[Any, Any, None]],
        service: str,
        port: int,
        listening: Listening,
        caplog: LogCaptureFixture,
    ) -> None:
        with caplog.at_level(
            level=INFO,
            logger="wordwinnow.cli",
        ):
            await serve(
                settings=Settings(
                    listen_address="0.0.0.0",
                    compose_stack=True,
                ),
            )

        (record,) = caplog.records

        assert record.getMessage() == "service.listening"

        assert (
            vars(
                record,
            )["address"]
            == "0.0.0.0"
        )

        assert (
            vars(
                record,
            )["port"]
            == port
        )

    @mark.parametrize(
        argnames=(
            "serve",
            "service",
            "port",
        ),
        argvalues=_SERVICES,
    )
    async def test_the_application_it_serves_answers_to_the_names_in_the_settings(
        self,
        *,
        serve: Callable[..., Coroutine[Any, Any, None]],
        service: str,
        port: int,
        listening: Listening,
    ) -> None:
        await serve(
            settings=Settings(
                allowed_hosts=frozenset(
                    ("foo",),
                ),
            ),
        )

        (config,) = listening.configs

        assert isinstance(
            config.app,
            FastAPI,
        )

        async with AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            transport=ASGITransport(
                app=config.app,
            ),
        ) as client:
            named = await client.get(
                url="/health",
                headers={
                    "Host": "foo",
                },
            )

            other = await client.get(
                url="/health",
                headers={
                    "Host": "bar",
                },
            )

        assert (
            named.status_code,
            other.status_code,
        ) == (
            200,
            400,
        )

    @mark.parametrize(
        argnames=(
            "serve",
            "service",
            "port",
        ),
        argvalues=_SERVICES,
    )
    async def test_an_address_beyond_loopback_outside_the_stack_is_refused_before_the_profile_is_built(
        self,
        *,
        serve: Callable[..., Coroutine[Any, Any, None]],
        service: str,
        port: int,
        listening: Listening,
        caplog: LogCaptureFixture,
    ) -> None:
        with raises(
            expected_exception=SystemExit,
        ) as ended:
            await serve(
                settings=Settings(
                    listen_address="0.0.0.0",
                ),
            )

        assert ended.value.code == 1

        assert listening.reached == []

        assert listening.configs == []

        (record,) = caplog.records

        assert record.getMessage() == "listener.refused"

        assert (
            vars(
                record,
            )["service"]
            == service
        )

        assert (
            vars(
                record,
            )["port"]
            == port
        )


@final
class TestWhatTheEnrichmentClientSends:
    # NOTE:
    # The enrichment service refuses a request that another site's page sent, as its `Origin` or its `Sec-Fetch-Site`
    # says, so the operator view's client must send neither.
    async def test_a_request_carries_no_header_only_a_browser_sends(
        self,
    ) -> None:
        seen: list[Request] = []

        def handler(
            request: Request,
            /,
        ) -> Response:
            seen.append(
                request,
            )

            return Response(
                status_code=404,
            )

        client = EnrichmentClient(
            base_url="http://127.0.0.1:8100",
            transport=MockTransport(
                handler=handler,
            ),
        )

        try:
            with raises(
                expected_exception=ActivityUnreadableError,
            ):
                await client.activity()

        finally:
            await client.close()

        (request,) = seen

        assert (
            tuple(
                name
                for name in request.headers
                if name.lower() == "origin"
                or name.lower().startswith(
                    "sec-fetch-",
                )
            )
            == ()
        )
