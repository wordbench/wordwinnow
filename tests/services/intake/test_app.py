"""
The intake service over HTTP, against fakes.
"""

from collections.abc import (
    AsyncIterator,
)
from collections.abc import (
    Set as AbstractSet,
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
    mark,
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
        allowed_hosts: AbstractSet[str] = frozenset(),
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
            allowed_hosts=allowed_hosts,
        )


@fixture
async def harness() -> AsyncIterator[tuple[Harness, AsyncClient]]:
    built = Harness(
        publisher=RecordingPublisher(),
    )

    async with AsyncClient(
        base_url="http://127.0.0.1:8010",
        transport=ASGITransport(
            app=built.app,
        ),
    ) as client:
        yield (
            built,
            client,
        )


@fixture
def client(
    *,
    harness: tuple[Harness, AsyncClient],
) -> AsyncClient:
    return harness[1]


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
            ("catch",),
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
            base_url="http://127.0.0.1:8010",
            transport=ASGITransport(
                app=built.app,
            ),
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
            base_url="http://127.0.0.1:8010",
            transport=ASGITransport(
                app=built.app,
            ),
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


_NOT_ANSWERED: Final = (
    'intake does not answer to the name "{name}", so that a web page cannot reach it under a name of its own; '
    "address it as localhost, 127.0.0.1, or [::1]"
)

_ANOTHER_SITE: Final = "intake refuses a request sent by another site's page"


@final
class TestTheHostAndTheSite:
    @mark.parametrize(
        argnames="host",
        argvalues=(
            "127.0.0.1:8010",
            "localhost:8010",
            "[::1]:8010",
            "LOCALHOST:8010",
        ),
    )
    async def test_a_name_of_the_loopback_interface_is_answered_in_any_case(
        self,
        *,
        client: AsyncClient,
        host: str,
    ) -> None:
        response = await client.get(
            url="/health",
            headers={
                "Host": host,
            },
        )

        assert response.status_code == 200

    @mark.parametrize(
        argnames=(
            "host",
            "name",
        ),
        argvalues=(
            (
                "attacker.example",
                "attacker.example",
            ),
            (
                "0.0.0.0:8010",
                "0.0.0.0",
            ),
        ),
    )
    async def test_another_name_is_refused_as_a_problem_that_names_it(
        self,
        *,
        client: AsyncClient,
        host: str,
        name: str,
    ) -> None:
        response = await client.get(
            url="/health",
            headers={
                "Host": host,
            },
        )

        assert response.status_code == 400

        assert response.json() == {
            "type": "host_not_allowed",
            "detail": _NOT_ANSWERED.format(
                name=name,
            ),
        }

    async def test_an_empty_host_is_refused_as_the_empty_name(
        self,
        *,
        client: AsyncClient,
    ) -> None:
        response = await client.get(
            url="/health",
            headers={
                "Host": "",
            },
        )

        assert response.status_code == 400

        assert response.json() == {
            "type": "host_not_allowed",
            "detail": _NOT_ANSWERED.format(
                name="",
            ),
        }

    async def test_a_name_the_builder_is_given_is_answered_and_the_same_name_without_it_is_not(
        self,
    ) -> None:
        for (
            allowed_hosts,
            status,
        ) in (
            (
                frozenset(
                    ("intake",),
                ),
                200,
            ),
            (
                frozenset(),
                400,
            ),
        ):
            built = Harness(
                publisher=RecordingPublisher(),
                allowed_hosts=allowed_hosts,
            )

            async with AsyncClient(
                base_url="http://127.0.0.1:8010",
                transport=ASGITransport(
                    app=built.app,
                ),
            ) as client:
                response = await client.get(
                    url="/health",
                    headers={
                        "Host": "intake:8010",
                    },
                )

            assert response.status_code == status

    async def test_a_form_another_site_posts_to_the_requeue_requeues_nothing(
        self,
        *,
        harness: tuple[Harness, AsyncClient],
    ) -> None:
        (
            built,
            client,
        ) = harness

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

        assert isinstance(
            built.publisher,
            RecordingPublisher,
        )

        published = len(
            built.publisher.published,
        )

        form = await client.post(
            url="/analyses/requeue",
            data={
                "foo": "bar",
            },
            headers={
                "Origin": "https://example.com",
            },
        )

        assert form.status_code == 403

        assert (
            len(
                built.publisher.published,
            )
            == published
        )

        # NOTE:
        # The control: the same analysis, stale as it is, is requeued when the request is not another site's.
        requeued = await client.post(
            url="/analyses/requeue",
        )

        assert requeued.status_code == 200

        assert (
            len(
                requeued.json()["analysis_ids"],
            )
            == 1
        )

    async def test_a_json_body_sent_as_text_is_refused_as_unprocessable(
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
            content='{"text": "Irene Adler kept the photograph.", "level": "A2"}',
            headers={
                "Content-Type": "text/plain",
            },
        )

        assert response.status_code == 422

        assert built.storage.analyses == {}

    @mark.parametrize(
        argnames=(
            "headers",
            "status",
        ),
        argvalues=(
            (
                {
                    "Origin": "https://example.com",
                },
                403,
            ),
            (
                {
                    "Origin": "null",
                },
                403,
            ),
            (
                {
                    "Sec-Fetch-Site": "cross-site",
                },
                403,
            ),
            (
                {
                    "Sec-Fetch-Site": "same-site",
                },
                403,
            ),
            (
                {
                    "Sec-Fetch-Site": "none",
                },
                200,
            ),
            (
                {
                    "Sec-Fetch-Site": "same-origin",
                    "Origin": "http://127.0.0.1:8010",
                },
                200,
            ),
            (
                {
                    "Origin": "http://127.0.0.1:8010",
                },
                200,
            ),
            (
                {
                    "Origin": "http://localhost:8010",
                },
                403,
            ),
        ),
    )
    async def test_a_request_another_sites_page_sent_is_refused_with_its_detail_and_no_problem(
        self,
        *,
        client: AsyncClient,
        headers: dict[str, str],
        status: int,
    ) -> None:
        response = await client.get(
            url="/health",
            headers=headers,
        )

        assert response.status_code == status

        if status == 403:
            assert response.json() == {
                "detail": _ANOTHER_SITE,
            }

    @mark.parametrize(
        argnames="path",
        argvalues=(
            "/docs",
            "/openapi.json",
        ),
    )
    async def test_the_pages_the_service_serves_itself_are_open_to_the_page_that_loaded_them(
        self,
        *,
        client: AsyncClient,
        path: str,
    ) -> None:
        response = await client.get(
            url=path,
            headers={
                "Sec-Fetch-Site": "same-origin",
            },
        )

        assert response.status_code == 200

    @mark.parametrize(
        argnames="path",
        argvalues=(
            "/health",
            "/metrics",
            "/docs",
            "/openapi.json",
        ),
    )
    async def test_every_route_is_covered_the_documentation_included(
        self,
        *,
        client: AsyncClient,
        path: str,
    ) -> None:
        foreign = await client.get(
            url=path,
            headers={
                "Host": "attacker.example",
            },
        )

        another_site = await client.get(
            url=path,
            headers={
                "Origin": "https://example.com",
            },
        )

        assert foreign.status_code == 400

        assert another_site.status_code == 403

    async def test_the_host_is_judged_before_the_site(
        self,
        *,
        client: AsyncClient,
    ) -> None:
        response = await client.get(
            url="/health",
            headers={
                "Host": "attacker.example",
                "Origin": "https://example.com",
                "Sec-Fetch-Site": "cross-site",
            },
        )

        assert response.status_code == 400

        assert response.json()["type"] == "host_not_allowed"
