"""
The stack's files, read as text: every port is published on 127.0.0.1, the
passwords come from one substitution that Compose refuses to leave empty, and
`make up` and `make up-full` run the two checks before Compose.

The tests read the files rather than run Compose, because running it would act
on whatever stack the checkout's directory names, and no YAML parser is
declared.
Each scan ends with a synthetic violation it is proven to catch.
"""

from itertools import (
    takewhile,
)
from re import (
    compile as compile_pattern,
)
from typing import (
    Final,
    final,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
)

_ROOT: Final = PACKAGE_ROOT.parent

_SUBSTITUTION: Final = compile_pattern(
    pattern=r"\$\{(?P<name>[A-Z_]+):\?[^}]+\}",
)

_SERVICE: Final = compile_pattern(
    pattern=r"  (?P<name>[a-z-]+):",
)

_PORT_ITEM: Final = compile_pattern(
    pattern=r'\s+- "(?P<mapping>[^"]*)"',
)


def _read(
    name: str,
    /,
) -> tuple[str, ...]:
    return tuple(
        (_ROOT / name)
        .read_text(
            encoding="utf-8",
        )
        .splitlines(),
    )


def _blocks(
    lines: tuple[str, ...],
    /,
) -> dict[str, tuple[str, ...]]:
    """
    The lines of each service under `services:`, by its name, and of the
    anchored `x-application` block under the name `x-application`.
    """

    blocks: dict[str, tuple[str, ...]] = {}

    section: str | None = None

    name: str | None = None

    for line in lines:
        # NOTE:
        # A line that starts a top-level key ends the block before it, and a service is a key two spaces in under
        # `services:` alone, which keeps `build:` and `image:` of the anchored block from being read as services.
        if line and not line.startswith(
            (
                " ",
                "#",
            ),
        ):
            section = line.partition(
                ":",
            )[0]

            name = None

            if section == "x-application":
                name = section

                blocks[name] = ()

        elif (
            section == "services"
            and (
                service := _SERVICE.fullmatch(
                    string=line,
                )
            )
            is not None
        ):
            name = service["name"]

            blocks[name] = ()

        elif name is not None:
            blocks[name] += (line,)

    return blocks


def _environment(
    block: tuple[str, ...],
    name: str,
    /,
) -> str | None:
    """
    What `name` is set to in the block's environment, or `None`.
    """

    pattern = compile_pattern(
        pattern=rf"\s+{name}: (?P<value>.+)",
    )

    for line in block:
        matched = pattern.fullmatch(
            string=line,
        )

        if matched is not None:
            return matched["value"]

    return None


def _published(
    block: tuple[str, ...],
    /,
) -> tuple[str, ...]:
    """
    The items of the block's `ports:` list, which is none when it has no
    such list.
    """

    start = next(
        (
            index
            for (
                index,
                line,
            ) in enumerate(
                iterable=block,
            )
            if line == "    ports:"
        ),
        None,
    )

    if start is None:
        return ()

    return tuple(
        item["mapping"]
        for item in (
            _PORT_ITEM.fullmatch(
                string=line,
            )
            for line in takewhile(
                lambda line: line.startswith(
                    "      - ",
                ),
                block[start + 1 :],
            )
        )
        if item is not None
    )


def _not_on_loopback(
    lines: tuple[str, ...],
    /,
) -> tuple[str, ...]:
    """
    Every published port of the file, with its service, that does not begin
    with the loopback address, and every service that has `ports:` but no list
    this scan can read.
    """

    out: list[str] = []

    for (
        name,
        block,
    ) in _blocks(
        lines,
    ).items():
        for mapping in _published(
            block,
        ):
            if not mapping.startswith(
                "127.0.0.1:",
            ):
                out.append(
                    f"{name} publishes {mapping}",
                )

        if "    ports:" in block and not _published(
            block,
        ):
            out.append(
                f"{name} has ports this scan cannot read",
            )

    return tuple(
        out,
    )


def _recipe(
    lines: tuple[str, ...],
    target: str,
    /,
) -> tuple[str, ...]:
    """
    The tab-indented lines under `target`, which make runs for it.
    """

    start = next(
        index
        for (
            index,
            line,
        ) in enumerate(
            iterable=lines,
        )
        if line.startswith(
            f"{target}:",
        )
    )

    return tuple(
        takewhile(
            lambda line: line.startswith(
                "\t",
            ),
            lines[start + 1 :],
        ),
    )


def _before_compose(
    recipe: tuple[str, ...],
    /,
) -> bool:
    """
    Whether the recipe writes the passwords, then checks what Compose
    resolved, and only then starts anything.
    """

    steps = tuple(
        next(
            (
                index
                for (
                    index,
                    line,
                ) in enumerate(
                    iterable=recipe,
                )
                if marker in line
            ),
            None,
        )
        for marker in (
            "stack/credentials.py",
            "stack/publication.py",
            " up -d ",
        )
    )

    return None not in steps and steps == tuple(
        sorted(
            steps,
            key=lambda index: index or 0,
        ),
    )


_COMPOSE: Final = _read(
    "docker-compose.yml",
)

_SYNTHETIC: Final = (
    "services:",
    "  foo:",
    "    ports:",
    '      - "127.0.0.1:5432:5432"',
    '      - "5433:5432"',
    "  bar:",
    "    ports:",
    '      - "0.0.0.0:5434:5432"',
    "  baz:",
    "    image: foo",
)


@final
class TestEveryPort:
    def test_every_published_port_begins_with_the_loopback_address(
        self,
    ) -> None:
        assert (
            _not_on_loopback(
                _COMPOSE,
            )
            == ()
        )

        # NOTE:
        # The seven ports of the stores, the two services, and Grafana: a scan that read none would pass the test
        # above.
        assert (
            sum(
                len(
                    _published(
                        block,
                    ),
                )
                for block in _blocks(
                    _COMPOSE,
                ).values()
            )
            == 7
        )

    def test_prometheus_and_tempo_publish_nothing(
        self,
    ) -> None:
        blocks = _blocks(
            _COMPOSE,
        )

        for name in (
            "prometheus",
            "tempo",
        ):
            assert blocks[name]

            assert "    ports:" not in blocks[name]

    def test_the_scan_catches_a_port_with_no_address_and_one_on_every_interface(
        self,
    ) -> None:
        assert _not_on_loopback(
            _SYNTHETIC,
        ) == (
            "foo publishes 5433:5432",
            "bar publishes 0.0.0.0:5434:5432",
        )


@final
class TestTheApplicationsEnvironment:
    def test_the_stack_says_it_is_one_and_listens_inside_its_containers_and_allows_the_two_names(
        self,
    ) -> None:
        application = _blocks(
            _COMPOSE,
        )["x-application"]

        assert (
            _environment(
                application,
                "WORDWINNOW_LISTEN_ADDRESS",
            )
            == "0.0.0.0"
        )

        assert (
            _environment(
                application,
                "WORDWINNOW_COMPOSE_STACK",
            )
            == '"true"'
        )

        assert (
            _environment(
                application,
                "WORDWINNOW_ALLOWED_HOSTS",
            )
            == "intake,enrichment"
        )

    # NOTE:
    # What lets an address beyond loopback through is this one file, so nothing else in the repository may set it: a
    # setting in the `Dockerfile`, a target, or a workflow would carry the allowance to a process Compose does not
    # publish.
    def test_nothing_else_sets_the_compose_stack(
        self,
    ) -> None:
        for name in (
            "Dockerfile",
            "Makefile",
            ".env.example",
            ".envrc",
            ".github/workflows/ci.yml",
        ):
            assert "WORDWINNOW_COMPOSE_STACK" not in "\n".join(
                _read(
                    name,
                ),
            )


@final
class TestThePasswords:
    def test_one_substitution_feeds_the_server_and_the_applications_and_has_no_fallback(
        self,
    ) -> None:
        blocks = _blocks(
            _COMPOSE,
        )

        server = _environment(
            blocks["clickhouse"],
            "CLICKHOUSE_PASSWORD",
        )

        applications = _environment(
            blocks["x-application"],
            "WORDWINNOW_CLICKHOUSE_PASSWORD",
        )

        assert server is not None

        assert server == applications

        matched = _SUBSTITUTION.fullmatch(
            string=server,
        )

        assert matched is not None

        assert matched["name"] == "WORDWINNOW_CLICKHOUSE_PASSWORD"

    def test_grafanas_password_is_a_substitution_with_no_fallback(
        self,
    ) -> None:
        password = _environment(
            _blocks(
                _COMPOSE,
            )["grafana"],
            "GF_SECURITY_ADMIN_PASSWORD",
        )

        assert password is not None

        matched = _SUBSTITUTION.fullmatch(
            string=password,
        )

        assert matched is not None

        assert matched["name"] == "GRAFANA_ADMIN_PASSWORD"

    def test_no_password_has_a_literal_or_a_fallback(
        self,
    ) -> None:
        text = "\n".join(
            _COMPOSE,
        )

        assert ":-wordwinnow" not in text

        assert "CLICKHOUSE_PASSWORD: wordwinnow" not in text

        assert "ADMIN_PASSWORD: wordwinnow" not in text

    def test_the_substitution_check_catches_a_fallback_and_a_literal(
        self,
    ) -> None:
        assert (
            _SUBSTITUTION.fullmatch(
                string="${GRAFANA_ADMIN_PASSWORD:-wordwinnow}",
            )
            is None
        )

        assert (
            _SUBSTITUTION.fullmatch(
                string="wordwinnow",
            )
            is None
        )

    def test_grafana_answers_only_to_localhost(
        self,
    ) -> None:
        grafana = _blocks(
            _COMPOSE,
        )["grafana"]

        assert (
            _environment(
                grafana,
                "GF_SERVER_DOMAIN",
            )
            == "localhost"
        )

        assert (
            _environment(
                grafana,
                "GF_SERVER_ENFORCE_DOMAIN",
            )
            == '"true"'
        )

    def test_the_example_file_holds_neither_passwords_line(
        self,
    ) -> None:
        example = "\n".join(
            _read(
                ".env.example",
            ),
        )

        for name in (
            "WORDWINNOW_CLICKHOUSE_PASSWORD",
            "GRAFANA_ADMIN_PASSWORD",
        ):
            assert f"{name}=" not in example


@final
class TestTheTargets:
    def test_up_and_up_full_write_the_passwords_then_check_what_compose_resolved_then_start(
        self,
    ) -> None:
        lines = _read(
            "Makefile",
        )

        for target in (
            "up",
            "up-full",
        ):
            assert _before_compose(
                _recipe(
                    lines,
                    target,
                ),
            )

    def test_the_check_catches_a_recipe_that_starts_first_or_skips_a_step(
        self,
    ) -> None:
        assert not _before_compose(
            (
                "\tdocker compose up -d --wait foo",
                "\t$(RUN) python3 stack/credentials.py",
                "\tdocker compose config | $(RUN) python3 stack/publication.py",
            ),
        )

        assert not _before_compose(
            (
                "\t$(RUN) python3 stack/credentials.py",
                "\tdocker compose up -d --wait foo",
            ),
        )

        assert _before_compose(
            (
                "\t$(RUN) python3 stack/credentials.py",
                "\tdocker compose config | $(RUN) python3 stack/publication.py",
                "\tdocker compose up -d --wait foo",
            ),
        )
