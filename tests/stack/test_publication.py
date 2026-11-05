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


def _unconfirmed(
    setting: str,
    /,
) -> str:
    return (
        f"the stack does not start: Compose's configuration sets {setting}, which this check cannot confirm keeps "
        "the stack on this host, and Wordwinnow supports the stack's own network alone, as docker-compose.yml "
        "declares it\n"
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


def _network(
    key: str,
    settings: dict[str, object],
    /,
) -> dict[str, object]:
    """
    One network as `docker compose config --format json` writes it for a
    network that the file declares with nothing, with `settings` laid over it.
    """

    return {
        "name": f"foo_{key}",
        "ipam": {},
    } | settings


def _networked(
    networks: dict[str, object],
    /,
    **services: dict[str, object],
) -> str:
    return dumps(
        obj={
            "name": "foo",
            "networks": networks,
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

    # NOTE:
    # The first is what Compose resolves the repository's file to, and the second adds the two settings the rule
    # accepts besides the name and the empty IPAM.
    @mark.parametrize(
        argnames="settings",
        argvalues=(
            {},
            {
                "driver": "bridge",
                "external": False,
            },
        ),
    )
    def test_the_stacks_own_network_passes_without_a_word(
        self,
        *,
        settings: dict[str, object],
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        settings,
                    ),
                },
                foo={
                    "networks": {
                        "default": None,
                    },
                    "ports": [
                        _port(
                            "127.0.0.1",
                            "5432",
                        ),
                    ],
                },
                bar={
                    "networks": {
                        "default": None,
                    },
                    "ports": [
                        _port(
                            "127.0.0.1",
                            "5433",
                        ),
                    ],
                },
            ),
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

    @mark.parametrize(
        argnames="joined",
        argvalues=(
            None,
            {},
        ),
    )
    def test_a_second_network_declared_with_nothing_passes_as_the_first_does(
        self,
        *,
        joined: dict[str, object] | None,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {},
                    ),
                    "bar": _network(
                        "bar",
                        {},
                    ),
                },
                foo={
                    "networks": {
                        "default": None,
                        "bar": joined,
                    },
                },
                baz={
                    "networks": {
                        "bar": joined,
                    },
                },
            ),
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

    @mark.parametrize(
        argnames="mode",
        argvalues=(
            "none",
            "service:foo",
        ),
    )
    def test_a_network_mode_of_none_or_of_a_service_of_the_configuration_passes(
        self,
        *,
        mode: str,
    ) -> None:
        result = _check(
            _configuration(
                foo={},
                bar={
                    "network_mode": mode,
                },
            ),
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

    # NOTE:
    # Compose names the project from `COMPOSE_PROJECT_NAME`, a `name` in a Compose file, or the directory, and the
    # configuration it resolves carries that name, so the rule reads it there and fixes none of its own.
    @mark.parametrize(
        argnames="project",
        argvalues=(
            "foo",
            "bar",
            "foo-bar",
            "foo_bar",
            "foo-1",
        ),
    )
    def test_the_default_network_named_for_the_project_in_the_configuration_passes(
        self,
        *,
        project: str,
    ) -> None:
        result = _check(
            dumps(
                obj={
                    "name": project,
                    "networks": {
                        "default": {
                            "name": f"{project}_default",
                            "ipam": {},
                        },
                    },
                    "services": {
                        "foo": {
                            "networks": {
                                "default": None,
                            },
                        },
                    },
                },
            ),
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

    def test_a_configuration_with_no_name_and_no_network_passes(
        self,
    ) -> None:
        result = _check(
            '{"networks": {}, "services": {"foo": {"networks": null}}}',
        )

        assert result.returncode == 0

        assert result.stdout == ""

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

    @mark.parametrize(
        argnames="mode",
        argvalues=(
            "bridge",
            "default",
            "container:bar",
            "HOST",
            "service:qux",
            "service:",
            "SERVICE:foo",
            "None",
            "",
        ),
    )
    def test_a_network_mode_that_is_not_the_stacks_own_is_refused_with_its_line(
        self,
        *,
        mode: str,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "network_mode": mode,
                },
                bar={},
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            "services.foo.network_mode",
        )

    # NOTE:
    # `bar` and `baz` are judged as themselves and nothing is said about them: the service they name is the one that
    # is wrong, and it is judged once.
    @mark.parametrize(
        argnames=(
            "mode",
            "line",
        ),
        argvalues=(
            (
                "host",
                _on_the_hosts_network(
                    "foo",
                ),
            ),
            (
                "bridge",
                _unconfirmed(
                    "services.foo.network_mode",
                ),
            ),
        ),
    )
    def test_a_service_reached_through_service_is_judged_once_as_itself(
        self,
        *,
        mode: str,
        line: str,
    ) -> None:
        result = _check(
            _configuration(
                bar={
                    "network_mode": "service:foo",
                },
                baz={
                    "network_mode": "service:bar",
                },
                foo={
                    "network_mode": mode,
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == line

    @mark.parametrize(
        argnames=(
            "key",
            "value",
        ),
        argvalues=(
            (
                "driver",
                "macvlan",
            ),
            (
                "driver",
                "ipvlan",
            ),
            (
                "driver",
                "overlay",
            ),
            (
                "driver_opts",
                {
                    "com.docker.network.bridge.gateway_mode_ipv4": "routed",
                },
            ),
            (
                "ipam",
                {
                    "config": [
                        {
                            "subnet": "192.0.2.0/24",
                        },
                    ],
                },
            ),
            (
                "ipam",
                {
                    "driver": "baz",
                },
            ),
            (
                "ipam",
                {
                    "options": {
                        "baz": "qux",
                    },
                },
            ),
            (
                "external",
                True,
            ),
            (
                "name",
                "bar_default",
            ),
            (
                "name",
                "host",
            ),
            (
                "enable_ipv6",
                True,
            ),
            (
                "enable_ipv4",
                False,
            ),
            (
                "internal",
                True,
            ),
            (
                "attachable",
                True,
            ),
            (
                "labels",
                {
                    "baz": "qux",
                },
            ),
            (
                "x-foo",
                "bar",
            ),
        ),
    )
    def test_a_network_setting_beyond_the_four_is_refused_with_its_line(
        self,
        *,
        key: str,
        value: object,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {
                            key: value,
                        },
                    ),
                },
                foo={
                    "networks": {
                        "default": None,
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            f"networks.default.{key}",
        )

    # NOTE:
    # Each value equals the allowed one in Python, or is the allowed one in another type or case, and is refused all
    # the same: the rule compares JSON values and their types.
    @mark.parametrize(
        argnames=(
            "key",
            "value",
        ),
        argvalues=(
            (
                "external",
                0,
            ),
            (
                "external",
                "false",
            ),
            (
                "external",
                None,
            ),
            (
                "driver",
                "Bridge",
            ),
            (
                "driver",
                None,
            ),
            (
                "ipam",
                [],
            ),
            (
                "ipam",
                None,
            ),
            (
                "name",
                "FOO_default",
            ),
            (
                "name",
                5,
            ),
            (
                "name",
                None,
            ),
        ),
    )
    def test_a_network_setting_is_compared_as_the_json_value_it_is(
        self,
        *,
        key: str,
        value: object,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {
                            key: value,
                        },
                    ),
                },
                foo={
                    "networks": {
                        "default": None,
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            f"networks.default.{key}",
        )

    # NOTE:
    # Each name is another project's network, or the project's own with something added or left out, and Compose would
    # join the network that name gives or make it, so the name must be the project's, an underscore, and the network's
    # key, and nothing else.
    @mark.parametrize(
        argnames=(
            "project",
            "network",
        ),
        argvalues=(
            (
                "bar",
                "foo_default",
            ),
            (
                "foo_bar",
                "foo_default",
            ),
            (
                "foo",
                "foo_bar_default",
            ),
            (
                "foo",
                "foo",
            ),
            (
                "foo",
                "foo-default",
            ),
            (
                "foo",
                "foo_default_",
            ),
            (
                "foo",
                "_default",
            ),
            (
                "foo",
                "default",
            ),
        ),
    )
    def test_a_default_network_not_named_for_the_project_in_the_configuration_is_refused(
        self,
        *,
        project: str,
        network: str,
    ) -> None:
        result = _check(
            dumps(
                obj={
                    "name": project,
                    "networks": {
                        "default": {
                            "name": network,
                            "ipam": {},
                        },
                    },
                    "services": {
                        "foo": {
                            "networks": {
                                "default": None,
                            },
                        },
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            "networks.default.name",
        )

    def test_a_macvlan_network_with_its_options_gets_a_line_for_each_setting_in_order(
        self,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {
                            "driver": "macvlan",
                            "driver_opts": {
                                "parent": "baz",
                            },
                        },
                    ),
                },
                foo={
                    "networks": {
                        "default": None,
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == (
            _unconfirmed(
                "networks.default.driver",
            )
            + _unconfirmed(
                "networks.default.driver_opts",
            )
        )

    @mark.parametrize(
        argnames=(
            "key",
            "value",
        ),
        argvalues=(
            (
                "aliases",
                [
                    "baz",
                ],
            ),
            (
                "ipv4_address",
                "192.0.2.1",
            ),
            (
                "ipv6_address",
                "2001:db8::1",
            ),
            (
                "mac_address",
                "02:00:00:00:00:01",
            ),
            (
                "driver_opts",
                {
                    "baz": "qux",
                },
            ),
            (
                "gw_priority",
                1,
            ),
            (
                "priority",
                1,
            ),
            (
                "interface_name",
                "baz",
            ),
            (
                "link_local_ips",
                [
                    "192.0.2.2",
                ],
            ),
            (
                "foo",
                "bar",
            ),
        ),
    )
    def test_a_service_setting_of_its_own_on_a_network_is_refused_with_its_line(
        self,
        *,
        key: str,
        value: object,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {},
                    ),
                },
                foo={
                    "networks": {
                        "default": {
                            key: value,
                        },
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            f"services.foo.networks.default.{key}",
        )

    @mark.parametrize(
        argnames="joined",
        argvalues=(
            None,
            {},
            {
                "aliases": [
                    "baz",
                ],
            },
        ),
    )
    def test_a_service_on_a_network_the_configuration_lacks_is_refused_with_one_line(
        self,
        *,
        joined: dict[str, object] | None,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {},
                    ),
                },
                foo={
                    "networks": {
                        "bar": joined,
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            "services.foo.networks.bar",
        )

    def test_a_service_on_a_network_when_the_configuration_declares_none_is_refused(
        self,
    ) -> None:
        result = _check(
            _configuration(
                foo={
                    "networks": {
                        "default": None,
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _unconfirmed(
            "services.foo.networks.default",
        )

    def test_every_line_comes_in_the_order_of_the_services_then_of_the_networks(
        self,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {
                            "driver": "macvlan",
                        },
                    ),
                    "bar": _network(
                        "bar",
                        {
                            "internal": True,
                        },
                    ),
                },
                bar={
                    "ports": [
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
                            None,
                            "5434",
                        ),
                    ],
                    "network_mode": "bridge",
                },
                foo={
                    "networks": {
                        "default": {
                            "aliases": [
                                "baz",
                            ],
                            "ipv4_address": "192.0.2.1",
                        },
                        "qux": None,
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stdout == ""

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
                "baz",
                "5434",
                "every interface",
            )
            + _unconfirmed(
                "services.baz.network_mode",
            )
            + _unconfirmed(
                "services.foo.networks.default.aliases",
            )
            + _unconfirmed(
                "services.foo.networks.default.ipv4_address",
            )
            + _unconfirmed(
                "services.foo.networks.qux",
            )
            + _unconfirmed(
                "networks.default.driver",
            )
            + _unconfirmed(
                "networks.bar.internal",
            )
        )

    # NOTE:
    # Every value below sits where a setting's value sits, in a network, in a service's network, and in a mode, and a
    # refusal names the setting's path and nothing the setting holds.
    def test_nothing_of_a_network_setting_but_its_path_is_printed(
        self,
    ) -> None:
        result = _check(
            _networked(
                {
                    "default": _network(
                        "default",
                        {
                            "driver_opts": {
                                "baz": f"{_SECRET}-driver-opts",
                            },
                            "ipam": {
                                "config": [
                                    {
                                        "subnet": f"{_SECRET}-subnet",
                                    },
                                ],
                            },
                            "labels": {
                                "baz": f"{_SECRET}-label",
                            },
                        },
                    ),
                },
                bar={
                    "network_mode": f"container:{_SECRET}-target",
                },
                foo={
                    "networks": {
                        "default": {
                            "aliases": [
                                f"{_SECRET}-alias",
                            ],
                        },
                    },
                },
            ),
        )

        assert result.returncode == 1

        assert result.stderr == (
            _unconfirmed(
                "services.bar.network_mode",
            )
            + _unconfirmed(
                "services.foo.networks.default.aliases",
            )
            + _unconfirmed(
                "networks.default.ipam",
            )
            + _unconfirmed(
                "networks.default.driver_opts",
            )
            + _unconfirmed(
                "networks.default.labels",
            )
        )

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

    @mark.parametrize(
        argnames="text",
        argvalues=(
            '{"name": "foo", "networks": [], "services": {"foo": {}}}',
            '{"name": "foo", "networks": null, "services": {"foo": {}}}',
            '{"name": "foo", "networks": "default", "services": {"foo": {}}}',
            '{"name": "foo", "networks": 5, "services": {"foo": {}}}',
            '{"name": "foo", "networks": {"default": null}, "services": {"foo": {}}}',
            '{"name": "foo", "networks": {"default": []}, "services": {"foo": {}}}',
            '{"name": "foo", "networks": {"default": "foo"}, "services": {"foo": {}}}',
            '{"networks": {"default": {}}, "services": {"foo": {}}}',
            '{"name": null, "networks": {"default": {}}, "services": {"foo": {}}}',
            '{"name": 5, "networks": {"default": {}}, "services": {"foo": {}}}',
            '{"services": {"foo": {"network_mode": 5}}}',
            '{"services": {"foo": {"network_mode": null}}}',
            '{"services": {"foo": {"network_mode": []}}}',
            '{"services": {"foo": {"network_mode": {"host": true}}}}',
            '{"services": {"foo": {"networks": []}}}',
            '{"services": {"foo": {"networks": ["default"]}}}',
            '{"services": {"foo": {"networks": "default"}}}',
            '{"services": {"foo": {"networks": 5}}}',
            '{"name": "foo", "networks": {"default": {}}, "services": {"foo": {"networks": {"default": []}}}}',
            '{"name": "foo", "networks": {"default": {}}, "services": {"foo": {"networks": {"default": "foo"}}}}',
            '{"name": "foo", "networks": {"default": {}}, "services": {"foo": {"networks": {"default": 5}}}}',
            '{"name": "foo", "networks": {"default": {}}, "services": {"foo": {"networks": {"default": true}}}}',
            '{"name": "foo", "networks": {"default": {}}, "services": {"foo": {"networks": {"bar": 5}}}}',
        ),
    )
    def test_a_network_entry_the_rule_cannot_read_ends_with_the_last_line(
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

    @mark.parametrize(
        argnames="text",
        argvalues=(
            '{"name": "foo", "networks": {"default": null}, "services": {"bar": {"ports": ["5433"]}}}',
            '{"services": {"bar": {"ports": ["5433"]}, "foo": {"networks": []}}}',
            '{"services": {"bar": {"ports": ["5433"]}, "foo": {"network_mode": 5}}}',
            (
                '{"name": "foo", "networks": {"default": {}}, '
                '"services": {"bar": {"ports": ["5433"]}, "foo": {"networks": {"default": 5}}}}'
            ),
            '{"services": {"bar": {"network_mode": "bridge"}, "foo": {"network_mode": 5}}}',
        ),
    )
    def test_an_unreadable_network_entry_beside_a_refusal_gives_the_last_line_alone(
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
