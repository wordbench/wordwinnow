"""
The intake service over HTTP, against fakes.
"""

from collections.abc import (
    AsyncIterator,
)
from datetime import (
    timedelta,
)
from logging import (
    WARNING,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    ASGITransport,
    AsyncClient,
)
from opentelemetry.metrics import (
    get_meter_provider,
)
from opentelemetry.sdk.metrics import (
    MeterProvider,
)
from pytest import (
    LogCaptureFixture,
    MonkeyPatch,
    fixture,
)

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    FakeDocumentSource,
    InMemoryStorage,
    RaisingPublisher,
    RecordingPublisher,
    UnavailableDocumentSource,
)
from tests.services.lifespan import (
    run_lifespan,
)
from wordwinnow.application.dto import (
    LemmaCount,
    LevelCount,
    ProcessingProgress,
    VocabularySummary,
)
from wordwinnow.domain.analysis import (
    AnalysisStatus,
    Stage,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    Reference,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.services.intake.app import (
    IntakeDependencies,
    build_intake_app,
)
from wordwinnow.services.intake.schemas import (
    AnalysisOut,
    VocabularySummaryOut,
)

_NYT_DOCUMENT: Final = Document(
    title="New York Times Top Stories: science",
    text="A cab waits at the door\n\nThe King wore a black mask",
    origin=DocumentOrigin.NEW_YORK_TIMES,
    reference="top-stories/science",
    attribution="Data provided by The New York Times",
    references=(
        Reference(
            title="A cab waits at the door",
            url="https://example.com/photograph",
        ),
    ),
    retention=timedelta(
        hours=24,
    ),
)


@final
class FakeFactQuery:
    """
    A fixed summary.
    """

    async def summarize(
        self,
        *,
        focus_limit: int,
    ) -> VocabularySummary:
        return VocabularySummary(
            analysis_count=2,
            fact_count=40,
            levels=(
                LevelCount(
                    level=CefrLevel.A1,
                    count=10,
                ),
            ),
            focus_lemmas=(
                LemmaCount(
                    lemma="photograph",
                    part_of_speech=PartOfSpeech.NOUN,
                    analysis_count=2,
                    occurrence_count=7,
                ),
            )[:focus_limit],
        )


@final
class Harness:
    """
    The app and the fakes behind it.
    """

    def __init__(
        self,
        *,
        publisher: RecordingPublisher | RaisingPublisher,
    ) -> None:
        self.storage: Final = InMemoryStorage()

        self.publisher: Final = publisher

        self.metrics: Final = build_metrics()

        self.app: Final = build_intake_app(
            dependencies=IntakeDependencies(
                stale_after_seconds=60,
                new_unit_of_work=self.storage.new_unit_of_work,
                publisher=publisher,
                sources={
                    "nyt-top-stories": FakeDocumentSource(
                        document=_NYT_DOCUMENT,
                    ),
                    "nyt-rss": UnavailableDocumentSource(),
                },
                facts=FakeFactQuery(),
                clock=FakeClock(
                    at=EPOCH,
                ),
                metrics=self.metrics,
            ),
        )


@fixture
async def harness() -> AsyncIterator[tuple[Harness, AsyncClient]]:
    built = Harness(
        publisher=RecordingPublisher(),
    )

    async with AsyncClient(
        transport=ASGITransport(
            app=built.app,
        ),
        base_url="http://intake.test",
    ) as client:
        yield (
            built,
            client,
        )


@final
class TestCreateAnalysis:
    async def test_pasted_text_is_accepted_stored_and_published(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

        response = await client.post(
            url="/analyses",
            json={
                "text": "Irene Adler kept the photograph.",
                "title": "a-scandal-in-bohemia",
                "level": "A2",
                "target_level": "B2",
                "known_lemmas": [
                    "Catch",
                ],
            },
        )

        assert response.status_code == 202

        accepted = response.json()

        assert accepted["status"] == "requested"

        (stored,) = built.storage.analyses.values()

        assert (
            str(
                object=stored.id,
            )
            == accepted["analysis_id"]
        )

        assert stored.profile.level is CefrLevel.A2

        assert stored.profile.target_level is CefrLevel.B2

        assert stored.profile.known_lemmas == frozenset(
            {
                "catch",
            },
        )

        assert isinstance(
            built.publisher,
            RecordingPublisher,
        )

        assert (
            len(
                built.publisher.published,
            )
            == 1
        )

        assert 'wordwinnow_analyses_requested_total{origin="custom_text"} 1.0' in built.metrics.render().decode()

    async def test_a_topic_is_acquired_from_its_source(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

        response = await client.post(
            url="/analyses",
            json={
                "level": "B1",
                "source": "nyt-top-stories",
                "topic": "science",
                "limit": 3,
            },
        )

        assert response.status_code == 202

        (stored,) = built.storage.analyses.values()

        assert stored.document == _NYT_DOCUMENT

    async def test_bad_requests_are_refused_with_a_reason(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            _,
            client,
        ) = harness

        both = await client.post(
            url="/analyses",
            json={
                "level": "B1",
                "text": "Irene Adler kept the photograph.",
                "topic": "science",
                "source": "nyt-top-stories",
            },
        )

        assert both.status_code == 422

        assert "exactly one" in str(
            object=both.json()["detail"],
        )

        backwards = await client.post(
            url="/analyses",
            json={
                "text": "Irene Adler kept the photograph.",
                "level": "B2",
                "target_level": "A1",
            },
        )

        assert backwards.status_code == 422

        levelless = await client.post(
            url="/analyses",
            json={
                "text": "Irene Adler kept the photograph.",
            },
        )

        assert levelless.status_code == 422

        assert "level" in str(
            object=levelless.json()["detail"],
        )

        blank = await client.post(
            url="/analyses",
            json={
                "level": "B1",
                "text": "   ",
            },
        )

        assert blank.status_code == 422

        assert blank.json()["type"] == "request_rejected"

        unknown = await client.post(
            url="/analyses",
            json={
                "level": "B1",
                "source": "nonexistent",
                "topic": "science",
            },
        )

        assert unknown.status_code == 422

        assert unknown.json()["type"] == "source_not_configured"

    async def test_an_unavailable_source_is_a_503(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            _,
            client,
        ) = harness

        response = await client.post(
            url="/analyses",
            json={
                "level": "B1",
                "source": "nyt-rss",
                "topic": "Science",
            },
        )

        assert response.status_code == 503

        assert response.json()["type"] == "source_unavailable"

    async def test_an_unavailable_source_is_a_warning_in_the_log(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
        caplog: LogCaptureFixture,
    ) -> None:
        (
            _,
            client,
        ) = harness

        with caplog.at_level(
            level=WARNING,
            logger="wordwinnow.intake",
        ):
            await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "source": "nyt-rss",
                    "topic": "Science",
                },
            )

        assert tuple(
            (
                record.levelname,
                record.getMessage(),
                vars(
                    record,
                )["detail"],
            )
            for record in caplog.records
        ) == (
            (
                "WARNING",
                "source.unavailable",
                "the source could not be reached for 'Science'",
            ),
        )

    async def test_an_unreachable_broker_is_a_503_with_the_analysis_left_requested(
        self,
    ) -> None:
        built = Harness(
            publisher=RaisingPublisher(),
        )

        async with AsyncClient(
            transport=ASGITransport(
                app=built.app,
            ),
            base_url="http://intake.test",
        ) as client:
            response = await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "text": "Irene Adler kept the photograph.",
                },
            )

        assert response.status_code == 503

        assert response.json()["type"] == "analysis_not_published"

        (stored,) = built.storage.analyses.values()

        assert stored.status is AnalysisStatus.REQUESTED


@final
class TestReadAnalyses:
    async def test_an_analysis_round_trips_through_the_wire_schema(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

        accepted = (
            await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "text": "Irene Adler kept the photograph.",
                },
            )
        ).json()

        response = await client.get(
            url=f"/analyses/{accepted['analysis_id']}",
        )

        assert response.status_code == 200

        analysis = AnalysisOut.model_validate(
            obj=response.json(),
        ).to_domain()

        assert analysis == built.storage.analyses[analysis.id]

        assert analysis.document.text == "Irene Adler kept the photograph."

        assert analysis.requested_at == EPOCH

    async def test_a_processing_analysis_carries_its_workers_report_and_a_requested_one_none(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

        accepted = (
            await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "text": "Irene Adler kept the photograph.",
                },
            )
        ).json()

        requested = (
            await client.get(
                url=f"/analyses/{accepted['analysis_id']}",
            )
        ).json()

        (analysis_id,) = built.storage.analyses

        report = ProcessingProgress(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=156,
            done=63,
            unavailable=20,
            dictionary_paused=True,
            reported_at=EPOCH,
        )

        async with built.storage.new_unit_of_work() as uow:
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
                progress=report,
            )

            await uow.commit()

        processing = AnalysisOut.model_validate(
            obj=(
                await client.get(
                    url=f"/analyses/{accepted['analysis_id']}",
                )
            ).json(),
        )

        assert "progress" not in requested

        assert processing.processing_progress() == report

    async def test_an_unknown_analysis_is_a_404(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            _,
            client,
        ) = harness

        response = await client.get(
            url="/analyses/00000000-0000-0000-0000-000000000000",
        )

        assert response.status_code == 404

    async def test_the_listing_and_the_requeue(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

        for _ in range(
            2,
        ):
            await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "text": "Irene Adler kept the photograph.",
                },
            )

        listing = await client.get(
            url="/analyses",
            params={
                "limit": 1,
            },
        )

        assert (
            len(
                listing.json(),
            )
            == 1
        )

        for analysis in built.storage.analyses.values():
            analysis.requested_at = EPOCH - timedelta(
                hours=1,
            )

        requeued = await client.post(
            url="/analyses/requeue",
        )

        assert requeued.status_code == 200

        assert (
            len(
                requeued.json()["analysis_ids"],
            )
            == 2
        )

    async def test_an_unreachable_broker_during_the_requeue_is_a_503_that_says_so(
        self,
    ) -> None:
        built = Harness(
            publisher=RaisingPublisher(),
        )

        async with AsyncClient(
            transport=ASGITransport(
                app=built.app,
            ),
            base_url="http://intake.test",
        ) as client:
            await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "text": "Irene Adler kept the photograph.",
                },
            )

            for analysis in built.storage.analyses.values():
                analysis.requested_at = EPOCH - timedelta(
                    hours=1,
                )

            response = await client.post(
                url="/analyses/requeue",
            )

        assert response.status_code == 503

        assert response.json()["type"] == "messaging_unavailable"

    async def test_an_expired_borrowed_text_is_deleted_before_the_next_read(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

        accepted = (
            await client.post(
                url="/analyses",
                json={
                    "level": "B1",
                    "source": "nyt-top-stories",
                    "topic": "science",
                },
            )
        ).json()

        (stored,) = built.storage.analyses.values()

        assert stored.expires_at == stored.requested_at + timedelta(
            hours=24,
        )

        stored.requested_at = EPOCH - timedelta(
            days=2,
        )

        gone = await client.get(
            url=f"/analyses/{accepted['analysis_id']}",
        )

        assert gone.status_code == 404

        assert built.storage.analyses == {}

    async def test_the_vocabulary_summary_and_the_metrics(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            _,
            client,
        ) = harness

        summary = await client.get(
            url="/vocabulary/summary",
            params={
                "focus_limit": 5,
            },
        )

        assert summary.status_code == 200

        parsed = VocabularySummaryOut.model_validate(
            obj=summary.json(),
        ).to_domain()

        assert parsed.focus_lemmas[0].lemma == "photograph"

        metrics = await client.get(
            url="/metrics",
        )

        assert metrics.status_code == 200

        assert "wordwinnow_analyses_requested_total" in metrics.text

        health = await client.get(
            url="/health",
        )

        assert health.json() == {
            "status": "ok",
        }


@final
class TestTelemetry:
    async def test_starting_up_leaves_the_exporters_to_the_process(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # NOTE:
        # With an endpoint in the environment, FastAPI's own setup would install a meter provider at startup and add a
        # second span exporter beside the process's own.
        monkeypatch.setenv(
            name="OTEL_EXPORTER_OTLP_ENDPOINT",
            value="http://collector.invalid:4318",
        )

        app = Harness(
            publisher=RecordingPublisher(),
        ).app

        sent = await run_lifespan(
            app=app,
        )

        assert "lifespan.startup.complete" in sent

        assert not isinstance(
            get_meter_provider(),
            MeterProvider,
        )
