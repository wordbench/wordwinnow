"""
The enrichment HTTP application.

One route answers the question the workers ask: everything the dictionary has
for a lemma.

The outcome is always HTTP 200, because an unavailable dictionary is an answer
rather than a failure of this service.

Another answers an operator: what the dictionary is doing now, the words in
flight among it.
"""

from collections.abc import (
    Callable,
    Coroutine,
)
from functools import (
    partial,
)
from time import (
    perf_counter,
)
from typing import (
    Annotated,
    Any,
    Final,
    final,
)

from fastapi import (
    FastAPI,
    HTTPException,
    Path,
    Response,
)
from httpx2 import (
    Request,
)
from httpx2 import (
    Response as HttpResponse,
)
from prometheus_client import (
    CONTENT_TYPE_LATEST,
)

from wordwinnow.application.ports.dictionary import (
    Dictionary,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)
from wordwinnow.infrastructure.dictionary.activity import (
    ActivityPayload,
    DictionaryActivity,
    to_payload,
)
from wordwinnow.infrastructure.dictionary.cache import (
    LookupObserver,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    ProviderObserver,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    LookupPayload,
    to_wire,
)
from wordwinnow.infrastructure.observability.metrics import (
    Metrics,
    status_class_of,
)
from wordwinnow.services.http_context import (
    install_correlation_middleware,
)

_STARTED: Final = "wordwinnow_started_at"

# NOTE:
# The label value for a lookup that did not fail, so the reason label stays bounded to the failure vocabulary plus
# this one.
_NO_REASON: Final = "none"


@final
class EnrichmentDependencies:
    """
    Everything the routes need, built by the composition root.

    `activity` reads what the dictionary is doing, and is `None` for a
    dictionary that asks no provider.
    """

    def __init__(
        self,
        *,
        dictionary: Dictionary,
        metrics: Metrics,
        activity: Callable[[], DictionaryActivity] | None = None,
    ) -> None:
        self.dictionary: Final = dictionary

        self.metrics: Final = metrics

        self.activity: Final = activity


def lookup_observer(
    *,
    metrics: Metrics,
) -> LookupObserver:
    """
    The observer to give the caching dictionary: it counts every lookup by
    outcome, failure reason, and whether the cache served it.
    """

    def observe(
        lookup: DictionaryLookup,
        served_from_cache: bool,
        /,
    ) -> None:
        metrics.dictionary_lookups.labels(
            outcome=lookup.outcome,
            reason=lookup.failure_reason or _NO_REASON,
            served_from_cache=str(
                object=served_from_cache,
            ).lower(),
        ).inc()

    return observe


@final
class _CountingObserver:
    """
    Turns what the dictionary client reports into the dictionary's metrics.

    Satisfies `ProviderObserver` structurally.
    """

    def __init__(
        self,
        *,
        metrics: Metrics,
    ) -> None:
        self._metrics: Final = metrics

    def waited(
        self,
        *,
        cause: WaitCause,
        seconds: float,
    ) -> None:
        self._metrics.dictionary_wait_seconds.labels(
            cause=cause,
        ).inc(
            amount=seconds,
        )

    def retried(
        self,
        *,
        reason: FailureReason,
    ) -> None:
        self._metrics.dictionary_retries.labels(
            reason=reason,
        ).inc()


def provider_observer(
    *,
    metrics: Metrics,
) -> ProviderObserver:
    """
    The observer to give the dictionary client: it adds up where lookups wait,
    and counts retries by reason.
    """

    return _CountingObserver(
        metrics=metrics,
    )


def circuit_gauge(
    *,
    metrics: Metrics,
    activity: Callable[[], DictionaryActivity],
) -> None:
    """
    Keep the circuit's gauge at the state the dictionary's activity reports,
    read each time the metrics are rendered.
    """

    # NOTE:
    # Read rather than reported, because the circuit becomes half-open when its recovery period ends, a moment at
    # which nothing in the client runs to report it.
    for state in BreakerState:
        metrics.dictionary_circuit_state.labels(
            state=state.name.lower(),
        ).set_function(
            f=partial(
                _in_state,
                activity,
                state,
            ),
        )


def _in_state(
    activity: Callable[[], DictionaryActivity],
    state: BreakerState,
    /,
) -> float:
    return 1.0 if activity().provider.circuit is state else 0.0


def provider_hooks(
    *,
    metrics: Metrics,
) -> dict[str, list[Callable[..., Coroutine[Any, Any, None]]]]:
    """
    The httpx2 event hooks that time every round trip to the provider, from
    the request leaving to the response headers arriving.
    """

    async def started(
        request: Request,
        /,
    ) -> None:
        request.extensions[_STARTED] = perf_counter()

    async def finished(
        response: HttpResponse,
        /,
    ) -> None:
        began = response.request.extensions.get(
            _STARTED,
        )

        if began is None:
            return

        metrics.dictionary_provider_request_seconds.labels(
            status_class=status_class_of(
                status_code=response.status_code,
            ),
        ).observe(
            amount=perf_counter() - began,
        )

    return {
        "request": [
            started,
        ],
        "response": [
            finished,
        ],
    }


def build_enrichment_app(
    *,
    dependencies: EnrichmentDependencies,
) -> FastAPI:
    """
    Assemble the application around its dependencies.
    """

    # NOTE:
    # Each process configures its own tracing, so FastAPI's setup from the OpenTelemetry environment stays off: it
    # would add a second span exporter beside the process's own, and metric and log exporters the trace collector
    # rejects.
    app = FastAPI(
        telemetry={
            "auto_configure": False,
        },
        title="wordwinnow enrichment",
        version="1",
    )

    install_correlation_middleware(
        app=app,
    )

    if dependencies.activity is not None:
        circuit_gauge(
            metrics=dependencies.metrics,
            activity=dependencies.activity,
        )

    @app.get(
        path="/health",
    )
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
        }

    @app.get(
        path="/metrics",
    )
    async def metrics() -> Response:
        return Response(
            content=dependencies.metrics.render(),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.get(
        path="/lookups/{lemma}",
        response_model=LookupPayload,
    )
    async def look_up(
        lemma: Annotated[
            str,
            Path(
                min_length=1,
                max_length=100,
            ),
        ],
    ) -> LookupPayload:
        lookup = await dependencies.dictionary.look_up(
            lemma=lemma,
        )

        return to_wire(
            lookup=lookup,
        )

    @app.get(
        path="/activity",
        response_model=ActivityPayload,
    )
    async def activity() -> ActivityPayload:
        if dependencies.activity is None:
            raise HTTPException(
                status_code=404,
                detail="this dictionary asks no provider, so it has no activity to show",
            )

        return to_payload(
            activity=dependencies.activity(),
        )

    return app
