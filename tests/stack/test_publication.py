"""
The check of what Compose resolved, run as `make up` runs it: a program that
reads the configuration as JSON on standard input.
"""

from json import (
    dumps,
)
from pathlib import (
    Path,
)
from subprocess import (
    CompletedProcess,
    run,
)
from sys import (
    executable,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
)

_PROGRAM: Final = (
    Path(
        __file__,
    ).parents[2]
    / "stack"
    / "publication.py"
)

_TIMEOUT_SECONDS: Final = 30

_UNREADABLE: Final = "the stack does not start: Compose's configuration could not be read\n"

_SECRET: Final = "foo-secret-value"


def _publishes(
    service: str,
    published: str,
    where: str,
    /,
) -> str:
    return (
        f"the stack does not start: {service} publishes port {published} on {where}, beyond this host's loopback "
        "interface, and Wordwinnow supports publishing its ports on 127.0.0.1 alone\n"
    )


def _on_the_hosts_network(
    service: str,
    /,
) -> str:
    return (
        f"the stack does not start: {service} uses this host's network, where its address would be this host's, and "
        "Wordwinnow supports the stack's own network alone\n"
    )


def _port(
    host_ip: str | None,
    published: str,
    /,
) -> dict[str, object]:
    """
    One entry of a service's `ports` as `docker compose config --format json`
    writes it: the long form, with no `host_ip` where the file named none.
    """

    entry: dict[str, object] = {
        "mode": "ingress",
        "target": 5432,
        "published": published,
        "protocol": "tcp",
    }

    if host_ip is not None:
        entry["host_ip"] = host_ip

    return entry


def _configuration(
    **services: dict[str, object],
) -> str:
    return dumps(
        obj={
            "name": "foo",
            "services": services,
        },
    )


def _check(
    text: str,
    /,
) -> CompletedProcess[str]:
    return run(
        args=[
            executable,
            "-I",
            str(
                object=_PROGRAM,
            ),
        ],
        capture_output=True,
        input=text,
        timeout=_TIMEOUT_SECONDS,
        check=False,
        text=True,
        env={},
    )


@final
class TestAConfigurationItAccepts:
    @mark.parametrize(
        argnames="host_ip",
        argvalues=(
            "127.0.0.1",
            "127.0.0.2",
            "127.255.255.254",
            "::1",
            "[::1]",
        ),
    )
    def test_a_port_published_on_loopback_passes_without_a_word(
        self,
        *,
        host_ip: str,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "ports": [
                        _port(
                            host_ip,
                            "5432",
                        ),
                    ],
                },
            ),
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

    def test_a_service_that_only_exposes_a_port_or_has_none_passes(
        self,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "expose": [
                        "5432",
                    ],
                },
                bar={},
                baz={
                    "ports": None,
                    "network_mode": "service:foo",
                },
            ),
        )

        assert result.returncode == 0

        assert result.stderr == ""

    @mark.parametrize(
        argnames="entry",
        argvalues=(
            "127.0.0.1:5432:5432",
            "127.0.0.2:5432:5432/tcp",
            "[::1]:5432:5432",
            "127.0.0.1::5432",
            "127.0.0.1:5432-5433:5432-5433",
        ),
    )
    def test_the_short_form_on_loopback_passes(
        self,
        *,
        entry: str,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "ports": [
                        entry,
                    ],
                },
            ),
        )

        assert result.returncode == 0

        assert result.stderr == ""


@final
class TestAConfigurationItRefuses:
    @mark.parametrize(
        argnames=(
            "host_ip",
            "where",
        ),
        argvalues=(
            (
                None,
                "every interface",
            ),
            (
                "",
                "every interface",
            ),
            (
                "0.0.0.0",
                "0.0.0.0",
            ),
            (
                "::",
                "::",
            ),
            (
                "192.0.2.1",
                "192.0.2.1",
            ),
            (
                "128.0.0.1",
                "128.0.0.1",
            ),
            (
                "attacker.example",
                "attacker.example",
            ),
        ),
    )
    def test_a_port_published_beyond_loopback_or_with_no_address_is_refused_with_its_line(
        self,
        *,
        host_ip: str | None,
        where: str,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "ports": [
                        _port(
                            host_ip,
                            "5433",
                        ),
                    ],
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _publishes(
            "foo",
            "5433",
            where,
        )

    @mark.parametrize(
        argnames=(
            "entry",
            "published",
            "where",
        ),
        argvalues=(
            (
                "5433",
                "5433",
                "every interface",
            ),
            (
                "5433:5432",
                "5433",
                "every interface",
            ),
            (
                "5433:5432/udp",
                "5433",
                "every interface",
            ),
            (
                ":5432",
                "5432",
                "every interface",
            ),
            (
                "0.0.0.0:5433:5432",
                "5433",
                "0.0.0.0",
            ),
            (
                "[::]:5433:5432",
                "5433",
                "::",
            ),
            (
                "192.0.2.1:5433:5432",
                "5433",
                "192.0.2.1",
            ),
            (
                "5433-5434:5432-5433",
                "5433-5434",
                "every interface",
            ),
        ),
    )
    def test_the_short_form_is_judged_as_the_long_form_is(
        self,
        *,
        entry: str,
        published: str,
        where: str,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "ports": [
                        entry,
                    ],
                },
            ),
        )

        assert result.returncode == 1

        assert result.stderr == _publishes(
            "foo",
            published,
            where,
        )

    # NOTE:
    # An entry with a container port alone is published by Docker on a port it picks, on every interface.
    def test_a_container_port_with_no_host_port_or_address_is_published_on_every_interface(
        self,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "ports": [
                        {
                            "target": 5432,
                        },
                    ],
                },
            ),
        )

        assert result.returncode == 1

        assert result.stderr == _publishes(
            "foo",
            "5432",
            "every interface",
        )

    def test_a_service_on_the_hosts_network_is_refused_with_its_line(
        self,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "network_mode": "host",
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _on_the_hosts_network(
            "foo",
        )

    def test_each_refused_service_and_port_gets_a_line_and_the_others_none(
        self,
    ) -> None:
        result = _check(
            _configuration(
                bar={
                    "ports": [
                        _port(
                            "127.0.0.1",
                            "5432",
                        ),
                        _port(
                            None,
                            "5433",
                        ),
                    ],
                    "network_mode": "host",
                },
                baz={
                    "ports": [
                        _port(
                            "127.0.0.1",
                            "5434",
                        ),
                    ],
                },
                foo={
                    "ports": [
                        _port(
                            "0.0.0.0",
                            "5435",
                        ),
                    ],
                },
            ),
        )

        assert result.returncode == 1

        assert result.stderr == (
            _publishes(
                "bar",
                "5433",
                "every interface",
            )
            + _on_the_hosts_network(
                "bar",
            )
            + _publishes(
                "foo",
                "5435",
                "0.0.0.0",
            )
        )

    # NOTE:
    # The resolved configuration carries the services' environments, passwords among them, so a refusal names a
    # service and a port and nothing else of what it read.
    def test_nothing_of_the_configuration_but_a_name_and_a_port_is_printed(
        self,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "environment": {
                        "FOO": _SECRET,
                    },
                    "ports": [
                        _port(
                            None,
                            "5433",
                        ),
                    ],
                },
            ),
        )

        assert result.returncode == 1

        assert _SECRET not in result.stdout + result.stderr


@final
class TestAConfigurationItCannotRead:
    @mark.parametrize(
        argnames="text",
        argvalues=(
            "",
            "   \n",
            "foo",
            "{",
            "[]",
            "null",
            "{}",
            '{"services": {}}',
            '{"services": []}',
            '{"services": {"foo": []}}',
            '{"services": {"foo": {"ports": "5432"}}}',
            '{"services": {"foo": {"ports": [5432]}}}',
            '{"services": {"foo": {"ports": [{"published": "5432", "host_ip": 1}]}}}',
            '{"services": {"foo": {"ports": ["[::1:5432:5432"]}}}',
            '{"services": {"foo": {"ports": ["1:2:3:4"]}}}',
        ),
    )
    def test_an_input_that_is_no_configuration_ends_with_the_last_line_so_a_failed_compose_stops_the_stack(
        self,
        *,
        text: str,
    ) -> None:
        result = _check(
            text,
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _UNREADABLE

    def test_a_configuration_holding_an_unreadable_entry_is_not_judged_in_part(
        self,
    ) -> None:
        result = _check(
            _configuration(
                bar={
                    "ports": [
                        _port(
                            None,
                            "5433",
                        ),
                    ],
                },
                foo={
                    "ports": [
                        5432,
                    ],
                },
            ),
        )

        assert result.returncode == 1

        assert result.stderr == _UNREADABLE
