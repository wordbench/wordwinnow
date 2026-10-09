"""
Dependencies point inward, and the domain depends on nothing.

The rules, from the inside out: `domain` imports the standard library and
itself; `application` adds `domain`; `infrastructure` adds `application` and
third-party libraries but never `services`; `services` may import everything.
"""

from collections.abc import (
    Set as AbstractSet,
)
from pathlib import (
    Path,
)
from typing import (
    final,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
    full_imports,
    is_stdlib,
    python_files,
)


def _violations(
    directory: Path,
    allowed_packages: AbstractSet[str],
    /,
) -> tuple[str, ...]:
    out: list[str] = []

    for path in python_files(
        directory=directory,
    ):
        source = path.read_text(
            encoding="utf-8",
        )

        location = str(
            object=path,
        )

        for module in sorted(
            full_imports(
                source=source,
            ),
        ):
            top = module.split(
                sep=".",
            )[0]

            if is_stdlib(
                name=top,
            ):
                continue

            if top == "wordwinnow":
                layer = module.split(
                    sep=".",
                )[1]

                if layer not in allowed_packages:
                    out.append(
                        f"{location} imports {module}",
                    )

                continue

            # NOTE:
            # The domain and the application import no third-party library at all, so any is a violation.
            out.append(
                f"{location} imports {module}",
            )

    return tuple(
        out,
    )


@final
class TestDependencyDirection:
    def test_the_domain_imports_only_the_standard_library_and_itself(
        self,
    ) -> None:
        assert (
            _violations(
                PACKAGE_ROOT / "domain",
                frozenset(
                    {
                        "domain",
                    },
                ),
            )
            == ()
        )

    def test_the_application_imports_only_the_domain_and_itself(
        self,
    ) -> None:
        assert (
            _violations(
                PACKAGE_ROOT / "application",
                frozenset(
                    {
                        "domain",
                        "application",
                    },
                ),
            )
            == ()
        )

    def test_the_infrastructure_never_imports_the_services(
        self,
    ) -> None:
        violations = tuple(
            f"{path} imports {module}"
            for path in python_files(
                directory=PACKAGE_ROOT / "infrastructure",
            )
            for module in full_imports(
                source=path.read_text(
                    encoding="utf-8",
                ),
            )
            if module.startswith(
                "wordwinnow.services",
            )
        )

        assert violations == ()

    def test_the_scan_catches_a_domain_module_that_imports_a_transport(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        domain = tmp_path / "domain"

        domain.mkdir()

        (domain / "leak.py").write_text(
            data="from httpx2 import (\n    AsyncClient,\n)\n",
            encoding="utf-8",
        )

        assert (
            _violations(
                domain,
                frozenset(
                    {
                        "domain",
                    },
                ),
            )
            != ()
        )

    def test_the_scan_catches_an_application_module_that_imports_the_infrastructure(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        application = tmp_path / "application"

        application.mkdir()

        (application / "leak.py").write_text(
            data="from wordwinnow.infrastructure.clock import (\n    SystemClock,\n)\n",
            encoding="utf-8",
        )

        assert (
            _violations(
                application,
                frozenset(
                    {
                        "domain",
                        "application",
                    },
                ),
            )
            != ()
        )

    def test_the_scan_catches_an_application_module_that_imports_any_library(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        application = tmp_path / "application"

        application.mkdir()

        (application / "leak.py").write_text(
            data="from numpy import (\n    array,\n)\n",
            encoding="utf-8",
        )

        assert (
            _violations(
                application,
                frozenset(
                    {
                        "domain",
                        "application",
                    },
                ),
            )
            != ()
        )
