"""
The one AST walk the fitness tests share.
"""

from ast import (
    Call,
    Import,
    ImportFrom,
    Name,
    parse,
    walk,
)
from collections.abc import (
    Iterator,
)
from pathlib import (
    Path,
)
from sys import (
    stdlib_module_names,
)
from typing import (
    Final,
)

PACKAGE_ROOT: Final = (
    Path(
        __file__,
    ).parents[2]
    / "wordwinnow"
)


def python_files(
    *,
    directory: Path,
) -> tuple[Path, ...]:
    """
    Every Python file under `directory`, in a stable order.
    """

    return tuple(
        sorted(
            directory.rglob(
                pattern="*.py",
            ),
        ),
    )


def full_imports(
    *,
    source: str,
) -> frozenset[str]:
    """
    Every dotted module the source imports from.
    """

    tree = parse(
        source=source,
    )

    names: set[str] = set()

    for node in walk(
        node=tree,
    ):
        if isinstance(
            node,
            Import,
        ):
            names.update(alias.name for alias in node.names)

        elif (
            isinstance(
                node,
                ImportFrom,
            )
            and node.module is not None
        ):
            names.add(
                node.module,
            )

    return frozenset(
        names,
    )


def is_stdlib(
    *,
    name: str,
) -> bool:
    """
    Whether a top-level module name belongs to the standard library.
    """

    return name in stdlib_module_names


def calls_named(
    *,
    source: str,
    name: str,
) -> Iterator[int]:
    """
    The line of every call whose callee is the bare name given.
    """

    tree = parse(
        source=source,
    )

    for node in walk(
        node=tree,
    ):
        if (
            isinstance(
                node,
                Call,
            )
            and isinstance(
                node.func,
                Name,
            )
            and node.func.id == name
        ):
            yield node.lineno
