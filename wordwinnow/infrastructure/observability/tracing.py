"""
OpenTelemetry tracing, configured once per process.

Traces are exported over OTLP/HTTP to whatever collector the environment
names; when tracing is off, the no-op provider stays in place and nothing here
costs anything.

Outgoing HTTP clients are instrumented one by one, with a hook that redacts
secret query parameters from the span's URL attributes, because the New York
Times key travels in one.
"""

from typing import (
    Final,
    final,
)

from fastapi import (
    FastAPI,
)
from httpx2 import (
    AsyncClient,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import (
    FastAPIInstrumentor,
)
from opentelemetry.instrumentation.httpx import (
    HTTPX2ClientInstrumentor,
    RequestInfo,
)
from opentelemetry.sdk.resources import (
    Resource,
)
from opentelemetry.sdk.trace import (
    TracerProvider,
)
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import (
    Span,
    Tracer,
    get_tracer,
    set_tracer_provider,
)

from wordwinnow.infrastructure.log_payload import (
    redact,
)

# NOTE:
# The span attributes the httpx instrumentation fills with the request URL, in both attribute conventions.
_URL_ATTRIBUTES: Final = (
    "http.url",
    "url.full",
)

# NOTE:
# The routes a service answers that no trace needs: the path right after the host, and nothing after it, so a lookup
# whose word is one of these names is still traced.
_UNTRACED_ROUTES: Final = "://[^/]+/(health|metrics|activity)$"


@final
class Tracing:
    """
    The configured provider of one process, so it can be flushed at exit.
    """

    def __init__(
        self,
        *,
        provider: TracerProvider,
    ) -> None:
        self._provider: Final = provider

    def shutdown(
        self,
    ) -> None:
        """
        Flush and stop the exporter.
        """

        self._provider.shutdown()


def _redact_url(
    span: Span,
    request: RequestInfo,
    /,
) -> None:
    if not span.is_recording():
        return

    for attribute in _URL_ATTRIBUTES:
        span.set_attribute(
            key=attribute,
            value=redact(
                text=str(
                    object=request.url,
                ),
            ),
        )


async def _redact_url_async(
    span: Span,
    request: RequestInfo,
    /,
) -> None:
    _redact_url(
        span,
        request,
    )


def configure_tracing(
    *,
    service_name: str,
    endpoint: str | None,
    exporter: SpanExporter | None = None,
) -> Tracing | None:
    """
    Install a tracer provider that exports to `endpoint`, or do nothing.

    An explicit `exporter` replaces the OTLP one, which is how tests capture
    spans in memory.
    """

    if exporter is None and endpoint is None:
        return None

    provider = TracerProvider(
        resource=Resource.create(
            attributes={
                "service.name": service_name,
            },
        ),
    )

    chosen = (
        exporter
        if exporter is not None
        else OTLPSpanExporter(
            endpoint=f"{endpoint}/v1/traces",
        )
    )

    provider.add_span_processor(
        span_processor=BatchSpanProcessor(
            span_exporter=chosen,
        ),
    )

    set_tracer_provider(
        tracer_provider=provider,
    )

    return Tracing(
        provider=provider,
    )


def instrument_client(
    *,
    client: AsyncClient,
) -> None:
    """
    Trace every request `client` makes, with secret query parameters redacted
    from the span.
    """

    # NOTE:
    # Instrumenting a client rather than the library keeps the effect visible where the client is built, and reaches a
    # client with a custom transport.
    #
    # WARN:
    # `instrument_client` is a class method whose first parameter is the client, declared positional-or-keyword; the
    # async hook is passed as `request_hook`, which is what the instrumentation reads for an async client.
    HTTPX2ClientInstrumentor.instrument_client(
        client=client,
        request_hook=_redact_url_async,
    )


def instrument_app(
    *,
    app: FastAPI,
    tracer_provider: TracerProvider | None = None,
) -> None:
    """
    Open a server span for every request `app` handles, continuing the trace a
    caller's headers carry, except for the health, metrics, and activity
    routes.

    The spans go to the installed provider unless `tracer_provider` names
    another, which is how a test reads them.
    """

    # NOTE:
    # A scrape every five seconds, or the activity read four times a second, is not a trace anyone reads.
    #
    # WARN:
    # The instrumentation searches the whole URL for each pattern, so a bare `health` would also leave untraced the
    # lookup of every word that contains it, `healthy` among them; the pattern names the routes from the host on.
    FastAPIInstrumentor.instrument_app(
        app=app,
        tracer_provider=tracer_provider,
        excluded_urls=_UNTRACED_ROUTES,
    )


def tracer(
    *,
    name: str,
) -> Tracer:
    """
    A tracer for one module, from whatever provider is installed.
    """

    return get_tracer(
        instrumenting_module_name=name,
    )
