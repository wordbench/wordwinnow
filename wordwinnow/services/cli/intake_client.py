"""
The CLI's view of the intake service.

Every answer comes back as domain objects, and every refusal comes back as the
application error the service named in its problem type, so a command handles
a remote refusal the way it handles a local one.
"""

from collections.abc import (
    Mapping,
)
from dataclasses import (
    dataclass,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    AsyncBaseTransport,
    AsyncClient,
    ConnectError,
    HTTPError,
    Response,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
    VocabularySummary,
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
from wordwinnow.domain.analysis import (
    Analysis,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
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
)


@final
class IntakeUnavailableError(
    RuntimeError,
):
    """
    Raised when the intake service does not answer.

    Retryable once the service is up.
    """


@final
class IntakeRejectedError(
    RuntimeError,
):
    """
    Raised when the intake service answered with an error it did not name as a
    problem, such as a validation failure of the request itself.

    Not retryable with the same request.
    """


# NOTE:
# The application error each problem type stands for, so the caller sees the same class in both modes.
_ERRORS: Final = MappingProxyType(
    mapping={
        ProblemType.ANALYSIS_NOT_FOUND: AnalysisNotFoundError,
        ProblemType.REQUEST_REJECTED: SourceRejectedError,
        ProblemType.SOURCE_NOT_CONFIGURED: SourceNotConfiguredError,
        ProblemType.SOURCE_UNAVAILABLE: SourceUnavailableError,
        ProblemType.ANALYSIS_NOT_PUBLISHED: AnalysisNotPublishedError,
        ProblemType.MESSAGING_UNAVAILABLE: MessagingUnavailableError,
        ProblemType.STORE_UNAVAILABLE: FactStoreUnavailableError,
    },
)

# NOTE:
# What a refused connection almost always means at the command line, whose default is the remote mode: the distributed
# mode is not running.
_NOT_RUNNING: Final = (
    "; is the distributed mode running? `make up-full` starts it, and --local runs analyze, show, and list in this "
    "process instead"
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class WatchedAnalysis:
    """
    An analysis as the intake service last described it, with how far a worker
    has come with it while it processes.
    """

    analysis: Analysis

    progress: ProcessingProgress | None


@final
class IntakeClient:
    """
    Talks to one intake service over HTTP.
    """

    def __init__(
        self,
        *,
        base_url: str,
        transport: AsyncBaseTransport | None = None,
    ) -> None:
        self._client: Final = AsyncClient(
            timeout=60.0,
            base_url=base_url,
            transport=transport,
        )

    async def close(
        self,
    ) -> None:
        """
        Release the connection pool.
        """

        await self._client.aclose()

    # NOTE:
    # The method and the path are structural and the body and the query are configuration, which is the split httpx2's
    # own `request(method, url, *, ...)` makes.
    async def _request(
        self,
        method: str,
        path: str,
        /,
        *,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, int] | None = None,
    ) -> Response:
        try:
            response = await self._client.request(
                method=method,
                url=path,
                json=json,
                params=params,
            )

        except HTTPError as exception:
            kind = type(
                exception,
            ).__name__

            hint = (
                _NOT_RUNNING
                if isinstance(
                    exception,
                    ConnectError,
                )
                else ""
            )

            raise IntakeUnavailableError(
                f"the intake service could not be reached for {method} {path} ({kind}){hint}",
            ) from exception

        _raise_for(
            response,
            method,
            path,
        )

        return response

    async def create(
        self,
        *,
        request: AnalysisRequest,
    ) -> AnalysisAccepted:
        """
        Ask for an analysis.
        """

        response = await self._request(
            "POST",
            "/analyses",
            json=request.model_dump(
                mode="json",
                exclude_none=True,
            ),
        )

        return AnalysisAccepted.model_validate_json(
            json_data=response.content,
        )

    async def get(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> Analysis:
        """
        Read an analysis back.
        """

        response = await self._request(
            "GET",
            f"/analyses/{analysis_id}",
        )

        return AnalysisOut.model_validate_json(
            json_data=response.content,
        ).to_domain()

    async def watch(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> WatchedAnalysis:
        """
        Read an analysis back with how far a worker has come with it.
        """

        response = await self._request(
            "GET",
            f"/analyses/{analysis_id}",
        )

        out = AnalysisOut.model_validate_json(
            json_data=response.content,
        )

        return WatchedAnalysis(
            analysis=out.to_domain(),
            progress=out.processing_progress(),
        )

    async def list_recent(
        self,
        *,
        limit: int,
    ) -> tuple[AnalysisSummary, ...]:
        response = await self._request(
            "GET",
            "/analyses",
            params={
                "limit": limit,
            },
        )

        return tuple(
            AnalysisSummaryOut.model_validate(
                obj=entry,
            ).to_domain()
            for entry in response.json()
        )

    async def summarize(
        self,
        *,
        focus_limit: int,
    ) -> VocabularySummary:
        response = await self._request(
            "GET",
            "/vocabulary/summary",
            params={
                "focus_limit": focus_limit,
            },
        )

        return VocabularySummaryOut.model_validate_json(
            json_data=response.content,
        ).to_domain()

    async def requeue(
        self,
    ) -> tuple[AnalysisId, ...]:
        response = await self._request(
            "POST",
            "/analyses/requeue",
        )

        return tuple(
            AnalysisId(
                value=value,
            )
            for value in RequeueResult.model_validate_json(
                json_data=response.content,
            ).analysis_ids
        )


def _raise_for(
    response: Response,
    method: str,
    path: str,
    /,
) -> None:
    if response.status_code < 400:
        return

    # NOTE:
    # A problem the service named is raised as the error it stands for; anything else, such as a validation error
    # FastAPI produced itself, is a rejection of the request as sent.
    try:
        problem = Problem.model_validate_json(
            json_data=response.content,
        )

    except ValueError:
        raise IntakeRejectedError(
            f"intake {method} {path} answered HTTP {response.status_code}",
        ) from None

    raise _ERRORS[problem.type](
        problem.detail,
    )
