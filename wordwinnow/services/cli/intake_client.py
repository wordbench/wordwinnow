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
    URL,
    AsyncBaseTransport,
    AsyncClient,
    ConnectError,
    ConnectTimeout,
    HTTPError,
    InvalidURL,
    PoolTimeout,
    ReadTimeout,
    Response,
    WriteTimeout,
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
    Raised when the intake service answered with an error that no application
    error stands for: one it did not name as a problem, such as a validation
    failure of the request itself, or one it named that only a remote command
    can meet, such as an address it does not answer to.

    What answered may not be the intake service, which the message says when
    the answer shows it.

    Not retryable with the same request.
    """


@final
class IntakeAddressError(
    RuntimeError,
):
    """
    Raised when the configured address of the intake service is not an http://
    or https:// address with a host.

    Not retryable: an address does not mend by waiting.
    """

    def __init__(
        self,
        *,
        value: str,
    ) -> None:
        (
            _,
            at,
            tail,
        ) = value.rpartition(
            "@",
        )

        # NOTE:
        # What comes before the last @ is where an address keeps its user and its password, so it never reaches a
        # message that a terminal, a log, or a screenshot may carry.
        shown = f"…@{tail}" if at else value

        super().__init__(
            f'WORDWINNOW_INTAKE_URL is "{shown}", which is not an http:// or https:// address with a host',
        )


# NOTE:
# The error each problem type stands for, so the caller sees the same class in both modes wherever the local mode has
# one; the address a command used is no part of the local mode, so `host_not_allowed` stands for the rejection.
_ERRORS: Final = MappingProxyType(
    mapping={
        ProblemType.ANALYSIS_NOT_FOUND: AnalysisNotFoundError,
        ProblemType.REQUEST_REJECTED: SourceRejectedError,
        ProblemType.SOURCE_NOT_CONFIGURED: SourceNotConfiguredError,
        ProblemType.SOURCE_UNAVAILABLE: SourceUnavailableError,
        ProblemType.ANALYSIS_NOT_PUBLISHED: AnalysisNotPublishedError,
        ProblemType.MESSAGING_UNAVAILABLE: MessagingUnavailableError,
        ProblemType.STORE_UNAVAILABLE: FactStoreUnavailableError,
        ProblemType.HOST_NOT_ALLOWED: IntakeRejectedError,
    },
)

# NOTE:
# Where the distributed mode's services come from, which a learner with only the installed command cannot be left to
# guess: that command has no Makefile and no Compose file, and the services start from a clone's.
_SERVICES: Final = (
    "the distributed mode's services, started from a clone of github.com/wordbench/wordwinnow as its getting-started "
    "guide describes and reached at WORDWINNOW_INTAKE_URL"
)

# NOTE:
# One timeout for every exchange, which the client applies and a message names, so that the two never differ.
_TIMEOUT_SECONDS: Final = 60.0

_SCHEMES: Final = frozenset(
    (
        "http",
        "https",
    ),
)

# NOTE:
# What a server that is not intake answers with: intake's own routes send none of these statuses, and no web page,
# except as a problem they name, so an answer without a named problem that has one of them came from another server.
_NOT_INTAKE_STATUSES: Final = frozenset(
    (
        404,
        405,
        501,
    ),
)

_NOT_INTAKE_MEDIA_TYPE: Final = "text/html"


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

    A service that does not answer is reported with `needed_by`, what needs
    the distributed mode's services, and, when there is something, with
    `otherwise`, what a learner can do instead.

    Raises `IntakeAddressError` when `base_url` is not an http:// or https://
    address with a host.
    """

    def __init__(
        self,
        *,
        base_url: str,
        transport: AsyncBaseTransport | None = None,
        needed_by: str = "this command",
        otherwise: str | None = None,
    ) -> None:
        url = _parsed(
            base_url,
        )

        tail = "" if otherwise is None else f"; {otherwise}"

        self._address: Final = _address(
            url,
        )

        self._needs: Final = f"{needed_by} needs {_SERVICES}{tail}"

        self._client: Final = AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            base_url=url,
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
            raise IntakeUnavailableError(
                self._unavailable(
                    exception,
                ),
            ) from exception

        _raise_for(
            response,
            method,
            path,
            self._address,
        )

        return response

    def _unavailable(
        self,
        exception: HTTPError,
        /,
    ) -> str:
        """
        What is said of an exchange that failed before the service answered:
        no connection, a connection that gave no answer, or one that broke
        off.
        """

        text = str(
            object=exception,
        )

        kind = type(
            exception,
        ).__name__

        words = text or kind

        if isinstance(
            exception,
            (
                ConnectError,
                ConnectTimeout,
            ),
        ):
            return f"no intake service answered at {self._address} ({words}); {self._needs}"

        if isinstance(
            exception,
            (
                ReadTimeout,
                WriteTimeout,
                PoolTimeout,
            ),
        ):
            return (
                f"the intake service at {self._address} took the connection but did not answer within "
                f"{_TIMEOUT_SECONDS:.0f} s"
            )

        return f"the intake service at {self._address} broke off without an answer ({words})"

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


def _parsed(
    base_url: str,
    /,
) -> URL:
    """
    The address as the client uses it, or `IntakeAddressError` when it is not
    an http:// or https:// address with a host.
    """

    try:
        url = URL(
            url=base_url,
        )

    except InvalidURL as exception:
        raise IntakeAddressError(
            value=base_url,
        ) from exception

    if url.scheme not in _SCHEMES or not url.host:
        raise IntakeAddressError(
            value=base_url,
        )

    return url


def _address(
    url: URL,
    /,
) -> str:
    """
    The scheme, host, and port of `url`, which is what a message names of the
    service.
    """

    # NOTE:
    # Only where the client connects, never the user, the password, the path, the query, or the fragment, which may
    # hold a secret that a terminal, a log, or a screenshot must not carry.
    #
    # An IPv6 host is written in brackets, as it is in an address.
    host = f"[{url.host}]" if ":" in url.host else url.host

    port = "" if url.port is None else f":{url.port}"

    return f"{url.scheme}://{host}{port}"


def _media_type(
    response: Response,
    /,
) -> str:
    """
    The response's `Content-Type` without its parameters, in lower case, or an
    empty string when it has none.
    """

    content_type = response.headers.get(
        key="content-type",
        default="",
    )

    return (
        content_type.split(
            sep=";",
            maxsplit=1,
        )[0]
        .strip()
        .lower()
    )


def _rejection(
    response: Response,
    method: str,
    path: str,
    address: str,
    /,
) -> IntakeRejectedError:
    """
    The error for a failing answer that names no problem: that of another
    server, that of a service that failed, or that of a request refused.
    """

    status = response.status_code

    media_type = _media_type(
        response,
    )

    if status in _NOT_INTAKE_STATUSES or media_type == _NOT_INTAKE_MEDIA_TYPE:
        return IntakeRejectedError(
            f"something answered at {address}, but not as a Wordwinnow intake service (HTTP {status}, "
            f"{media_type or 'no content type'})",
        )

    if status >= 500:
        return IntakeRejectedError(
            f"the service at {address} failed (HTTP {status}); if it is the intake service, its log says why",
        )

    return IntakeRejectedError(
        f"intake {method} {path} answered HTTP {status}",
    )


def _raise_for(
    response: Response,
    method: str,
    path: str,
    address: str,
    /,
) -> None:
    if response.status_code < 400:
        return

    # NOTE:
    # A problem the service named is raised as the error it stands for, whatever its status; anything else is a
    # rejection, of the request as sent when a validation error FastAPI produced itself says so, and of the address
    # when the status or the media type says that another server answered.
    try:
        problem = Problem.model_validate_json(
            json_data=response.content,
        )

    except ValueError:
        raise _rejection(
            response,
            method,
            path,
            address,
        ) from None

    raise _ERRORS[problem.type](
        problem.detail,
    )
