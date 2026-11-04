"""
What keeps the services to their own host: which listening addresses are
refused and how, and which requests the middleware answers, on an application
of its own.
"""

from ipaddress import (
    ip_address,
)
from logging import (
    WARNING,
)
from typing import (
    Final,
    final,
)

from fastapi import (
    FastAPI,
)
from httpx2 import (
    ASGITransport,
    AsyncClient,
)
from pytest import (
    LogCaptureFixture,
    mark,
    raises,
)
from starlette.types import (
    Message,
)

from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.exposure import (
    install_exposure_middleware,
    is_loopback,
    require_supported_listener,
)

_PORT: Final = 8010


def _refusal(
    service: str,
    address: str,
    /,
) -> str:
    """
    The decided text of the refusal to listen, `address` as it is written
    before the port.
    """

    return (
        f"{service} refuses to listen on {address}:{_PORT}, beyond this host's loopback interface, because it "
        "authenticates no request: whatever reached that address could use it as this host's user would. Wordwinnow "
        "supports that only inside its Compose stack, which publishes its ports on 127.0.0.1 alone; any other "
        "exposure is unsupported and unsafe. Unset WORDWINNOW_LISTEN_ADDRESS to listen on 127.0.0.1 alone; "
        'docs/operating.md, "Security", says why.'
    )


def _listening_on(
    address: str,
    /,
    *,
    in_the_stack: bool,
) -> Settings:
    return Settings(
        listen_address=address,
        compose_stack=in_the_stack,
    )


@final
class TestLoopback:
    @mark.parametrize(
        argnames="address",
        argvalues=(
            "127.0.0.1",
            "127.0.0.2",
            "127.255.255.254",
            "::1",
        ),
    )
    def test_the_loopback_interface_is_127_0_0_0_8_and_the_one_ipv6_address(
        self,
        *,
        address: str,
    ) -> None:
        assert is_loopback(
            address=ip_address(
                address=address,
            ),
        )

    @mark.parametrize(
        argnames="address",
        argvalues=(
            "0.0.0.0",
            "::",
            "192.0.2.1",
            "128.0.0.1",
            "::ffff:127.0.0.1",
        ),
    )
    def test_every_other_address_is_beyond_it(
        self,
        *,
        address: str,
    ) -> None:
        assert not is_loopback(
            address=ip_address(
                address=address,
            ),
        )


@final
class TestTheListeningAddress:
    @mark.parametrize(
        argnames="in_the_stack",
        argvalues=(
            False,
            True,
        ),
    )
    @mark.parametrize(
        argnames="address",
        argvalues=(
            "127.0.0.1",
            "127.0.0.2",
            "::1",
        ),
    )
    def test_a_loopback_address_listens_and_says_nothing_whatever_the_setting(
        self,
        *,
        address: str,
        in_the_stack: bool,
        caplog: LogCaptureFixture,
    ) -> None:
        with caplog.at_level(
            level=0,
        ):
            require_supported_listener(
                service="intake",
                settings=_listening_on(
                    address,
                    in_the_stack=in_the_stack,
                ),
                port=_PORT,
            )

        assert caplog.records == []

    @mark.parametrize(
        argnames=(
            "address",
            "written",
        ),
        argvalues=(
            (
                "0.0.0.0",
                "0.0.0.0",
            ),
            (
                "::",
                "[::]",
            ),
            (
                "192.0.2.1",
                "192.0.2.1",
            ),
        ),
    )
    def test_an_address_beyond_loopback_is_refused_with_the_decided_text_and_status_1(
        self,
        *,
        address: str,
        written: str,
        caplog: LogCaptureFixture,
    ) -> None:
        with (
            caplog.at_level(
                level=WARNING,
            ),
            raises(
                expected_exception=SystemExit,
            ) as ended,
        ):
            require_supported_listener(
                service="the analysis worker's metrics",
                settings=_listening_on(
                    address,
                    in_the_stack=False,
                ),
                port=_PORT,
            )

        assert ended.value.code == 1

        (record,) = caplog.records

        assert record.levelname == "ERROR"

        assert record.getMessage() == "listener.refused"

        assert (
            vars(
                record,
            )["service"]
            == "the analysis worker's metrics"
        )

        assert vars(
            record,
        )["address"] == str(
            object=ip_address(
                address=address,
            ),
        )

        assert (
            vars(
                record,
            )["port"]
            == _PORT
        )

        assert vars(
            record,
        )["detail"] == _refusal(
            "the analysis worker's metrics",
            written,
        )

    @mark.parametrize(
        argnames="address",
        argvalues=(
            "0.0.0.0",
            "::",
            "192.0.2.1",
        ),
    )
    def test_the_compose_file_that_says_the_process_is_one_of_its_services_lets_it_listen_without_a_word(
        self,
        *,
        address: str,
        caplog: LogCaptureFixture,
    ) -> None:
        with caplog.at_level(
            level=WARNING,
        ):
            require_supported_listener(
                service="intake",
                settings=_listening_on(
                    address,
                    in_the_stack=True,
                ),
                port=_PORT,
            )

        assert caplog.records == []


def _application(
    *,
    allowed_hosts: frozenset[str] = frozenset(),
) -> FastAPI:
    """
    An application of one route, with the middleware and nothing else.
    """

    app = FastAPI()

    install_exposure_middleware(
        app=app,
        service="foo",
        allowed_hosts=allowed_hosts,
    )

    @app.get(
        path="/bar",
    )
    async def bar() -> dict[str, str]:
        return {
            "status": "ok",
        }

    return app


async def _status(
    app: FastAPI,
    headers: dict[str, str],
    /,
) -> int:
    async with AsyncClient(
        base_url="http://127.0.0.1:8010",
        transport=ASGITransport(
            app=app,
        ),
    ) as client:
        return (
            await client.get(
                url="/bar",
                headers=headers,
            )
        ).status_code


@final
class TestTheHostAndTheSite:
    # NOTE:
    # A Host that is no name and a port, a user and an at sign among others, is refused as it stands, because a
    # browser never sends one and a name that happens to begin with an allowed one must not pass for it.
    @mark.parametrize(
        argnames="host",
        argvalues=(
            "localhost:8010@attacker.example",
            "localhost@attacker.example",
            "localhost:port",
            "localhost.attacker.example",
            "localhost/bar",
            "127.0.0.1 attacker.example",
            "[::1",
            "[0:0:0:0:0:0:0:1]",
            ":8010",
        ),
    )
    async def test_a_host_that_is_no_name_of_the_loopback_interface_is_refused(
        self,
        *,
        host: str,
    ) -> None:
        assert (
            await _status(
                _application(),
                {
                    "Host": host,
                },
            )
            == 400
        )

    @mark.parametrize(
        argnames="host",
        argvalues=(
            "localhost",
            "LocalHost:8010",
            "127.0.0.1:8010",
            "[::1]",
            "[::1]:8010",
        ),
    )
    async def test_the_three_names_pass_with_a_port_and_without_one_in_any_case(
        self,
        *,
        host: str,
    ) -> None:
        assert (
            await _status(
                _application(),
                {
                    "Host": host,
                },
            )
            == 200
        )

    async def test_a_name_the_application_is_given_passes_in_any_case_with_or_without_its_port(
        self,
    ) -> None:
        app = _application(
            allowed_hosts=frozenset(
                ("Intake",),
            ),
        )

        for host in (
            "intake",
            "INTAKE:8010",
        ):
            assert (
                await _status(
                    app,
                    {
                        "Host": host,
                    },
                )
                == 200
            )

        assert (
            await _status(
                app,
                {
                    "Host": "enrichment:8100",
                },
            )
            == 400
        )

    async def test_a_name_it_is_given_never_removes_one_of_the_three(
        self,
    ) -> None:
        assert (
            await _status(
                _application(
                    allowed_hosts=frozenset(
                        ("intake",),
                    ),
                ),
                {
                    "Host": "localhost:8010",
                },
            )
            == 200
        )

    async def test_a_request_with_no_host_at_all_is_refused_as_an_empty_one_is(
        self,
    ) -> None:
        sent: list[Message] = []

        async def receive() -> Message:
            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }

        # WARN:
        # The application calls `send(message)` positionally, as the ASGI protocol does, so `send` declares the
        # positional boundary the protocol imposes.
        async def send(
            message: Message,
            /,
        ) -> None:
            sent.append(
                message,
            )

        await _application()(
            scope={
                "type": "http",
                "asgi": {
                    "version": "3.0",
                },
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/bar",
                "raw_path": b"/bar",
                "query_string": b"",
                "headers": [],
                "client": (
                    "127.0.0.1",
                    1,
                ),
                "server": (
                    "127.0.0.1",
                    _PORT,
                ),
            },
            receive=receive,
            send=send,
        )

        (start,) = (message for message in sent if message["type"] == "http.response.start")

        assert start["status"] == 400

        body = b"".join(message["body"] for message in sent if message["type"] == "http.response.body")

        assert b'does not answer to the name \\"\\"' in body

    async def test_the_site_is_judged_by_sec_fetch_site_before_origin_is_read(
        self,
    ) -> None:
        app = _application()

        assert (
            await _status(
                app,
                {
                    "Sec-Fetch-Site": "same-origin",
                    "Origin": "https://example.com",
                },
            )
            == 200
        )

        assert (
            await _status(
                app,
                {
                    "Sec-Fetch-Site": "cross-site",
                    "Origin": "http://127.0.0.1:8010",
                },
            )
            == 403
        )

    # NOTE:
    # A value the browsers never send is refused as another site's, so that a header written in another case or a
    # value invented for the purpose passes for nothing.
    @mark.parametrize(
        argnames="value",
        argvalues=(
            "NONE",
            "Same-Origin",
            "",
            "foo",
        ),
    )
    async def test_a_sec_fetch_site_no_browser_sends_is_another_site(
        self,
        *,
        value: str,
    ) -> None:
        assert (
            await _status(
                _application(),
                {
                    "Sec-Fetch-Site": value,
                },
            )
            == 403
        )

    @mark.parametrize(
        argnames="origin",
        argvalues=(
            "",
            "https://127.0.0.1:8010",
            "http://127.0.0.1",
            "http://127.0.0.1:8010/",
        ),
    )
    async def test_an_origin_that_is_not_http_and_the_hosts_own_name_is_another_site(
        self,
        *,
        origin: str,
    ) -> None:
        assert (
            await _status(
                _application(),
                {
                    "Origin": origin,
                },
            )
            == 403
        )
