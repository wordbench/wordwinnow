"""
The console script's entry: it starts the `wordwinnow` command, and says so on
standard error when it is a terminal and the start is taking long.
"""

from collections.abc import (
    Callable,
)
from functools import (
    partial,
)
from os import (
    isatty,
    write,
)
from threading import (
    Timer,
)
from typing import (
    Final,
)

# NOTE:
# A warm import of the command took 1.15 to 1.22 s, and a warm `--help` 1.1 to 2.4 s, so three seconds stays above an
# ordinary warm start and the notice appears only for a start that is slow.
#
# The first output then comes within 3.5 s of the process starting, because the timer counts from the moment it is
# armed, after Python itself has started, and the half second between the two is what Python's own start may take.
THRESHOLD_SECONDS: Final = 3.0

NOTICE: Final = b"Wordwinnow is starting; the first run after an install takes longer.\n"

_STANDARD_ERROR: Final = 2


def _load() -> Callable[[], None]:
    # NOTE:
    # The import is deferred because it has a precondition: the timer is armed before its cost begins, since the
    # import is the wait that the notice is for, and an import at the top of this module would be that wait with
    # nothing yet able to say so.
    from wordwinnow.services.cli.main import (
        main as command_line,
    )

    return command_line


def _enter(
    threshold: float,
    is_terminal: Callable[[], bool],
    write_notice: Callable[[bytes], object],
    load: Callable[[], Callable[[], None]],
    /,
) -> None:
    """
    Load the command while a daemon timer waits, write the notice once if the
    load outlasts the threshold on a terminal, and then run the command.
    """

    timer = Timer(
        interval=threshold,
        function=write_notice,
        args=(NOTICE,),
    )

    timer.daemon = True

    if is_terminal():
        timer.start()

    try:
        command = load()

    finally:
        timer.cancel()

    command()


def main() -> None:
    """
    The console script's entry point.
    """

    _enter(
        THRESHOLD_SECONDS,
        partial(
            isatty,
            _STANDARD_ERROR,
        ),
        partial(
            write,
            _STANDARD_ERROR,
        ),
        _load,
    )
