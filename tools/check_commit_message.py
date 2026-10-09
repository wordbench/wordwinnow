#!/usr/bin/env python3
# Copyright 2026 Oleg Turzhanskii
# SPDX-License-Identifier: Apache-2.0
"""
Validates one commit message against the Conventional Commits form.

    python check_commit_message.py MESSAGE_FILE
    git log -1 --pretty=%B | python check_commit_message.py -

This is a linter for a message rather than a workflow.

It reads text and exits 0 or 1.

It does not touch a repository, install anything, or run any Git command.
"""

from pathlib import (
    Path,
)
from re import (
    compile as compile_pattern,
)
from sys import (
    argv,
    stderr,
    stdin,
)
from typing import (
    Final,
)

TYPES: Final = (
    "feat",
    "fix",
    "refactor",
    "perf",
    "style",
    "test",
    "docs",
    "build",
    "ops",
    "chore",
)

_TYPE_ALTERNATIVES: Final = "|".join(
    TYPES,
)

SUBJECT: Final = compile_pattern(
    pattern=(
        rf"^(?P<type>{_TYPE_ALTERNATIVES})"
        rf"(?:\((?P<scope>[^)]+)\))?!?: (?P<description>.+)$"
    ),
)

SUBJECT_LIMIT: Final = 72

ISSUE_SCOPE: Final = compile_pattern(
    pattern=r"#?\d+",
)


def _check(
    message: str,
    /,
) -> tuple[str, ...]:
    """
    Return every problem with `message`, in the order they occur.
    """

    lines = message.rstrip().splitlines()

    if not lines or not lines[0].strip():
        return ("the message is empty",)

    subject = lines[0]

    match = SUBJECT.match(
        string=subject,
    )

    if match is None:
        allowed = ", ".join(
            TYPES,
        )

        return (f"subject does not match `<type>(<scope>): <description>`; type must be one of {allowed}",)

    problems: list[str] = []

    description = match.group(
        "description",
    )

    if description[0].isupper():
        problems.append(
            "description is capitalized",
        )

    if description.endswith(
        ".",
    ):
        problems.append(
            "description ends with a period",
        )

    length = len(
        subject,
    )

    if length > SUBJECT_LIMIT:
        problems.append(
            f"subject is {length} characters, over {SUBJECT_LIMIT}",
        )

    scope = match.group(
        "scope",
    )

    if scope is not None and ISSUE_SCOPE.fullmatch(
        string=scope,
    ):
        problems.append(
            "scope is an issue identifier",
        )

    line_count = len(
        lines,
    )

    if line_count > 1 and lines[1].strip():
        problems.append(
            "no blank line between the subject and the body",
        )

    for (
        number,
        line,
    ) in enumerate(
        iterable=lines[2:],
        start=3,
    ):
        breaking = line.startswith(
            "BREAKING CHANGE",
        )

        well_formed = line.startswith(
            "BREAKING CHANGE: ",
        )

        if breaking and not well_formed:
            problems.append(
                f"line {number}: breaking change must read `BREAKING CHANGE: <description>`",
            )

    return tuple(
        problems,
    )


def main() -> int:
    """
    Read the message the command line names and report what is wrong with it.
    """

    argument_count = len(
        argv,
    )

    if argument_count != 2:
        print(
            __doc__,
            file=stderr,
        )

        return 2

    message = (
        stdin.read()
        if argv[1] == "-"
        else Path(
            argv[1],
        ).read_text(
            encoding="utf-8",
        )
    )

    problems = _check(
        message,
    )

    for problem in problems:
        print(
            f"commit message: {problem}",
            file=stderr,
        )

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(
        main(),
    )
