"""
How the command line reads and names the address of a service.

The intake client and the enrichment client share these rules, so that a
message of either names an address in the same words.
"""

from typing import (
    Final,
)

from httpx2 import (
    URL,
    InvalidURL,
)

_SCHEMES: Final = frozenset(
    (
        "http",
        "https",
    ),
)


def parse_address(
    *,
    value: str,
) -> URL | None:
    """
    The address as a client uses it, or `None` when `value` is not an http://
    or https:// address with a host.
    """

    try:
        url = URL(
            url=value,
        )

    except InvalidURL:
        return None

    if url.scheme not in _SCHEMES or not url.host:
        return None

    return url


def name_address(
    *,
    url: URL,
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


def show_value(
    *,
    value: str,
) -> str:
    """
    A configured value as a message may carry it: what comes before its last
    `@` is replaced by an ellipsis.
    """

    (
        _,
        at,
        tail,
    ) = value.rpartition(
        "@",
    )

    # NOTE:
    # What comes before the last @ is where an address keeps its user and its password, so it never reaches a message
    # that a terminal, a log, or a screenshot may carry.
    return f"…@{tail}" if at else value


def refusal(
    *,
    setting: str,
    value: str,
) -> str:
    """
    The line for a `setting` whose `value` is not an http:// or https://
    address with a host.
    """

    return f'{setting} is "{
        show_value(
            value=value,
        )
    }", which is not an http:// or https:// address with a host'
