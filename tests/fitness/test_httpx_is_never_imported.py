"""
The HTTP client is `httpx2`, and nothing imports `httpx`.

`httpx` is installed beside it, because JupyterLab in the development group
requires it, so an import of it would pass every test and fail in the image,
which installs the runtime dependencies alone.
"""

from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
    full_imports,
    python_files,
)

_SUPERSEDED: Final = "httpx"

# NOTE:
# The package, its migrations, and its tests: every module of the project's own that the image or the gate runs.
_SCANNED: Final = (
    PACKAGE_ROOT,
    PACKAGE_ROOT.parent / "alembic",
    PACKAGE_ROOT.parent / "tests",
)


def _imports_of_the_superseded_client(
    directory: Path,
    /,
) -> tuple[str, ...]:
    return tuple(
        f"{path} imports {module}"
        for path in python_files(
            directory=directory,
        )
        for module in sorted(
            full_imports(
                source=path.read_text(
                    encoding="utf-8",
                ),
            ),
        )
        if module.split(
            sep=".",
        )[0]
        == _SUPERSEDED
    )


@final
class TestTheHttpClient:
    def test_no_module_imports_the_superseded_client(
        self,
    ) -> None:
        assert (
            tuple(
                violation
                for directory in _SCANNED
                for violation in _imports_of_the_superseded_client(
                    directory,
                )
            )
            == ()
        )

    def test_the_scan_catches_a_module_that_imports_it(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "leak.py").write_text(
            data="import httpx\n",
            encoding="utf-8",
        )

        assert (
            _imports_of_the_superseded_client(
                tmp_path,
            )
            != ()
        )

    def test_the_scan_leaves_the_client_in_use_alone(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "client.py").write_text(
            data="from httpx2 import (\n    AsyncClient,\n)\n",
            encoding="utf-8",
        )

        assert (
            _imports_of_the_superseded_client(
                tmp_path,
            )
            == ()
        )
