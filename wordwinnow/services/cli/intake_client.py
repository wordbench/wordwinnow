"""
The CLI's view of the intake service.

Every answer comes back as domain objects, and every refusal comes back as the
application error the service named in its problem type, so a command handles
a remote refusal the way it handles a local one.
"""

from collections.abc import (
    Callable,
    Mapping,
    Sequence,
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
    PoolTimeout,
    ReadTimeout,
    Response,
    WriteTimeout,
)
from pydantic import (
    TypeAdapter,
    ValidationError,
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
from wordwinnow.services.cli.addresses import (
    name_address,
    parse_address,
    refusal,
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

    It is raised too for an answer that the command cannot use: a redirect, a
    body that is not JSON, or JSON that the command cannot read.

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
    or https:// address with a host, or has more than a scheme, a host, and a
    port.

    Given `alone`, the address of a value that has more, the message says to
    set the setting to it.

    Not retryable: an address does not mend by waiting.
    """

    def __init__(
        self,
        *,
        value: str,
        alone: str | None = None,
    ) -> None:
        super().__init__(
            refusal(
                setting=_SETTING,
                value=value,
            )
            if alone is None
            else f"{_SETTING} has more than a scheme, a host, and a port; set it to the address alone, {alone}",
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

# NOTE:
# The setting the client's address comes from, which a message names so that a learner knows what to change.
_SETTING: Final = "WORDWINNOW_INTAKE_URL"

# NOTE:
# Each of these in a value is more than a scheme, a host, and a port, wherever it stands: httpx2 keeps an empty `?` in
# the path it sends, so that the request goes to `/?limit=1`, and drops an empty `#` and an empty `@`, so one rule on
# the value as written keeps the refusal's line true of every value it refuses.
_MORE_THAN_AN_ADDRESS: Final = (
    "?",
    "#",
    "@",
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

# NOTE:
# Each answer's body is read as one value, so that a scalar, a null, or an object where a list belongs is JSON the
# command cannot read, and no entry of a list is read by itself before the rest is known to be a list.
_ACCEPTED: Final = TypeAdapter(
    type=AnalysisAccepted,
)

_ANALYSIS: Final = TypeAdapter(
    type=AnalysisOut,
)

_LISTING: Final = TypeAdapter(
    type=list[AnalysisSummaryOut],
)

_SUMMARY: Final = TypeAdapter(
    type=VocabularySummaryOut,
)

_REQUEUED: Final = TypeAdapter(
    type=RequeueResult,
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

    A service that does not answer is reported with `needed_by`, what needs
    the distributed mode's services, and, when there is something, with
    `otherwise`, what a learner can do instead.

    Raises `IntakeAddressError` when `base_url` is not an http:// or https://
    address with a host, or has more than a scheme, a host, and a port.
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

        self._address: Final = name_address(
            url=url,
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
    # own `request(method, url, *, ...)` makes; what the answer is read as and rebuilt into are structural too, and
    # every method passes through here, so none can read an answer without the order `_raise_for` and `_read` keep.
    async def _request[Wire, Answer](
        self,
        method: str,
        path: str,
        wire: TypeAdapter[Wire],
        build: Callable[[Wire], Answer],
        /,
        *,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, int] | None = None,
    ) -> Answer:
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

        return _read(
            response,
            self._address,
            wire,
            build,
        )

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

        return await self._request(
            "POST",
            "/analyses",
            _ACCEPTED,
            _unchanged,
            json=request.model_dump(
                mode="json",
                exclude_none=True,
            ),
        )

    async def get(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> Analysis:
        """
        Read an analysis back.
        """

        return await self._request(
            "GET",
            f"/analyses/{analysis_id}",
            _ANALYSIS,
            AnalysisOut.to_domain,
        )

    async def watch(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> WatchedAnalysis:
        """
        Read an analysis back with how far a worker has come with it.
        """

        return await self._request(
            "GET",
            f"/analyses/{analysis_id}",
            _ANALYSIS,
            _watched,
        )

    async def list_recent(
        self,
        *,
        limit: int,
    ) -> tuple[AnalysisSummary, ...]:
        return await self._request(
            "GET",
            "/analyses",
            _LISTING,
            _listed,
            params={
                "limit": limit,
            },
        )

    async def summarize(
        self,
        *,
        focus_limit: int,
    ) -> VocabularySummary:
        return await self._request(
            "GET",
            "/vocabulary/summary",
            _SUMMARY,
            VocabularySummaryOut.to_domain,
            params={
                "focus_limit": focus_limit,
            },
        )

    async def requeue(
        self,
    ) -> tuple[AnalysisId, ...]:
        return await self._request(
            "POST",
            "/analyses/requeue",
            _REQUEUED,
            _requeued,
        )


def _parsed(
    base_url: str,
    /,
) -> URL:
    """
    The address as the client uses it, or `IntakeAddressError` when it is not
    an http:// or https:// address with a host, or has more than a scheme, a
    host, and a port.
    """

    url = parse_address(
        value=base_url,
    )

    if url is None:
        raise IntakeAddressError(
            value=base_url,
        )

    # NOTE:
    # Intake serves its routes at the root of its address and authenticates no one, so a path is always a mistake, and
    # a user and a password protect nothing; the path is read as httpx2 parses it, which holds `/` for an address
    # alone and `//` for a second slash, and the three marks are read in the value as written.
    if url.path != "/" or any(mark in base_url for mark in _MORE_THAN_AN_ADDRESS):
        raise IntakeAddressError(
            value=base_url,
            alone=name_address(
                url=url,
            ),
        )

    return url


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


def _not_intake(
    response: Response,
    address: str,
    /,
) -> IntakeRejectedError:
    """
    The error for an answer that another server gave, as its status and its
    media type show.
    """

    media_type = _media_type(
        response,
    )

    return IntakeRejectedError(
        f"something answered at {address}, but not as a Wordwinnow intake service (HTTP {response.status_code}, "
        f"{media_type or 'no content type'})",
    )


def _unreadable(
    response: Response,
    address: str,
    /,
) -> IntakeRejectedError:
    """
    The error for an answer that is JSON the command cannot read, which a
    different version of the intake service may have given.
    """

    return IntakeRejectedError(
        f"the service at {address} answered HTTP {response.status_code} with JSON this command cannot read; if it is "
        "the intake service, its version may not match this command's",
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
        return _not_intake(
            response,
            address,
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
    if response.status_code < 300:
        return

    # NOTE:
    # A redirect is never followed, as httpx2's default already has it, because a 307 or a 308 would send the body of
    # a request, a learner's text among them, wherever the redirect points; intake gives no command a redirect, so one
    # came from another server, whatever it sends as the body.
    if response.status_code < 400:
        raise _not_intake(
            response,
            address,
        )

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


def _read[Wire, Answer](
    response: Response,
    address: str,
    wire: TypeAdapter[Wire],
    build: Callable[[Wire], Answer],
    /,
) -> Answer:
    """
    What an answer below 300 says, read as `wire` and rebuilt by `build`: the
    error for another server when the body is not JSON, and the error for JSON
    the command cannot read when the body is not what `wire` describes or
    cannot be rebuilt.
    """

    try:
        value = wire.validate_json(
            response.content,
        )

    except ValidationError as exception:
        # NOTE:
        # Pydantic reports a body that is no JSON, an empty one included, as `json_invalid` and as nothing else, which
        # is what tells it from JSON of another shape, so that the body decides and the media type does not.
        if any(error["type"] == "json_invalid" for error in exception.errors()):
            raise _not_intake(
                response,
                address,
            ) from exception

        raise _unreadable(
            response,
            address,
        ) from exception

    try:
        return build(
            value,
        )

    except ValueError as exception:
        raise _unreadable(
            response,
            address,
        ) from exception


def _unchanged[Answer](
    answer: Answer,
    /,
) -> Answer:
    """
    `answer` as it is, for an answer that the command uses as it was read.
    """

    return answer


def _watched(
    out: AnalysisOut,
    /,
) -> WatchedAnalysis:
    """
    The analysis `out` describes, with how far a worker has come with it.
    """

    return WatchedAnalysis(
        analysis=out.to_domain(),
        progress=out.processing_progress(),
    )


def _listed(
    entries: Sequence[AnalysisSummaryOut],
    /,
) -> tuple[AnalysisSummary, ...]:
    """
    The analyses `entries` describe.
    """

    return tuple(entry.to_domain() for entry in entries)


def _requeued(
    result: RequeueResult,
    /,
) -> tuple[AnalysisId, ...]:
    """
    The analyses `result` says went back to the workers.
    """

    return tuple(
        AnalysisId(
            value=value,
        )
        for value in result.analysis_ids
    )
