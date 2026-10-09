"""
The intake HTTP application.

Routes are thin: they translate the wire schema to the domain, call one use
case, and translate the answer back; errors become the status codes and the
problem types the application's error taxonomy prescribes.
"""

from collections.abc import (
    Mapping,
)
from logging import (
    getLogger,
)
from typing import (
    Annotated,
    Final,
    final,
)
from uuid import (
    UUID,
)

from fastapi import (
    Body,
    FastAPI,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.responses import (
    JSONResponse,
)
from prometheus_client import (
    CONTENT_TYPE_LATEST,
)

from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    AnalysisNotPublishedError,
    FactStoreUnavailableError,
    MessagingUnavailableError,
    SourceNotConfiguredError,
    SourceRejectedError,
    SourceUnavailableError,
)
from wordwinnow.application.ports.analysis_event_publisher import (
    AnalysisEventPublisher,
)
from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.document_source import (
    DocumentSource,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)
from wordwinnow.application.ports.vocabulary_fact_query import (
    VocabularyFactQuery,
)
from wordwinnow.application.use_cases.acquire_a_document import (
    acquire_a_document,
)
from wordwinnow.application.use_cases.purge_expired_analyses import (
    purge_expired_analyses,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.application.use_cases.requeue_stale_analyses import (
    requeue_stale_analyses,
)
from wordwinnow.application.use_cases.show_an_analysis import (
    list_analyses,
    show_an_analysis,
    show_progress,
)
from wordwinnow.domain.analysis import (
    AnalysisStatus,
)
from wordwinnow.domain.document import (
    InvalidDocumentError,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.logging import (
    bind_correlation_id,
)
from wordwinnow.infrastructure.observability.metrics import (
    Metrics,
)
from wordwinnow.infrastructure.sources.custom_text import (
    document_from_text,
)
from wordwinnow.services.http_context import (
    install_correlation_middleware,
)
from wordwinnow.services.intake.schemas import (
    AnalysisAccepted,
    AnalysisOut,
    AnalysisRequest,
    AnalysisSummaryOut,
    Problem,
    ProblemType,
    RequeueResult,
    VocabularySummaryOut,
    summaries_out,
)

_logger: Final = getLogger(
    name="wordwinnow.intake",
)

# NOTE:
# What a pasted text is called when the request names no title or reference.
PASTED_TEXT: Final = "pasted text"


@final
class IntakeDependencies:
    """
    Everything the routes need, built by the composition root.
    """

    def __init__(
        self,
        *,
        stale_after_seconds: float,
        new_unit_of_work: UnitOfWorkFactory,
        publisher: AnalysisEventPublisher,
        sources: Mapping[str, DocumentSource],
        facts: VocabularyFactQuery,
        clock: Clock,
        metrics: Metrics,
    ) -> None:
        self.stale_after_seconds: Final = stale_after_seconds

        self.new_unit_of_work: Final = new_unit_of_work

        self.publisher: Final = publisher

        self.sources: Final = sources

        self.facts: Final = facts

        self.clock: Final = clock

        self.metrics: Final = metrics


def _problem(
    status_code: int,
    problem_type: ProblemType,
    exception: Exception,
    /,
) -> JSONResponse:
    return JSONResponse(
        content=Problem(
            type=problem_type,
            detail=str(
                object=exception,
            ),
        ).model_dump(
            mode="json",
        ),
        status_code=status_code,
    )


async def _purge(
    dependencies: IntakeDependencies,
    /,
) -> None:
    deleted = await purge_expired_analyses(
        new_unit_of_work=dependencies.new_unit_of_work,
        clock=dependencies.clock,
    )

    if deleted:
        _logger.info(
            msg="analyses.purged",
            extra={
                "count": deleted,
            },
        )


def build_intake_app(
    *,
    dependencies: IntakeDependencies,
) -> FastAPI:
    """
    Assemble the application around its dependencies.

    Every route that reads the store first deletes the analyses whose text may
    no longer be kept, so nothing outlives its retention by more than the
    quiet between two requests.
    """

    # NOTE:
    # Each process configures its own tracing, so FastAPI's setup from the OpenTelemetry environment stays off: it
    # would add a second span exporter beside the process's own, and metric and log exporters the trace collector
    # rejects.
    app = FastAPI(
        telemetry={
            "auto_configure": False,
        },
        title="wordwinnow intake",
        version="1",
    )

    install_correlation_middleware(
        app=app,
    )

    # WARN:
    # FastAPI calls an exception handler as `handler(request, exc)`, positionally, so the handlers below declare the
    # positional boundary the framework imposes.
    @app.exception_handler(
        exc_class_or_status_code=AnalysisNotFoundError,
    )
    async def not_found(
        request: Request,
        exception: AnalysisNotFoundError,
        /,
    ) -> JSONResponse:
        return _problem(
            404,
            ProblemType.ANALYSIS_NOT_FOUND,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=SourceRejectedError,
    )
    async def rejected(
        request: Request,
        exception: SourceRejectedError,
        /,
    ) -> JSONResponse:
        return _problem(
            422,
            ProblemType.REQUEST_REJECTED,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=InvalidDocumentError,
    )
    async def invalid_document(
        request: Request,
        exception: InvalidDocumentError,
        /,
    ) -> JSONResponse:
        return _problem(
            422,
            ProblemType.REQUEST_REJECTED,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=SourceNotConfiguredError,
    )
    async def not_configured(
        request: Request,
        exception: SourceNotConfiguredError,
        /,
    ) -> JSONResponse:
        return _problem(
            422,
            ProblemType.SOURCE_NOT_CONFIGURED,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=SourceUnavailableError,
    )
    async def unavailable(
        request: Request,
        exception: SourceUnavailableError,
        /,
    ) -> JSONResponse:
        # NOTE:
        # The learner reads the refusal in the answer; the operator reads it here, because a source that times out or
        # fails leaves no other line in this service's log.
        _logger.warning(
            msg="source.unavailable",
            extra={
                "detail": str(
                    object=exception,
                ),
            },
        )

        return _problem(
            503,
            ProblemType.SOURCE_UNAVAILABLE,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=AnalysisNotPublishedError,
    )
    async def not_published(
        request: Request,
        exception: AnalysisNotPublishedError,
        /,
    ) -> JSONResponse:
        return _problem(
            503,
            ProblemType.ANALYSIS_NOT_PUBLISHED,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=MessagingUnavailableError,
    )
    async def messaging_unavailable(
        request: Request,
        exception: MessagingUnavailableError,
        /,
    ) -> JSONResponse:
        return _problem(
            503,
            ProblemType.MESSAGING_UNAVAILABLE,
            exception,
        )

    @app.exception_handler(
        exc_class_or_status_code=FactStoreUnavailableError,
    )
    async def store_unavailable(
        request: Request,
        exception: FactStoreUnavailableError,
        /,
    ) -> JSONResponse:
        return _problem(
            503,
            ProblemType.STORE_UNAVAILABLE,
            exception,
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

    @app.post(
        path="/analyses",
        status_code=202,
        response_model=AnalysisAccepted,
    )
    async def create_analysis(
        body: Annotated[
            AnalysisRequest,
            Body(),
        ],
    ) -> AnalysisAccepted:
        await _purge(
            dependencies,
        )

        if body.text is not None:
            document = document_from_text(
                text=body.text,
                title=body.title or PASTED_TEXT,
                reference=body.reference or PASTED_TEXT,
            )

        else:
            # NOTE:
            # The request's own validation guarantees a source beside a topic; the guard keeps the type checker and
            # the route in agreement.
            if body.source is None or body.topic is None:
                raise SourceRejectedError(
                    "an analysis request requires a source with a topic",
                )

            document = await acquire_a_document(
                source=body.source,
                topic=body.topic,
                limit=body.limit,
                sources=dependencies.sources,
            )

        analysis = await request_an_analysis(
            document=document,
            profile=body.profile(),
            options=body.options(),
            new_unit_of_work=dependencies.new_unit_of_work,
            publisher=dependencies.publisher,
            clock=dependencies.clock,
        )

        bind_correlation_id(
            correlation_id=str(
                object=analysis.id,
            ),
        )

        dependencies.metrics.analyses_requested.labels(
            origin=document.origin,
        ).inc()

        _logger.info(
            msg="analysis.requested",
            extra={
                "analysis_id": str(
                    object=analysis.id,
                ),
                "origin": document.origin,
                "characters": len(
                    document.text,
                ),
            },
        )

        return AnalysisAccepted(
            analysis_id=analysis.id.value,
            status=analysis.status,
        )

    @app.get(
        path="/analyses",
        response_model=list[AnalysisSummaryOut],
    )
    async def list_recent(
        limit: Annotated[
            int,
            Query(
                ge=1,
                le=200,
            ),
        ] = 20,
    ) -> list[AnalysisSummaryOut]:
        await _purge(
            dependencies,
        )

        summaries = await list_analyses(
            limit=limit,
            new_unit_of_work=dependencies.new_unit_of_work,
        )

        return summaries_out(
            summaries=summaries,
        )

    @app.get(
        path="/analyses/{analysis_id}",
        response_model=AnalysisOut,
    )
    async def read_analysis(
        analysis_id: Annotated[
            UUID,
            Path(),
        ],
    ) -> AnalysisOut:
        await _purge(
            dependencies,
        )

        analysis = await show_an_analysis(
            analysis_id=AnalysisId(
                value=analysis_id,
            ),
            new_unit_of_work=dependencies.new_unit_of_work,
        )

        progress = (
            await show_progress(
                analysis_id=analysis.id,
                new_unit_of_work=dependencies.new_unit_of_work,
            )
            if analysis.status is AnalysisStatus.PROCESSING
            else None
        )

        return AnalysisOut.from_domain(
            analysis=analysis,
            progress=progress,
        )

    @app.post(
        path="/analyses/requeue",
        response_model=RequeueResult,
    )
    async def requeue() -> RequeueResult:
        requeued = await requeue_stale_analyses(
            older_than_seconds=dependencies.stale_after_seconds,
            new_unit_of_work=dependencies.new_unit_of_work,
            publisher=dependencies.publisher,
            clock=dependencies.clock,
        )

        _logger.info(
            msg="analyses.requeued",
            extra={
                "count": len(
                    requeued,
                ),
            },
        )

        return RequeueResult(
            analysis_ids=[analysis_id.value for analysis_id in requeued],
        )

    @app.get(
        path="/vocabulary/summary",
        response_model=VocabularySummaryOut,
    )
    async def vocabulary_summary(
        focus_limit: Annotated[
            int,
            Query(
                ge=1,
                le=200,
            ),
        ] = 20,
    ) -> VocabularySummaryOut:
        summary = await dependencies.facts.summarize(
            focus_limit=focus_limit,
        )

        return VocabularySummaryOut.from_domain(
            summary=summary,
        )

    return app
