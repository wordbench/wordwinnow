"""
What the command line writes on a stream that is not UTF-8.

A character the stream cannot encode is written as one that it can, taking the
cells the original took, so that nothing a console measured moves.

Text is judged by grapheme, as Rich lays it out: a grapheme the stream can
encode whole is written as it is.

The nearest character comes first, then a letter's base letter where it takes
the same cells, and a `?` for each cell otherwise.

A grapheme that the run of unencodable characters cuts is written for its part
inside the run, in the cells it takes less those of its characters outside the
run, and nothing outside the run is changed or dropped.
"""

import sys
from codecs import (
    lookup,
    register_error,
)
from collections.abc import (
    Iterable,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
)
from unicodedata import (
    category,
    normalize,
)

from rich.cells import (
    cell_len,
    split_graphemes,
)

__all__ = (
    "CELLS_HANDLER",
    "keep_cells_on",
    "keep_cells_on_standard_streams",
    "writes_utf8",
)

# NOTE:
# The name the encode error handler is registered under, for a stream's `reconfigure(errors=...)`.
CELLS_HANDLER: Final = "wordwinnow-cells"

# NOTE:
# The characters a terminal that cannot show them reads as one it can, by code point, with what each is written as.
#
# The table is closed: a character it does not name goes on to its base letter, if it is a letter, and then to `?`.
_NEAREST_GROUPS: Final = (
    (
        (
            0x2018,
            0x2019,
            0x201A,
            0x201B,
            0x2032,
        ),
        "'",
    ),
    (
        (
            0x201C,
            0x201D,
            0x201E,
            0x201F,
            0x2033,
        ),
        '"',
    ),
    (
        (
            *range(
                0x2010,
                0x2016,
            ),
            0x2212,
        ),
        "-",
    ),
    (
        (0x2026,),
        ".",
    ),
    (
        (
            0x00A0,
            0x2007,
            0x202F,
        ),
        " ",
    ),
    (
        (0x00D7,),
        "x",
    ),
)

_NEAREST: Final = MappingProxyType(
    mapping={
        chr(
            code,
        ): written
        for (
            codes,
            written,
        ) in _NEAREST_GROUPS
        for code in codes
    },
)


def _encodable(
    text: str,
    encoding: str,
    /,
) -> bool:
    try:
        text.encode(
            encoding=encoding,
        )

    except UnicodeEncodeError:
        return False

    return True


def _base_letter(
    character: str,
    /,
) -> str | None:
    """
    The character itself without its combining marks, when it is a letter and
    that leaves exactly one character, and nothing otherwise.
    """

    # WARN:
    # Only a letter: the NFKD form of a symbol such as `≠` is `=` and a combining overlay, and dropping the overlay
    # would write an opposite as `=`, the report's mark for a synonym.
    if not category(
        character,
    ).startswith(
        "L",
    ):
        return None

    bare = "".join(
        part
        for part in normalize(
            "NFKD",
            character,
        )
        if not category(
            part,
        ).startswith(
            "M",
        )
    )

    return (
        bare
        if len(
            bare,
        )
        == 1
        else None
    )


def _written_for(
    character: str,
    encoding: str,
    /,
) -> str:
    nearest = _NEAREST.get(
        character,
    )

    if nearest is not None and _encodable(
        nearest,
        encoding,
    ):
        return nearest

    base = _base_letter(
        character,
    )

    # NOTE:
    # A base letter is taken only where it takes the cells the character takes, since a fullwidth letter's is a cell
    # narrower and Rich laid the line out for the wider one.
    if (
        base is not None
        and _encodable(
            base,
            encoding,
        )
        and cell_len(
            text=base,
        )
        == cell_len(
            text=character,
        )
    ):
        return base

    return "?" * cell_len(
        text=character,
    )


def _written_inside(
    inside: str,
    room: int,
    encoding: str,
    /,
) -> str:
    """
    What the part of a grapheme inside a run is written as, in `room` cells: a
    single character by its own rule where that takes the cells, and a `?` for
    each cell otherwise.
    """

    if (
        len(
            inside,
        )
        == 1
    ):
        form = _written_for(
            inside,
            encoding,
        )

        if (
            cell_len(
                text=form,
            )
            == room
        ):
            return form

    return "?" * room


def _in_cells(
    error: UnicodeError,
    /,
) -> tuple[str, int]:
    """
    The encode error handler: each grapheme the run touches, as the characters
    the stream can encode that take the cells of its part inside the run.
    """

    if not isinstance(
        error,
        UnicodeEncodeError,
    ):
        raise error

    text = error.object

    # NOTE:
    # No grapheme crosses the end of a line, so only the line that holds the run is divided.
    first = (
        text.rfind(
            "\n",
            0,
            error.start,
        )
        + 1
    )

    last = text.find(
        "\n",
        error.end,
    )

    line = text[
        first : (
            len(
                text,
            )
            if last < 0
            else last
        )
    ]

    (
        spans,
        _,
    ) = split_graphemes(
        text=line,
    )

    written: list[str] = []

    for (
        begin,
        stop,
        cells,
    ) in spans:
        begin += first

        stop += first

        if stop <= error.start or begin >= error.end:
            continue

        inside_start = max(
            begin,
            error.start,
        )

        inside_end = min(
            stop,
            error.end,
        )

        # WARN:
        # The handler never returns past the end of the run: Rich's graphemes join a control character, an escape, a
        # tab, or the character after a joiner to the grapheme before them, and resuming at a grapheme's end would
        # drop characters the stream writes itself, such as the escape that ends a styled word.
        written.append(
            _written_inside(
                text[inside_start:inside_end],
                max(
                    cells
                    - cell_len(
                        text=text[begin:inside_start],
                    )
                    - cell_len(
                        text=text[inside_end:stop],
                    ),
                    0,
                ),
                error.encoding,
            ),
        )

    return (
        "".join(
            written,
        ),
        error.end,
    )


register_error(
    CELLS_HANDLER,
    _in_cells,
)


def writes_utf8(
    *,
    stream: object,
) -> bool:
    """
    Whether `stream` encodes as UTF-8, by its codec rather than its spelling,
    a stream that names no encoding being read as Rich reads it, as UTF-8.
    """

    encoding = getattr(
        stream,
        "encoding",
        None,
    )

    if encoding is None:
        return True

    try:
        return (
            lookup(
                encoding,
            ).name
            == "utf-8"
        )

    except LookupError:
        return False


def keep_cells_on(
    *,
    streams: Iterable[object],
) -> None:
    """
    Set the rule on each stream that does not encode as UTF-8 and can take an
    error handler, and leave every other stream as it is.
    """

    for stream in streams:
        reconfigure = getattr(
            stream,
            "reconfigure",
            None,
        )

        if reconfigure is None or writes_utf8(
            stream=stream,
        ):
            continue

        reconfigure(
            errors=CELLS_HANDLER,
        )


def keep_cells_on_standard_streams() -> None:
    """
    Set the rule on standard output and standard error as they are now.
    """

    # NOTE:
    # Read through the module at the call: a test, or a caller that rebinds a stream, must see the streams as they are
    # when the command starts.
    keep_cells_on(
        streams=(
            sys.stdout,
            sys.stderr,
        ),
    )
