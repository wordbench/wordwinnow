"""
Every string enumeration the project defines says what ordering means for it:
the CEFR scale orders by rank, and everything else refuses to order.
"""

from enum import (
    StrEnum,
)
from importlib import (
    import_module,
)
from inspect import (
    getmembers,
    isclass,
)
from typing import (
    final,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
    python_files,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)


def _string_enums() -> dict[str, type[StrEnum]]:
    found: dict[str, type[StrEnum]] = {}

    for path in python_files(
        directory=PACKAGE_ROOT,
    ):
        parts = (
            path.relative_to(
                other=PACKAGE_ROOT.parent,
            )
            .with_suffix(
                suffix="",
            )
            .parts
        )

        module = import_module(
            name=".".join(
                parts[:-1] if parts[-1] == "__init__" else parts,
            ),
        )

        for (
            name,
            member,
        ) in getmembers(
            object=module,
            predicate=isclass,
        ):
            if (
                issubclass(
                    member,
                    StrEnum,
                )
                and member.__module__ == module.__name__
                and member is not UnorderedStrEnum
            ):
                found[f"{module.__name__}.{name}"] = member

    return found


@final
class TestStringEnums:
    def test_every_string_enum_declares_its_ordering(
        self,
    ) -> None:
        enums = _string_enums()

        # TEST:
        # The scan has to see the project's enumerations, or a clean result would prove nothing.
        assert "wordwinnow.domain.cefr.CefrLevel" in enums

        assert "wordwinnow.domain.study.StudyTier" in enums

        assert (
            len(
                enums,
            )
            >= 14
        )

        unexplained = sorted(
            name
            for (
                name,
                member,
            ) in enums.items()
            if member is not CefrLevel
            and not issubclass(
                member,
                UnorderedStrEnum,
            )
        )

        assert unexplained == []
