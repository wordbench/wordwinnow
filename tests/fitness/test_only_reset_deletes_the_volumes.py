"""
The stack's teardown: `make down` keeps the named volumes, and only
`make reset`, asked for with `CONFIRM=1`, deletes them.

The test reads the Makefile rather than running it, because running either
target would act on whatever stack the checkout's directory names.
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

_MAKEFILE: Final = PACKAGE_ROOT.parent / "Makefile"

# NOTE:
# `down` with `-v` or `--volumes` deletes the named volumes; `rm --volumes` deletes only the anonymous ones.
_DELETES_NAMED_VOLUMES: Final = compile_pattern(
    pattern=r"\bdown\b.*(\s-v\b|\s--volumes\b)",
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


def _lines() -> tuple[str, ...]:
    return tuple(
        _MAKEFILE.read_text(
            encoding="utf-8",
        ).splitlines(),
    )


@final
class TestTeardown:
    def test_down_keeps_the_named_volumes(
        self,
    ) -> None:
        recipe = _recipe(
            _lines(),
            "down",
        )

        assert recipe

        assert not any(
            _DELETES_NAMED_VOLUMES.search(
                string=line,
            )
            for line in recipe
        )

    def test_only_reset_deletes_the_volumes_and_only_once_confirmed(
        self,
    ) -> None:
        lines = _lines()

        recipe = _recipe(
            lines,
            "reset",
        )

        assert tuple(
            line
            for line in lines
            if _DELETES_NAMED_VOLUMES.search(
                string=line,
            )
        ) == ("\tdocker compose --profile full down -v",)

        guard = next(
            index
            for (
                index,
                line,
            ) in enumerate(
                iterable=recipe,
            )
            if '"$(CONFIRM)" != "1"' in line
        )

        deletion = next(
            index
            for (
                index,
                line,
            ) in enumerate(
                iterable=recipe,
            )
            if _DELETES_NAMED_VOLUMES.search(
                string=line,
            )
        )

        assert guard < deletion

        assert any("exit 2" in line for line in recipe[guard:deletion])

    def test_the_pattern_catches_both_spellings_and_leaves_rm_alone(
        self,
    ) -> None:
        assert _DELETES_NAMED_VOLUMES.search(
            string="\tdocker compose down -v",
        )

        assert _DELETES_NAMED_VOLUMES.search(
            string="\tdocker compose --profile full down --volumes",
        )

        assert not _DELETES_NAMED_VOLUMES.search(
            string="\tdocker compose --profile full rm --stop --force --volumes",
        )
