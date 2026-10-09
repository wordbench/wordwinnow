"""
`Analysis` is constructed only where its lifecycle invariants live.

Everything else goes through `request_an_analysis` or `reconstitute`, so a
stored analysis can never come back in a state the aggregate would refuse.
"""

from collections.abc import (
    Iterable,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
    calls_named,
    python_files,
)

_OWNER: Final = PACKAGE_ROOT / "domain" / "analysis.py"


def _construction_sites(
    roots: Iterable[Path],
    /,
) -> tuple[str, ...]:
    return tuple(
        f"{path}:{line}"
        for root in roots
        for path in python_files(
            directory=root,
        )
        if path != _OWNER
        for line in calls_named(
            source=path.read_text(
                encoding="utf-8",
            ),
            name="Analysis",
        )
    )


@final
class TestAggregateConstruction:
    def test_nothing_outside_the_aggregate_module_constructs_an_analysis(
        self,
    ) -> None:
        assert (
            _construction_sites(
                (
                    PACKAGE_ROOT,
                    PACKAGE_ROOT.parent / "tests",
                ),
            )
            == ()
        )

    def test_the_scan_catches_a_direct_construction(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "leak.py").write_text(
            data="analysis = Analysis(\n    id=foo,\n)\n",
            encoding="utf-8",
        )

        assert (
            _construction_sites(
                (tmp_path,),
            )
            != ()
        )

    def test_an_annotation_or_an_isinstance_is_not_a_construction(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "fine.py").write_text(
            data="def f(\n    *,\n    analysis: Analysis,\n) -> bool:\n"
            "    return isinstance(\n        analysis,\n        Analysis,\n    )\n",
            encoding="utf-8",
        )

        assert (
            _construction_sites(
                (tmp_path,),
            )
            == ()
        )
