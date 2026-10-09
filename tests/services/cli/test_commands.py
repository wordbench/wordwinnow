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
    AsyncIterator,
    Sequence,
)
from pathlib import (
    Path,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    ASGITransport,
    AsyncClient,
    MockTransport,
    Request,
    Response,
)
from pytest import (
    MonkeyPatch,
    fixture,
    raises,
)
from rich.console import (
    Console,
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
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    REQUIRED_RESOURCES,
    NltkResource,
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
    analyze_locally,
    analyze_remotely,
    doctor,
    list_recent,
    requeue,
    show,
)
from wordwinnow.services.cli.intake_client import (
    IntakeUnavailableError,
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

_TEXT: Final = (
    "The King wanted the photograph back. The groom looked very drunken, but Irene Adler kept the photograph."
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

        stored = await show(
            analysis_id=analysis.id,
            settings=settings,
            local=True,
        )

        assert stored == analysis

        assert stored.items == analysis.items

        summaries = await list_recent(
            settings=settings,
            local=True,
            limit=5,
        )

        assert tuple(summary.analysis_id for summary in summaries) == (analysis.id,)

        console = Console(
            record=True,
            width=80,
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
        with raises(
            expected_exception=AnalysisNotFoundError,
        ):
            await show(
                analysis_id=AnalysisId.new(),
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
        # A refused connection is what a learner meets when the distributed mode is not running, so the error says how
        # to start it and how to do without it.
        with raises(
            expected_exception=IntakeUnavailableError,
            match="`make up-full` starts it, and --local runs analyze, show, and list",
        ):
            await list_recent(
                settings=Settings(),
                local=False,
                limit=5,
            )


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
