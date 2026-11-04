"""
The client's reading of an intake service that cannot serve a command: which
address it names, and which of the cases it met, through a transport that
raises or answers as a failing service would.
"""

from typing import (
    Final,
    final,
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

from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    MessagingUnavailableError,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.services.cli.intake_client import (
    IntakeAddressError,
    IntakeClient,
    IntakeRejectedError,
    IntakeUnavailableError,
)
from wordwinnow.services.intake.schemas import (
    AnalysisRequest,
)

_ADDRESS: Final = "http://intake.invalid:8010"


def _refusal(
    value: str,
    /,
) -> str:
    """
    The line for an address that is no address, as the decided text writes it.
    """

    return f'WORDWINNOW_INTAKE_URL is "{value}", which is not an http:// or https:// address with a host'


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


async def _fails[Failure: Exception](
    transport: AsyncBaseTransport,
    expected: type[Failure],
    /,
    *,
    base_url: str = _ADDRESS,
) -> Failure:
    """
    What the client raises when it asks `transport` for the listing.
    """

    client = IntakeClient(
        base_url=base_url,
        transport=transport,
    )

    try:
        with raises(
            expected_exception=expected,
        ) as caught:
            await client.list_recent(
                limit=1,
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

    async def test_the_address_shown_has_no_user_password_path_or_query(
        self,
    ) -> None:
        error = await _fails(
            _raising(
                ConnectError(
                    message="All connection attempts failed",
                ),
            ),
            IntakeUnavailableError,
            base_url="http://foo:bar@intake.invalid:8010/baz?qux=1",
        )

        message = str(
            object=error,
        )

        assert "http://intake.invalid:8010" in message

        for hidden in (
            "foo",
            "bar",
            "baz",
            "qux",
        ):
            assert hidden not in message

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
