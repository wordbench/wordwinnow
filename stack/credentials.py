#!/usr/bin/env python3
"""
Generates the Compose stack's passwords into `.env`, where Compose reads them.

    python3 stack/credentials.py

Run from the repository's root before Compose by `make up` and `make up-full`.

For each of ClickHouse's and Grafana's passwords, a line `.env` already has
is never touched, and a line it lacks is appended with a new random value.

An empty value stops the run, since Compose refuses one too and this program
never edits a line, and it changes no file in that case.

No value is ever printed.
"""

from collections.abc import (
    Sequence,
)
from pathlib import (
    Path,
)
from re import (
    compile as compile_pattern,
)
from secrets import (
    token_urlsafe,
)
from sys import (
    stderr,
)
from typing import (
    Final,
)

_ENV: Final = Path(
    ".env",
)

# NOTE:
# What the password of ClickHouse's user and of Grafana's user is called, with the words that say what each is for,
# the command that starts the service it belongs to, and what a published one lets a web page do.
_PASSWORDS: Final = (
    (
        "WORDWINNOW_CLICKHOUSE_PASSWORD",
        "the password of ClickHouse's user wordwinnow",
        "make up",
        ", so a web page can use ClickHouse with it while the stack runs",
    ),
    (
        "GRAFANA_ADMIN_PASSWORD",
        "the password of Grafana's user admin",
        "make up-full",
        "",
    ),
)

# NOTE:
# The password this repository published before it generated one, which a `.env` may still hold.
_PUBLISHED: Final = "wordwinnow"

_COMMENT: Final = compile_pattern(
    pattern=r"\s+#.*$",
)


def _value_of(
    lines: Sequence[str],
    name: str,
    /,
) -> str | None:
    """
    What `name` is set to by the last of `lines` that sets it, without its
    quotes or its trailing comment, or `None` when no line sets it.

    A line sets a name when, after any spaces and an optional `export`, it
    starts with the name, optional spaces, and an equals sign.
    """

    pattern = compile_pattern(
        pattern=rf"\s*(?:export\s+)?{name}\s*=(?P<value>.*)",
    )

    value: str | None = None

    for line in lines:
        matched = pattern.fullmatch(
            string=line.rstrip(
                "\r",
            ),
        )

        if matched is not None:
            value = matched["value"]

    if value is None:
        return None

    text = value.strip()

    if text[:1] in {
        "'",
        '"',
    }:
        closing = text.find(
            text[0],
            1,
        )

        if closing != -1:
            return text[1:closing]

    return _COMMENT.sub(
        repl="",
        string=value,
    ).strip()


def main() -> int:
    """
    Add to `.env`, in the current directory, the passwords it lacks.

    Returns 0 when `.env` holds both afterward, and 1 when a value is empty or
    the file cannot be read or written.
    """

    try:
        contents: bytes | None = _ENV.read_bytes()

    except FileNotFoundError:
        contents = None

    except OSError as exception:
        print(
            f"cannot read .env: {exception.strerror or exception}",
            file=stderr,
        )

        return 1

    lines = (
        []
        if contents is None
        else contents.decode(
            encoding="utf-8",
            errors="replace",
        ).split(
            sep="\n",
        )
    )

    current = {
        name: _value_of(
            lines,
            name,
        )
        for (
            name,
            _,
            _,
            _,
        ) in _PASSWORDS
    }

    empty = False

    for (
        name,
        _,
        _,
        _,
    ) in _PASSWORDS:
        if current[name] == "":
            empty = True

            print(
                f"{name} in .env is empty, which Compose refuses; delete the line, then run make up, to have one "
                "generated",
                file=stderr,
            )

    if empty:
        return 1

    appended: list[str] = []

    messages: list[tuple[str, bool]] = []

    if contents is None:
        messages.append(
            (
                "created .env, readable by you alone, for the stack's generated passwords",
                False,
            ),
        )

    for (
        name,
        purpose,
        start,
        consequence,
    ) in _PASSWORDS:
        value = current[name]

        if value is None:
            appended.append(
                f"{name}={
                    token_urlsafe(
                        nbytes=32,
                    )
                }\n",
            )

            messages.append(
                (
                    f"generated {name} in .env, {purpose}",
                    False,
                ),
            )

        elif value == _PUBLISHED:
            messages.append(
                (
                    f"{name} in .env is {_PUBLISHED}, which this repository once published{consequence}; delete the "
                    f"line, then run {start}, to have one generated",
                    True,
                ),
            )

    if appended:
        try:
            if contents is None:
                _ENV.touch(
                    mode=0o600,
                    exist_ok=False,
                )

                _ENV.chmod(
                    mode=0o600,
                )

            # NOTE:
            # A file that does not end with a line break gets one first, so that the new line is a line of its own;
            # every earlier byte stays as it was.
            separator = (
                "\n"
                if contents
                and not contents.endswith(
                    b"\n",
                )
                else ""
            )

            with _ENV.open(
                mode="ab",
            ) as handle:
                handle.write(
                    (
                        separator
                        + "".join(
                            appended,
                        )
                    ).encode(
                        encoding="utf-8",
                    ),
                )

        except OSError as exception:
            print(
                f"cannot write .env: {exception.strerror or exception}",
                file=stderr,
            )

            return 1

    for (
        message,
        notice,
    ) in messages:
        print(
            message,
            file=stderr if notice else None,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(),
    )
