"""
What keeps the services reachable from their own host alone: the refusal to
listen beyond it, and the answers to a request that names another host or
comes from another site's page.

None of it is authentication, since a request that names an allowed host and
sends no browser header passes, as curl's does.
"""

from collections.abc import (
    Awaitable,
    Callable,
)
from collections.abc import (
    Set as AbstractSet,
)
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
)
from logging import (
    getLogger,
)
from re import (
    IGNORECASE,
)
from re import (
    compile as compile_pattern,
)
from typing import (
    Final,
)

from fastapi import (
    FastAPI,
    Request,
    Response,
)
from fastapi.responses import (
    JSONResponse,
)

from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.intake.schemas import (
    Problem,
    ProblemType,
)

_logger: Final = getLogger(
    name="wordwinnow.exposure",
)

# NOTE:
# The names a browser sends in `Host` for this machine's own loopback interface, which a service always answers to.
_LOOPBACK_NAMES: Final = frozenset(
    (
        "localhost",
        "127.0.0.1",
        "[::1]",
    ),
)

_LOOPBACK_V4: Final = IPv4Network(
    address="127.0.0.0/8",
)

_LOOPBACK_V6: Final = IPv6Address(
    address="::1",
)

# NOTE:
# A host name, an IPv4 address, or a bracketed IPv6 address, then an optional port: nothing else is a name a browser
# sends, so a `Host` that does not read this way, a user and an at sign among others, is refused as it stands.
_HOST: Final = compile_pattern(
    pattern=r"(?P<name>\[[0-9a-f:.]+\]|[^\s:/?#@\[\]]*)(?::[0-9]*)?",
    flags=IGNORECASE,
)

# NOTE:
# The values of `Sec-Fetch-Site` a browser sends for an address typed by the user and for a page the service served.
_NOT_ANOTHER_SITE: Final = frozenset(
    (
        "none",
        "same-origin",
    ),
)


def is_loopback(
    *,
    address: IPv4Address | IPv6Address,
) -> bool:
    """
    Whether `address` is on this host's loopback interface: any address of
    127.0.0.0/8, or `::1`.
    """

    return address in _LOOPBACK_V4 or address == _LOOPBACK_V6


def require_supported_listener(
    *,
    service: str,
    settings: Settings,
    port: int,
) -> None:
    """
    End the process with status 1, after logging why, when the settings would
    make `service` listen on `port` beyond this host's loopback interface
    without the Compose file's word that the process is one of its stack's
    services.

    Nothing is opened before it returns, and a supported listener returns
    without a word.
    """

    address = settings.listen_address

    if settings.compose_stack or is_loopback(
        address=address,
    ):
        return

    shown = (
        f"[{address}]"
        if address.version == 6
        else str(
            object=address,
        )
    )

    _logger.error(
        msg="listener.refused",
        extra={
            "service": service,
            "address": str(
                object=address,
            ),
            "port": port,
            "detail": (
                f"{service} refuses to listen on {shown}:{port}, beyond this host's loopback interface, because it "
                "authenticates no request: whatever reached that address could use it as this host's user would. "
                "Wordwinnow supports that only inside its Compose stack, which publishes its ports on 127.0.0.1 "
                "alone; any other exposure is unsupported and unsafe. Unset WORDWINNOW_LISTEN_ADDRESS to listen on "
                '127.0.0.1 alone; docs/operating.md, "Security", says why.'
            ),
        },
    )

    # NOTE:
    # The commands turn the errors an operator can act on into one line and status 1 themselves; this refusal comes
    # before any of their work, and the log line above is the one line, so the process ends here rather than in a
    # traceback.
    raise SystemExit(
        1,
    )


def install_exposure_middleware(
    *,
    app: FastAPI,
    service: str,
    allowed_hosts: AbstractSet[str],
) -> None:
    """
    Refuse, on every route of `app`, a request whose `Host` is no name of this
    host's loopback interface and none of `allowed_hosts`, and then a request
    that another site's page sent, as its `Sec-Fetch-Site` or its `Origin`
    says.

    The first is answered HTTP 400 with a `Problem` of type
    `host_not_allowed`, and the second HTTP 403 with a detail alone.
    """

    names = _LOOPBACK_NAMES | frozenset(name.lower() for name in allowed_hosts)

    # WARN:
    # Starlette calls an HTTP middleware as `dispatch(request, call_next)`, positionally, so the function declares the
    # positional boundary the framework imposes.
    @app.middleware(
        middleware_type="http",
    )
    async def refuse_other_hosts_and_sites(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
        /,
    ) -> Response:
        host = request.headers.get(
            "host",
            "",
        )

        parsed = _HOST.fullmatch(
            string=host,
        )

        name = host if parsed is None else parsed["name"]

        if parsed is None or name.lower() not in names:
            return JSONResponse(
                content=Problem(
                    type=ProblemType.HOST_NOT_ALLOWED,
                    detail=(
                        f'{service} does not answer to the name "{name}", so that a web page cannot reach it under a '
                        "name of its own; address it as localhost, 127.0.0.1, or [::1]"
                    ),
                ).model_dump(
                    mode="json",
                ),
                status_code=400,
            )

        fetch_site = request.headers.get(
            "sec-fetch-site",
        )

        origin = request.headers.get(
            "origin",
        )

        # NOTE:
        # A browser that sends `Sec-Fetch-Site` says what it knows better than `Origin` does, so `Origin` is read only
        # when the other is absent: an old browser, or a client that is no browser, which sends neither.
        if fetch_site is None:
            another_site = origin is not None and origin != f"http://{host}"

        else:
            another_site = fetch_site not in _NOT_ANOTHER_SITE

        if another_site:
            return JSONResponse(
                content={
                    "detail": f"{service} refuses a request sent by another site's page",
                },
                status_code=403,
            )

        return await call_next(
            request,
        )
