"""
The console script's entry: the notice it writes when the command's start is
slow, and every case in which it writes none.
"""

from collections.abc import (
    Callable,
)
from functools import (
    partial,
)
from json import (
    loads,
)
from os import (
    close,
    openpty,
    read,
)
from select import (
    select,
)
from subprocess import (
    PIPE,
    run,
)
from sys import (
    executable,
)
from threading import (
    Event,
)
from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from wordwinnow.services.cli.entry import (
    NOTICE,
    _enter,
)

_TEXT: Final = "Wordwinnow is starting; the first run after an install takes longer."

_THRESHOLD: Final = 0.05

# NOTE:
# Four times the threshold is how long a test waits before it says that nothing was written, so that a timer left
# armed after the load returned, or one armed for a command that runs long, would have fired by then.
_AFTER: Final = 4 * _THRESHOLD

# NOTE:
# How long a load waits for the notice before the test fails, far longer than the threshold and far shorter than a
# hung run is worth waiting for.
_PATIENCE: Final = 5.0

# NOTE:
# What a fresh interpreter has imported once the entry is, outside the standard library: the entry's own packages, and
# nothing of the command.
_IMPORTED_BY_THE_ENTRY: Final = (
    "wordwinnow",
    "wordwinnow.services",
    "wordwinnow.services.cli",
    "wordwinnow.services.cli.entry",
)

_IMPORT_THE_ENTRY: Final = (
    "import json, sys\n"
    "before = set(sys.modules)\n"
    "import wordwinnow.services.cli.entry\n"
    "new = set(sys.modules) - before\n"
    "print(json.dumps(sorted(name for name in new if name.split('.')[0] not in sys.stdlib_module_names)))\n"
)

# NOTE:
# The child program of the two tests that run the real `main()`, so that what `main()` tests and where it writes are
# under test, and not what a recorder saw of its arguments.
#
# It sets the threshold to zero so that the timer is immediate, and replaces the load with one that returns only once
# the timer thread has ended, so that no assertion depends on how fast anything is.
_RUN_THE_ENTRY: Final = (
    "from threading import Timer, enumerate as running\n"
    "from time import sleep\n"
    "from wordwinnow.services.cli import entry\n"
    "def load():\n"
    "    while any(isinstance(thread, Timer) for thread in running()):\n"
    "        sleep(0.001)\n"
    "    return lambda: None\n"
    "entry.THRESHOLD_SECONDS = 0.0\n"
    "entry._load = load\n"
    "entry.main()\n"
)


@final
class _Writer:
    """
    What the timer writes to, recording each write and saying when the first
    came.
    """

    def __init__(
        self,
    ) -> None:
        self.writes: Final[list[bytes]] = []

        self.written: Final = Event()

    def __call__(
        self,
        data: bytes,
        /,
    ) -> int:
        self.writes.append(
            data,
        )

        self.written.set()

        return len(
            data,
        )


def _terminal() -> bool:
    return True


def _not_a_terminal() -> bool:
    return False


def _command() -> None:
    return None


def _wait(
    seconds: float,
    /,
) -> None:
    Event().wait(
        timeout=seconds,
    )


def _loaded_at_once() -> Callable[[], None]:
    return _command


def _loaded_after(
    seconds: float,
    /,
) -> Callable[[], Callable[[], None]]:
    def load() -> Callable[[], None]:
        _wait(
            seconds,
        )

        return _command

    return load


# NOTE:
# A load that returns only once the notice has been written, so that the test can say the notice came while the load
# was running, and fails when it does not come.
def _loaded_once_noticed(
    writer: _Writer,
    /,
    *,
    then: float = 0.0,
) -> Callable[[], Callable[[], None]]:
    def load() -> Callable[[], None]:
        assert writer.written.wait(
            timeout=_PATIENCE,
        ), "no notice was written while the command was loading"

        _wait(
            then,
        )

        return _command

    return load


# WARN:
# The terminal is read while the test still holds its other end open, because on macOS a read after the last close
# returns nothing, where on Linux it returns what was written.
#
# The wait is bounded by `select`, because the kernel may hand over a terminal's output a moment after the child
# exits.
def _first_line(
    master: int,
    patience: float,
    /,
) -> bytes:
    received = b""

    while (
        b"\n" not in received
        and select(
            (master,),
            (),
            (),
            patience,
        )[0]
    ):
        received += read(
            master,
            4096,
        )

    return received


@final
class TestEntry:
    def test_a_load_that_outlasts_the_threshold_on_a_terminal_writes_the_notice_once_before_it_returns(
        self,
    ) -> None:
        writer = _Writer()

        _enter(
            _THRESHOLD,
            _terminal,
            writer,
            _loaded_once_noticed(
                writer,
            ),
        )

        assert writer.writes == [
            f"{_TEXT}\n".encode(),
        ]

    def test_a_load_that_returns_at_once_on_a_terminal_writes_nothing(
        self,
    ) -> None:
        writer = _Writer()

        _enter(
            _THRESHOLD,
            _terminal,
            writer,
            _loaded_at_once,
        )

        _wait(
            _AFTER,
        )

        assert writer.writes == []

    def test_a_load_that_outlasts_the_threshold_off_a_terminal_writes_nothing(
        self,
    ) -> None:
        writer = _Writer()

        _enter(
            _THRESHOLD,
            _not_a_terminal,
            writer,
            _loaded_after(
                2 * _THRESHOLD,
            ),
        )

        _wait(
            _AFTER,
        )

        assert writer.writes == []

    def test_a_command_that_runs_long_after_a_quick_load_gets_no_notice(
        self,
    ) -> None:
        writer = _Writer()

        def load() -> Callable[[], None]:
            return partial(
                _wait,
                _AFTER,
            )

        _enter(
            _THRESHOLD,
            _terminal,
            writer,
            load,
        )

        assert writer.writes == []

    def test_a_load_past_twice_the_threshold_still_gets_one_notice(
        self,
    ) -> None:
        writer = _Writer()

        _enter(
            _THRESHOLD,
            _terminal,
            writer,
            _loaded_once_noticed(
                writer,
                then=2 * _THRESHOLD,
            ),
        )

        assert writer.writes == [
            f"{_TEXT}\n".encode(),
        ]

    def test_a_load_that_raises_reaches_the_caller_and_writes_nothing_after_it(
        self,
    ) -> None:
        writer = _Writer()

        def load() -> Callable[[], None]:
            raise RuntimeError(
                "the import failed",
            )

        with raises(
            expected_exception=RuntimeError,
            match="the import failed",
        ):
            _enter(
                _THRESHOLD,
                _terminal,
                writer,
                load,
            )

        _wait(
            _AFTER,
        )

        assert writer.writes == []

    def test_the_notice_is_one_line_that_says_it_is_starting_and_nothing_of_an_error(
        self,
    ) -> None:
        assert f"{_TEXT}\n".encode() == NOTICE

        assert NOTICE.endswith(
            b"\n",
        )

        assert not any(byte < 0x20 or byte == 0x7F for byte in NOTICE[:-1])

        assert b"error" not in NOTICE.lower()

    def test_importing_the_entry_imports_nothing_of_the_command(
        self,
    ) -> None:
        result = run(
            args=[
                executable,
                "-I",
                "-c",
                _IMPORT_THE_ENTRY,
            ],
            capture_output=True,
            check=False,
            text=True,
        )

        assert result.returncode == 0, result.stderr

        assert (
            tuple(
                loads(
                    s=result.stdout,
                ),
            )
            == _IMPORTED_BY_THE_ENTRY
        )

    def test_the_console_entry_writes_the_notice_to_standard_error_when_it_alone_is_a_terminal(
        self,
    ) -> None:
        (
            master,
            slave,
        ) = openpty()

        result = run(
            args=[
                executable,
                "-I",
                "-c",
                _RUN_THE_ENTRY,
            ],
            check=False,
            stdin=slave,
            stdout=PIPE,
            stderr=slave,
        )

        terminal = _first_line(
            master,
            _PATIENCE,
        )

        close(
            fd=slave,
        )

        close(
            fd=master,
        )

        assert result.returncode == 0, terminal

        assert result.stdout == b""

        assert (
            terminal.replace(
                b"\r\n",
                b"\n",
            )
            == NOTICE
        )

    def test_the_console_entry_writes_nothing_when_standard_error_alone_is_not_a_terminal(
        self,
    ) -> None:
        (
            master,
            slave,
        ) = openpty()

        result = run(
            args=[
                executable,
                "-I",
                "-c",
                _RUN_THE_ENTRY,
            ],
            check=False,
            stdin=slave,
            stdout=slave,
            stderr=PIPE,
        )

        terminal = _first_line(
            master,
            0.0,
        )

        close(
            fd=slave,
        )

        close(
            fd=master,
        )

        assert result.returncode == 0, result.stderr

        assert result.stderr == b""

        assert terminal == b""
