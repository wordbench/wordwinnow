#!/usr/bin/env python3
"""
Refuses a Compose configuration that would put a service beyond this host.

    docker compose config --format json | python3 stack/publication.py

Reads the configuration Compose resolved as JSON from standard input, so that
it judges exactly what Compose would start, override files included, and
`make up` and `make up-full` add `--profile full` to judge the whole stack.

A service is refused when it publishes a port with no host address, which
Docker binds on every interface, or with one outside 127.0.0.0/8 and other
than `::1`, and when it uses the host's own network; `expose` publishes
nothing and passes.

Prints one line for each refusal and ends with status 1, and prints nothing
for a configuration it accepts.

Input that is empty, or is no such configuration, ends with status 1 as well,
so that a failure of Compose to resolve its configuration stops the stack.

The configuration holds the values of the services' environments, so this
program never prints any of it beyond a service's name and the port it
publishes.
"""

from collections.abc import (
    Iterator,
    Mapping,
)
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    ip_address,
)
from json import (
    loads,
)
from sys import (
    stderr,
    stdin,
)
from typing import (
    Final,
    final,
)

_LOOPBACK_V4: Final = IPv4Network(
    address="127.0.0.0/8",
)

_LOOPBACK_V6: Final = IPv6Address(
    address="::1",
)

_EVERY_INTERFACE: Final = "every interface"

_UNREADABLE: Final = "the stack does not start: Compose's configuration could not be read"


@final
class _UnreadableError(
    ValueError,
):
    """
    Raised when the configuration is no Compose configuration this program can
    judge, which stops the stack as a refusal does.
    """


def _loopback(
    address: IPv4Address | IPv6Address,
    /,
) -> bool:
    return address in _LOOPBACK_V4 or address == _LOOPBACK_V6


def _publication(
    entry: object,
    /,
) -> tuple[str, str | None]:
    """
    The port a `ports` entry publishes on the host, and the host address it
    names, `None` when it names none.

    Compose resolves every entry to the long form, a mapping, and the short
    form, a string, is read as well: a configuration that holds one is judged
    rather than trusted.
    """

    if isinstance(
        entry,
        Mapping,
    ):
        address = entry.get(
            "host_ip",
        )

        published = entry.get(
            "published",
        ) or entry.get(
            "target",
        )

        if (
            address is not None
            and not isinstance(
                address,
                str,
            )
        ) or published is None:
            raise _UnreadableError

        return (
            str(
                object=published,
            ),
            address or None,
        )

    if not isinstance(
        entry,
        str,
    ):
        raise _UnreadableError

    mapping = entry.partition(
        "/",
    )[0]

    # NOTE:
    # An IPv6 address is written in brackets in the short form, which keeps its colons from separating the fields.
    if mapping.startswith(
        "[",
    ):
        (
            address,
            closing,
            rest,
        ) = mapping[1:].partition(
            "]",
        )

        if not closing:
            raise _UnreadableError

        match rest.removeprefix(
            ":",
        ).split(
            sep=":",
        ):
            case [host, container]:
                return (
                    host or container,
                    address,
                )

            case _:
                raise _UnreadableError

    match mapping.split(
        sep=":",
    ):
        case [container]:
            return (
                container,
                None,
            )

        case [host, container]:
            return (
                host or container,
                None,
            )

        case [address, host, container]:
            return (
                host or container,
                address or None,
            )

        case _:
            raise _UnreadableError


def _refusals(
    services: Mapping[str, object],
    /,
) -> Iterator[str]:
    for (
        name,
        service,
    ) in services.items():
        if not isinstance(
            service,
            Mapping,
        ):
            raise _UnreadableError

        ports = service.get(
            "ports",
        )

        # NOTE:
        # Compose leaves the key out of a service that publishes nothing, and a null is the same statement.
        if ports is None:
            ports = []

        if not isinstance(
            ports,
            list,
        ):
            raise _UnreadableError

        for entry in ports:
            (
                published,
                address,
            ) = _publication(
                entry,
            )

            try:
                beyond = address is None or not _loopback(
                    ip_address(
                        address=address.strip(
                            "[]",
                        ),
                    ),
                )

            except ValueError:
                beyond = True

            if beyond:
                yield (
                    f"the stack does not start: {name} publishes port {published} on {address or _EVERY_INTERFACE}, "
                    "beyond this host's loopback interface, and Wordwinnow supports publishing its ports on "
                    "127.0.0.1 alone"
                )

        if (
            service.get(
                "network_mode",
            )
            == "host"
        ):
            yield (
                f"the stack does not start: {name} uses this host's network, where its address would be this host's, "
                "and Wordwinnow supports the stack's own network alone"
            )


def main() -> int:
    """
    Judge the configuration on standard input.

    Returns 0 when every service stays on this host's loopback interface and
    its own network, and 1 otherwise.
    """

    try:
        configuration = loads(
            s=stdin.buffer.read(),
        )

        if not isinstance(
            configuration,
            Mapping,
        ):
            raise _UnreadableError

        services = configuration.get(
            "services",
        )

        if (
            not isinstance(
                services,
                Mapping,
            )
            or not services
        ):
            raise _UnreadableError

        refused = tuple(
            _refusals(
                services,
            ),
        )

    except ValueError:
        print(
            _UNREADABLE,
            file=stderr,
        )

        return 1

    for line in refused:
        print(
            line,
            file=stderr,
        )

    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(
        main(),
    )
