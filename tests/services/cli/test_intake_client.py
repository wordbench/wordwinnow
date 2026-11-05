"""
The client's reading of an intake service that cannot serve a command: which
address it names, and which of the cases it met, through a transport that
raises or answers as a failing service would.
"""

from json import (
    dumps,
)
from typing import (
    Final,
    final,
)
from uuid import (
    UUID,
)

from httpx2 import (
    AsyncBaseTransport,
    ConnectError,
    ConnectTimeout,
    MockTransport,
    PoolTimeout,
    ReadError,
    ReadTimeout,
    RemoteProtocolError,
    Request,
    Response,
    WriteTimeout,
)
from pytest import (
    mark,
    raises,
)

from tests.fakes.ports import (
    EPOCH,
)
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    MessagingUnavailableError,
)
from wordwinnow.domain.analysis import (
    AnalysisOptions,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    InvalidDocumentError,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.services.cli.intake_client import (
    IntakeAddressError,
    IntakeClient,
    IntakeRejectedError,
    IntakeUnavailableError,
)
from wordwinnow.services.intake.schemas import (
    AnalysisOut,
    AnalysisRequest,
    ProblemType,
)

_ADDRESS: Final = "http://intake.invalid:8010"

_ID: Final = AnalysisId(
    value=UUID(
        int=0,
    ),
)

# NOTE:
# The six methods that read an answer, which every one of them reads in the same order.
_METHODS: Final = (
    "create",
    "get",
    "watch",
    "list_recent",
    "summarize",
    "requeue",
)


def _refusal(
    value: str,
    /,
) -> str:
    """
    The line for an address that is no address, as the decided text writes it.
    """

    return f'WORDWINNOW_INTAKE_URL is "{value}", which is not an http:// or https:// address with a host'


def _too_much(
    address: str,
    /,
) -> str:
    """
    The line for an address that has more than a scheme, a host, and a port,
    as the decided text writes it.
    """

    return f"WORDWINNOW_INTAKE_URL has more than a scheme, a host, and a port; set it to the address alone, {address}"


def _raising(
    exception: Exception,
    /,
) -> MockTransport:
    """
    A transport whose every exchange fails with `exception`.
    """

    def handler(
        request: Request,
        /,
    ) -> Response:
        raise exception

    return MockTransport(
        handler=handler,
    )


def _answering(
    response: Response,
    /,
) -> MockTransport:
    """
    A transport whose every exchange ends in `response`.
    """

    def handler(
        request: Request,
        /,
    ) -> Response:
        return response

    return MockTransport(
        handler=handler,
    )


def _recording(
    response: Response,
    seen: list[Request],
    /,
) -> MockTransport:
    """
    A transport whose every exchange ends in `response`, and which keeps each
    request it saw in `seen`.
    """

    def handler(
        request: Request,
        /,
    ) -> Response:
        seen.append(
            request,
        )

        return response

    return MockTransport(
        handler=handler,
    )


async def _asks(
    client: IntakeClient,
    method: str,
    /,
) -> object:
    """
    What `client` returns when it is asked through `method`, with a request
    that has no meaning of its own.
    """

    match method:
        case "create":
            return await client.create(
                request=AnalysisRequest(
                    text="foo",
                    title="foo",
                    reference="foo",
                    level=CefrLevel.A2,
                ),
            )

        case "get":
            return await client.get(
                analysis_id=_ID,
            )

        case "watch":
            return await client.watch(
                analysis_id=_ID,
            )

        case "list_recent":
            return await client.list_recent(
                limit=1,
            )

        case "summarize":
            return await client.summarize(
                focus_limit=1,
            )

        case "requeue":
            return await client.requeue()

        case _:
            raise ValueError(
                f"no such method: {method}",
            )


async def _fails[Failure: Exception](
    transport: AsyncBaseTransport,
    expected: type[Failure],
    /,
    *,
    base_url: str = _ADDRESS,
    method: str = "list_recent",
) -> Failure:
    """
    What the client raises when it asks `transport` through `method`, for the
    listing unless it is told otherwise.
    """

    client = IntakeClient(
        base_url=base_url,
        transport=transport,
    )

    try:
        with raises(
            expected_exception=expected,
        ) as caught:
            await _asks(
                client,
                method,
            )

    finally:
        await client.close()

    return caught.value


@final
class TestTheAddress:
    def test_an_address_without_a_scheme_is_refused_before_any_request(
        self,
    ) -> None:
        seen: list[Request] = []

        def handler(
            request: Request,
            /,
        ) -> Response:
            seen.append(
                request,
            )

            return Response(
                status_code=200,
            )

        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url="localhost:8010",
                transport=MockTransport(
                    handler=handler,
                ),
            )

        assert not isinstance(
            caught.value,
            IntakeUnavailableError,
        )

        assert str(
            object=caught.value,
        ) == _refusal(
            "localhost:8010",
        )

        assert seen == []

    @mark.parametrize(
        argnames="value",
        argvalues=(
            "not a url",
            "http://",
        ),
    )
    def test_a_value_that_is_no_address_is_refused_the_same_way(
        self,
        *,
        value: str,
    ) -> None:
        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url=value,
            )

        assert str(
            object=caught.value,
        ) == _refusal(
            value,
        )

    def test_the_value_shown_hides_what_comes_before_its_at_sign(
        self,
    ) -> None:
        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url="foo:bar@localhost:8010",
            )

        message = str(
            object=caught.value,
        )

        assert message == _refusal(
            "…@localhost:8010",
        )

        assert "foo" not in message

        assert "bar" not in message

    # NOTE:
    # `httpx2` raises its own error for an address it cannot read at all, such as a port that is no number, and that
    # error is not one a command prints as a line.
    def test_an_address_httpx2_cannot_read_is_refused_the_same_way(
        self,
    ) -> None:
        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url="http://intake.invalid:baz",
            )

        assert str(
            object=caught.value,
        ) == _refusal(
            "http://intake.invalid:baz",
        )

    @mark.parametrize(
        argnames="value",
        argvalues=(
            "ftp://intake.invalid:8010",
            "ws://intake.invalid:8010",
        ),
    )
    def test_a_scheme_other_than_http_or_https_is_refused_before_any_request(
        self,
        *,
        value: str,
    ) -> None:
        seen: list[Request] = []

        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url=value,
                transport=_recording(
                    Response(
                        status_code=200,
                    ),
                    seen,
                ),
            )

        assert not isinstance(
            caught.value,
            IntakeUnavailableError,
        )

        assert str(
            object=caught.value,
        ) == _refusal(
            value,
        )

        assert seen == []

    @mark.parametrize(
        argnames="value",
        argvalues=(
            "http://intake.invalid:8010/foo",
            "http://intake.invalid:8010//",
            "http://intake.invalid:8010/?",
            "http://intake.invalid:8010?qux=1",
            "http://intake.invalid:8010#",
            "http://intake.invalid:8010#baz",
            "http://foo@intake.invalid:8010",
            "http://foo:bar@intake.invalid:8010",
            "http://@intake.invalid:8010",
        ),
    )
    def test_an_address_with_more_than_a_scheme_a_host_and_a_port_is_refused_before_any_request(
        self,
        *,
        value: str,
    ) -> None:
        seen: list[Request] = []

        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url=value,
                transport=_recording(
                    Response(
                        status_code=200,
                    ),
                    seen,
                ),
            )

        assert not isinstance(
            caught.value,
            IntakeUnavailableError,
        )

        assert str(
            object=caught.value,
        ) == _too_much(
            _ADDRESS,
        )

        assert seen == []

    def test_the_address_shown_has_no_user_password_path_or_query(
        self,
    ) -> None:
        with raises(
            expected_exception=IntakeAddressError,
        ) as caught:
            IntakeClient(
                base_url="http://foo:bar@intake.invalid:8010/baz?qux=1",
            )

        message = str(
            object=caught.value,
        )

        assert message == _too_much(
            _ADDRESS,
        )

        for hidden in (
            "foo",
            "bar",
            "baz",
            "qux",
        ):
            assert hidden not in message

        with raises(
            expected_exception=IntakeAddressError,
        ) as ipv6:
            IntakeClient(
                base_url="http://[::1]:8010/foo",
            )

        assert str(
            object=ipv6.value,
        ) == _too_much(
            "http://[::1]:8010",
        )

    @mark.parametrize(
        argnames="value",
        argvalues=(
            _ADDRESS,
            f"{_ADDRESS}/",
        ),
    )
    async def test_an_address_alone_is_accepted_with_or_without_a_final_slash(
        self,
        *,
        value: str,
    ) -> None:
        seen: list[Request] = []

        client = IntakeClient(
            base_url=value,
            transport=_recording(
                Response(
                    status_code=200,
                    json=[],
                ),
                seen,
            ),
        )

        try:
            await client.list_recent(
                limit=1,
            )

        finally:
            await client.close()

        assert [
            (
                request.method,
                request.url.raw_path,
            )
            for request in seen
        ] == [
            (
                "GET",
                b"/analyses?limit=1",
            ),
        ]

    async def test_an_ipv6_host_is_shown_in_brackets(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                ConnectError(
                    message="foo",
                ),
            ),
            IntakeUnavailableError,
            base_url="http://[::1]:8010",
        )

        assert str(
            object=error,
        ).startswith(
            "no intake service answered at http://[::1]:8010 (foo); ",
        )


@final
class TestAnExchangeThatFailed:
    async def test_no_connection_names_the_address_and_the_systems_words(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                ConnectError(
                    message="All connection attempts failed",
                ),
            ),
            IntakeUnavailableError,
        )

        assert str(
            object=error,
        ).startswith(
            "no intake service answered at http://intake.invalid:8010 (All connection attempts failed); ",
        )

    async def test_a_connection_that_times_out_is_no_connection(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                ConnectTimeout(
                    message="foo",
                ),
            ),
            IntakeUnavailableError,
        )

        assert str(
            object=error,
        ).startswith(
            "no intake service answered at http://intake.invalid:8010 (foo); ",
        )

    @mark.parametrize(
        argnames="timeout",
        argvalues=(
            ReadTimeout,
            WriteTimeout,
            PoolTimeout,
        ),
    )
    async def test_no_answer_within_the_timeout_says_so(
        self,
        *,
        timeout: type[ReadTimeout | WriteTimeout | PoolTimeout],
    ) -> None:
        error = await _fails(
            _raising(
                timeout(
                    message="foo",
                ),
            ),
            IntakeUnavailableError,
        )

        assert (
            str(
                object=error,
            )
            == "the intake service at http://intake.invalid:8010 took the connection but did not answer within 60 s"
        )

    async def test_an_exchange_broken_off_names_the_systems_words(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                RemoteProtocolError(
                    message="foo",
                ),
            ),
            IntakeUnavailableError,
        )

        assert (
            str(
                object=error,
            )
            == "the intake service at http://intake.invalid:8010 broke off without an answer (foo)"
        )

    async def test_a_failure_with_no_words_is_named_by_its_class(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                ReadError(
                    message="",
                ),
            ),
            IntakeUnavailableError,
        )

        assert (
            str(
                object=error,
            )
            == "the intake service at http://intake.invalid:8010 broke off without an answer (ReadError)"
        )

    async def test_a_client_built_without_a_command_names_this_command(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                ConnectError(
                    message="foo",
                ),
            ),
            IntakeUnavailableError,
        )

        assert (
            str(
                object=error,
            )
            == "no intake service answered at http://intake.invalid:8010 (foo); this command needs the distributed "
            "mode's services, started from a clone of github.com/wordbench/wordwinnow as its getting-started guide "
            "describes and reached at WORDWINNOW_INTAKE_URL"
        )


@final
class TestAnAnswerThatIsNoService:
    @mark.parametrize(
        argnames=(
            "status_code",
            "content_type",
            "body",
            "shown",
        ),
        argvalues=(
            (
                501,
                "text/html; charset=utf-8",
                "<html><body>Unsupported method</body></html>",
                "text/html",
            ),
            (
                404,
                "text/html",
                "<html><body>Error response</body></html>",
                "text/html",
            ),
            (
                405,
                "application/json",
                '{"detail": "Method Not Allowed"}',
                "application/json",
            ),
        ),
    )
    async def test_a_server_that_is_not_intake_is_named_as_such(
        self,
        *,
        status_code: int,
        content_type: str,
        body: str,
        shown: str,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=status_code,
                    headers={
                        "content-type": content_type,
                    },
                    text=body,
                ),
            ),
            IntakeRejectedError,
        )

        assert str(
            object=error,
        ) == (
            "something answered at http://intake.invalid:8010, but not as a Wordwinnow intake service "
            f"(HTTP {status_code}, {shown})"
        )

    async def test_a_response_without_a_content_type_says_so(
        self,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=404,
                    content=b"foo",
                ),
            ),
            IntakeRejectedError,
        )

        assert str(
            object=error,
        ) == (
            "something answered at http://intake.invalid:8010, but not as a Wordwinnow intake service "
            "(HTTP 404, no content type)"
        )

    async def test_a_failure_without_a_problem_names_the_service_not_intake(
        self,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=500,
                    headers={
                        "content-type": "text/plain; charset=utf-8",
                    },
                    text="Internal Server Error",
                ),
            ),
            IntakeRejectedError,
        )

        assert (
            str(
                object=error,
            )
            == "the service at http://intake.invalid:8010 failed (HTTP 500); if it is the intake service, its log "
            "says why"
        )

    async def test_a_problem_intake_names_is_raised_as_before(
        self,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=503,
                    json={
                        "type": "messaging_unavailable",
                        "detail": "foo",
                    },
                ),
            ),
            MessagingUnavailableError,
        )

        assert error.args == ("foo",)

    # NOTE:
    # Intake answers a missing analysis with a 404, which is also what another server answers for a route it lacks, so
    # only the problem in the body tells the two apart, and it is read first.
    async def test_a_problem_intake_names_is_not_taken_for_another_server_whatever_its_status(
        self,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=404,
                    json={
                        "type": "analysis_not_found",
                        "detail": "foo",
                    },
                ),
            ),
            AnalysisNotFoundError,
        )

        assert error.args == ("foo",)

    async def test_a_rejection_without_a_problem_reads_as_before(
        self,
    ) -> None:
        client = IntakeClient(
            base_url=_ADDRESS,
            transport=_answering(
                Response(
                    status_code=422,
                    json={
                        "detail": [
                            {
                                "type": "missing",
                                "loc": [
                                    "body",
                                    "level",
                                ],
                                "msg": "Field required",
                                "input": {},
                            },
                        ],
                    },
                ),
            ),
        )

        try:
            with raises(
                expected_exception=IntakeRejectedError,
            ) as caught:
                await client.create(
                    request=AnalysisRequest(
                        text="foo",
                        title="foo",
                        reference="foo",
                        level=CefrLevel.A2,
                    ),
                )

        finally:
            await client.close()

        assert (
            str(
                object=caught.value,
            )
            == "intake POST /analyses answered HTTP 422"
        )


@final
class TestAnAnswerTheCommandCannotUse:
    @mark.parametrize(
        argnames="method",
        argvalues=_METHODS,
    )
    @mark.parametrize(
        argnames=(
            "status_code",
            "headers",
            "content",
            "shown",
        ),
        argvalues=(
            (
                200,
                {
                    "content-type": "text/html; charset=utf-8",
                },
                b"<!doctype html><title>foo</title>",
                "text/html",
            ),
            (
                204,
                {},
                b"",
                "no content type",
            ),
            (
                200,
                {
                    "content-type": "application/json",
                },
                b"",
                "application/json",
            ),
        ),
    )
    async def test_an_answer_below_300_that_is_not_json_is_another_server(
        self,
        *,
        method: str,
        status_code: int,
        headers: dict[str, str],
        content: bytes,
        shown: str,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=status_code,
                    headers=headers,
                    content=content,
                ),
            ),
            IntakeRejectedError,
            method=method,
        )

        assert str(
            object=error,
        ) == (
            "something answered at http://intake.invalid:8010, but not as a Wordwinnow intake service "
            f"(HTTP {status_code}, {shown})"
        )

    @mark.parametrize(
        argnames="method",
        argvalues=(
            "create",
            "list_recent",
        ),
    )
    @mark.parametrize(
        argnames=(
            "status_code",
            "headers",
            "content",
            "shown",
        ),
        argvalues=(
            (
                301,
                {},
                b"",
                "no content type",
            ),
            (
                302,
                {},
                b"",
                "no content type",
            ),
            (
                307,
                {},
                b"",
                "no content type",
            ),
            (
                308,
                {},
                b"",
                "no content type",
            ),
            (
                307,
                {
                    "content-type": "application/json",
                },
                b"[]",
                "application/json",
            ),
        ),
    )
    async def test_a_redirect_is_another_server_and_is_never_followed(
        self,
        *,
        method: str,
        status_code: int,
        headers: dict[str, str],
        content: bytes,
        shown: str,
    ) -> None:
        seen: list[Request] = []

        error = await _fails(
            _recording(
                Response(
                    status_code=status_code,
                    headers={
                        "location": "http://intake.invalid:8011/",
                        **headers,
                    },
                    content=content,
                ),
                seen,
            ),
            IntakeRejectedError,
            method=method,
        )

        assert str(
            object=error,
        ) == (
            "something answered at http://intake.invalid:8010, but not as a Wordwinnow intake service "
            f"(HTTP {status_code}, {shown})"
        )

        assert (
            len(
                seen,
            )
            == 1
        )

    @mark.parametrize(
        argnames="method",
        argvalues=_METHODS,
    )
    @mark.parametrize(
        argnames="content",
        argvalues=(
            b"42",
            b"null",
            b'{"foo": "bar"}',
            b'[{"foo": "bar"}]',
        ),
    )
    async def test_json_the_command_cannot_read_may_be_another_version(
        self,
        *,
        method: str,
        content: bytes,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=200,
                    headers={
                        "content-type": "application/json",
                    },
                    content=content,
                ),
            ),
            IntakeRejectedError,
            method=method,
        )

        assert str(
            object=error,
        ) == (
            "the service at http://intake.invalid:8010 answered HTTP 200 with JSON this command cannot read; "
            "if it is the intake service, its version may not match this command's"
        )

    # NOTE:
    # The schema accepts a document of blank text and the domain refuses it, so an answer of intake's own shape can
    # still be one the command cannot rebuild into its objects.
    @mark.parametrize(
        argnames="method",
        argvalues=(
            "get",
            "watch",
        ),
    )
    async def test_an_answer_the_command_cannot_rebuild_is_json_it_cannot_read(
        self,
        *,
        method: str,
    ) -> None:
        wire = AnalysisOut.from_domain(
            analysis=request_an_analysis(
                document=Document(
                    title="foo",
                    text="foo",
                    origin=DocumentOrigin.CUSTOM_TEXT,
                    reference="foo",
                ),
                profile=LearnerProfile(
                    level=CefrLevel.B1,
                    target_level=CefrLevel.B2,
                ),
                options=AnalysisOptions(),
                at=EPOCH,
            ),
        ).model_dump(
            mode="json",
        )

        wire["document"]["text"] = "   "

        content = dumps(
            obj=wire,
        ).encode()

        with raises(
            expected_exception=InvalidDocumentError,
        ):
            AnalysisOut.model_validate_json(
                json_data=content,
            ).to_domain()

        error = await _fails(
            _answering(
                Response(
                    status_code=200,
                    headers={
                        "content-type": "application/json",
                    },
                    content=content,
                ),
            ),
            IntakeRejectedError,
            method=method,
        )

        assert str(
            object=error,
        ) == (
            "the service at http://intake.invalid:8010 answered HTTP 200 with JSON this command cannot read; "
            "if it is the intake service, its version may not match this command's"
        )


@final
class TestEveryProblemType:
    @mark.parametrize(
        argnames="problem",
        argvalues=tuple(
            ProblemType,
        ),
    )
    async def test_each_stands_for_an_error_that_carries_the_detail_and_none_is_a_key_error(
        self,
        *,
        problem: ProblemType,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=400,
                    json={
                        "type": problem,
                        "detail": "foo",
                    },
                ),
            ),
            Exception,
        )

        assert not isinstance(
            error,
            KeyError,
        )

        assert error.args == ("foo",)

    async def test_an_address_the_service_does_not_answer_to_is_a_rejection(
        self,
    ) -> None:
        error = await _fails(
            _answering(
                Response(
                    status_code=400,
                    json={
                        "type": "host_not_allowed",
                        "detail": "foo",
                    },
                ),
            ),
            IntakeRejectedError,
        )

        assert error.args == ("foo",)


@final
class TestWhatTheClientSends:
    # NOTE:
    # Intake refuses a request that another site's page sent, as its `Origin` or its `Sec-Fetch-Site` says, so the
    # command's client must send neither, on the read and on the one write that needs no body.
    async def test_a_request_carries_no_header_only_a_browser_sends(
        self,
    ) -> None:
        seen: list[Request] = []

        def handler(
            request: Request,
            /,
        ) -> Response:
            seen.append(
                request,
            )

            return Response(
                status_code=200,
                json={
                    "analysis_ids": [],
                }
                if request.method == "POST"
                else [],
            )

        client = IntakeClient(
            base_url=_ADDRESS,
            transport=MockTransport(
                handler=handler,
            ),
        )

        try:
            await client.list_recent(
                limit=1,
            )

            await client.requeue()

        finally:
            await client.close()

        assert tuple(request.method for request in seen) == (
            "GET",
            "POST",
        )

        for request in seen:
            assert (
                tuple(
                    name
                    for name in request.headers
                    if name.lower() == "origin"
                    or name.lower().startswith(
                        "sec-fetch-",
                    )
                )
                == ()
            )
