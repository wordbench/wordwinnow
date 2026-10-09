"""
Tracing: when it is on, and what a span never records.
"""

from typing import (
    final,
)

from httpx2 import (
    ASGITransport,
    AsyncClient,
    MockTransport,
    Request,
    Response,
)
from opentelemetry.sdk.trace import (
    TracerProvider,
)
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import (
    SpanKind,
)

from tests.fakes.ports import (
    FakeDictionary,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.infrastructure.observability.tracing import (
    configure_tracing,
    instrument_app,
    instrument_client,
)
from wordwinnow.services.enrichment.app import (
    EnrichmentDependencies,
    build_enrichment_app,
)


@final
class TestTracing:
    def test_no_endpoint_means_no_tracing(
        self,
    ) -> None:
        assert (
            configure_tracing(
                service_name="wordwinnow-intake",
                endpoint=None,
            )
            is None
        )

    async def test_an_outgoing_call_becomes_a_span_with_the_secret_redacted(
        self,
    ) -> None:
        exporter = InMemorySpanExporter()

        tracing = configure_tracing(
            service_name="wordwinnow-intake",
            endpoint=None,
            exporter=exporter,
        )

        assert tracing is not None

        def handler(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
            )

        async with AsyncClient(
            transport=MockTransport(
                handler=handler,
            ),
        ) as client:
            instrument_client(
                client=client,
            )

            await client.get(
                url="https://example.com/svc?api-key=hidden&x=1",
            )

        tracing.shutdown()

        spans = exporter.get_finished_spans()

        assert spans

        attributes = spans[0].attributes or {}

        urls = tuple(
            str(
                object=value,
            )
            for (
                key,
                value,
            ) in attributes.items()
            if key
            in {
                "http.url",
                "url.full",
            }
        )

        assert urls

        assert all("hidden" not in url for url in urls)

        assert all("[REDACTED]" in url for url in urls)

    async def test_a_service_traces_every_lookup_and_none_of_its_own_routes(
        self,
    ) -> None:
        exporter = InMemorySpanExporter()

        provider = TracerProvider()

        provider.add_span_processor(
            span_processor=SimpleSpanProcessor(
                span_exporter=exporter,
            ),
        )

        app = build_enrichment_app(
            dependencies=EnrichmentDependencies(
                dictionary=FakeDictionary(),
                metrics=build_metrics(),
            ),
        )

        instrument_app(
            app=app,
            tracer_provider=provider,
        )

        # NOTE:
        # Each word looked up here is, or contains, the name of a route the service leaves untraced.
        async with AsyncClient(
            transport=ASGITransport(
                app=app,
            ),
            base_url="http://enrichment.test",
        ) as client:
            for path in (
                "/health",
                "/metrics",
                "/activity",
                "/lookups/health",
                "/lookups/healthy",
                "/lookups/activity",
            ):
                await client.get(
                    url=path,
                )

        traced = tuple(
            (span.attributes or {}).get(
                "http.target",
            )
            for span in exporter.get_finished_spans()
            if span.kind is SpanKind.SERVER
        )

        assert traced == (
            "/lookups/health",
            "/lookups/healthy",
            "/lookups/activity",
        )
