"""
One Python minor version, named wherever it is named: `.python-version`, the
one range `requires-python` is written as, every Python stage of the
Dockerfile, and the `--python` of every install command the guides give.

The test reads the files and runs nothing, so it needs no network.

Each scan is a function of the file's text, and its positive control runs the
same function on a synthetic file that names the wrong version.
"""

from re import (
    IGNORECASE,
    MULTILINE,
)
from re import (
    compile as compile_pattern,
)
from shlex import (
    split,
)
from tomllib import (
    loads,
)
from typing import (
    Final,
    final,
)

from tests.fitness._scan import (
    PACKAGE_ROOT,
)

_ROOT: Final = PACKAGE_ROOT.parent

_GUIDES: Final = (
    "README.md",
    "docs/getting-started.md",
)

# NOTE:
# The one spelling this project writes `requires-python` in: from the minor version, up to and not including the next.
#
# Another spelling of the same range, such as `==3.14.*`, which `uv lock` writes into the lock, fails the scan, and is
# rewritten in this one.
_ONE_MINOR: Final = compile_pattern(
    pattern=r">=(\d+)\.(\d+),\s*<(\d+)\.(\d+)",
)

# NOTE:
# A Python stage of the Dockerfile as Docker reads it: any case, any indentation, any options such as `--platform=`,
# and the image named with or without its registry and repository, with or without a tag or a digest.
#
# A stage whose tag names no minor version, such as `python:3` or `python:latest`, or no tag, is reported, because
# Docker would run it on whatever version the tag resolves to.
_IMAGE: Final = compile_pattern(
    pattern=r"^[ \t]*FROM[ \t]+(?:--\S+[ \t]+)*(?:\S+/)?python(?::([^\s@]+))?(?:@\S+)?(?=\s|$)",
    flags=MULTILINE | IGNORECASE,
)


def _read(
    name: str,
    /,
) -> str:
    return (_ROOT / name).read_text(
        encoding="utf-8",
    )


def _minor() -> str:
    return _read(
        ".python-version",
    ).strip()


def _allowed_minor(
    requires_python: str,
    /,
) -> str | None:
    """
    The one minor version a range in this project's spelling allows, or `None`
    for a range spelled otherwise or allowing more or fewer.
    """

    matched = _ONE_MINOR.fullmatch(
        string=requires_python.strip(),
    )

    if matched is None:
        return None

    (
        lower_major,
        lower_minor,
        upper_major,
        upper_minor,
    ) = matched.groups()

    lower = int(
        lower_minor,
    )

    upper = int(
        upper_minor,
    )

    if lower_major != upper_major or upper != lower + 1:
        return None

    return f"{lower_major}.{lower_minor}"


def _expected_range(
    minor: str,
    /,
) -> str:
    (
        major,
        _,
        number,
    ) = minor.partition(
        ".",
    )

    successor = (
        int(
            number,
        )
        + 1
    )

    return f">={minor},<{major}.{successor}"


def _range_problems(
    requires_python: str,
    minor: str,
    /,
) -> tuple[str, ...]:
    if (
        _allowed_minor(
            requires_python,
        )
        == minor
    ):
        return ()

    return (
        f"requires-python = {requires_python!r}, where this check reads only '{
            _expected_range(
                minor,
            )
        }'",
    )


def _image_problems(
    dockerfile: str,
    minor: str,
    /,
) -> tuple[str, ...]:
    found = tuple(
        _IMAGE.finditer(
            string=dockerfile,
        ),
    )

    if not found:
        return ("the Dockerfile has no stage that names a Python image",)

    problems: list[str] = []

    for stage in found:
        tag = (
            stage.group(
                1,
            )
            or ""
        )

        if tag == minor or tag.startswith(
            (
                f"{minor}.",
                f"{minor}-",
            ),
        ):
            continue

        problems.append(
            f"`{
                ' '.join(
                    stage.group(
                        0,
                    ).split(),
                )
            }` is not {minor}",
        )

    return tuple(
        problems,
    )


def _python_option(
    command: tuple[str, ...],
    /,
) -> str | None:
    for (
        index,
        argument,
    ) in enumerate(
        iterable=command,
    ):
        if argument == "--python":
            following = command[index + 1 :]

            return following[0] if following else None

        if argument.startswith(
            "--python=",
        ):
            return argument.removeprefix(
                "--python=",
            )

    return None


# NOTE:
# An install command is a line that begins with `uv tool install` once its indentation is taken off, which is how a
# fence inside a list item holds one.
#
# A command quoted in a paragraph's inline code stays unseen, because prose that names `uv tool install` without its
# arguments would otherwise read as a command.
def _install_lines(
    guide: str,
    /,
) -> tuple[str, ...]:
    return tuple(
        stripped
        for stripped in (line.lstrip() for line in guide.splitlines())
        if stripped.startswith(
            "uv tool install",
        )
    )


def _guide_problems(
    guide: str,
    minor: str,
    /,
) -> tuple[str, ...]:
    commands = tuple(
        tuple(
            split(
                s=line,
            ),
        )
        for line in _install_lines(
            guide,
        )
    )

    if not commands:
        return ("the guide has no `uv tool install` command",)

    problems: list[str] = []

    for command in commands:
        option = _python_option(
            command,
        )

        if option != minor:
            problems.append(
                f"`{
                    ' '.join(
                        command,
                    )
                }` names --python {option}, not {minor}",
            )

    return tuple(
        problems,
    )


@final
class TestOnePythonVersion:
    def test_requires_python_is_the_one_spelling_of_the_version_files_minor_version(
        self,
    ) -> None:
        assert not _range_problems(
            loads(
                _read(
                    "pyproject.toml",
                ),
            )["project"]["requires-python"],
            _minor(),
        )

    def test_every_python_stage_of_the_dockerfile_names_that_minor_version(
        self,
    ) -> None:
        assert not _image_problems(
            _read(
                "Dockerfile",
            ),
            _minor(),
        )

    def test_every_install_command_of_the_guides_names_that_minor_version_with_python(
        self,
    ) -> None:
        for guide in _GUIDES:
            assert not _guide_problems(
                _read(
                    guide,
                ),
                _minor(),
            ), guide

    def test_the_scans_catch_a_range_spelled_otherwise_or_allowing_more_or_another_version(
        self,
    ) -> None:
        # TEST:
        # Positive controls: the range scan on a range with no upper bound, on one that allows two minor versions, on
        # one that allows another, which `_allowed_minor` must not read as the version asked for, and on `==3.14.*`,
        # which allows 3.14 and no other but is not the spelling this check reads.
        for requires_python in (
            ">=3.14",
            ">=3.14,<3.16",
            ">=3.13,<3.14",
            "==3.14.*",
        ):
            assert _range_problems(
                requires_python,
                "3.14",
            )

        assert _range_problems(
            "==3.14.*",
            "3.14",
        ) == ("requires-python = '==3.14.*', where this check reads only '>=3.14,<3.15'",)

        assert not _range_problems(
            ">=3.14,<3.15",
            "3.14",
        )

    def test_the_scans_catch_an_image_of_another_version(
        self,
    ) -> None:
        # TEST:
        # Positive controls: the image scan on a Dockerfile with one stage of another version, on one with none, and
        # on a stage of another version written with a `--platform` option, with a registry and a repository, in lower
        # case, or indented, and on a stage whose tag names no minor version, whose tag is missing, or whose digest
        # stands alone, each beside a stage of the right one; and, as the negative control, on another image of a
        # registry beside a right one.
        assert _image_problems(
            "FROM python:3.13.1-slim AS build\nFROM python:3.14.9-slim\n",
            "3.14",
        ) == ("`FROM python:3.13.1-slim` is not 3.14",)

        assert _image_problems(
            "FROM scratch\n",
            "3.14",
        )

        for stage in (
            "FROM --platform=$BUILDPLATFORM python:3.13",
            "FROM docker.io/library/python:3.13",
            "from python:3.13",
            "  FROM python:3.13",
            "FROM python:3-slim",
            "FROM python:latest",
            "FROM python",
            f"FROM python@sha256:{'0' * 64}",
        ):
            assert _image_problems(
                f"{stage} AS build\nFROM python:3.14.9-slim\n",
                "3.14",
            ) == (
                f"`{
                    ' '.join(
                        stage.split(),
                    )
                }` is not 3.14",
            ), stage

        assert not _image_problems(
            "FROM python:3.14.9-slim AS build\nFROM python:3.14.9-slim\n",
            "3.14",
        )

        assert not _image_problems(
            "FROM ghcr.io/astral-sh/uv:0.13.5 AS uv\nFROM python:3.14.9-slim\n",
            "3.14",
        )

    def test_the_scans_catch_a_guide_whose_command_lacks_python_or_names_another_version(
        self,
    ) -> None:
        # TEST:
        # Positive controls: the guide scan on a synthetic guide whose command lacks `--python`, on one that names
        # another version in either spelling, and on one with no install command at all.
        for command in (
            "uv tool install git+https://example.invalid/foo",
            "uv tool install --python 3.13 git+https://example.invalid/foo",
            "uv tool install --python=3.13 git+https://example.invalid/foo",
            "uv tool install git+https://example.invalid/foo --python",
        ):
            assert _guide_problems(
                f"```sh\n{command}\n```\n",
                "3.14",
            ), command

        assert _guide_problems(
            "Install it with uv.\n",
            "3.14",
        )

        # TEST:
        # An install command that omits `--python` inside a fence indented under a list item, beside a good one at the
        # left margin, which the scan must still report.
        assert _guide_problems(
            "```sh\nuv tool install --python 3.14 git+https://example.invalid/foo\n```\n"
            "\n- Or:\n\n  ```sh\n  uv tool install git+https://example.invalid/foo\n  ```\n",
            "3.14",
        ) == ("`uv tool install git+https://example.invalid/foo` names --python None, not 3.14",)

        for command in (
            "uv tool install --python 3.14 git+https://example.invalid/foo",
            "uv tool install --python=3.14 git+https://example.invalid/foo",
            "  uv tool install --python 3.14 git+https://example.invalid/foo",
        ):
            assert not _guide_problems(
                f"```sh\n{command}\n```\n",
                "3.14",
            ), command
