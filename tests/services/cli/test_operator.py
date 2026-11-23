"""
The operator's view the command line shows on standard error: the learner's
line, and under it the dictionary client drawn as one loop.

The frames are drawn from recordings of the product's own dictionary stage
against a scripted provider, replayed into the view with a clock that moves
only as the recording does.
"""

from collections import (
    Counter,
)
from dataclasses import (
    replace,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from io import (
    StringIO,
)
from pathlib import (
    Path,
)
from re import (
    compile as compile_pattern,
)
from re import (
    search,
    sub,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
)
from rich.console import (
    Console,
    Group,
)
from rich.panel import (
    Panel,
)
from rich.text import (
    Text,
)

from tests.fakes.dictionary_stage import (
    ANSWERING,
    ANSWERING_SLOWLY,
    DOWN_AND_BACK,
    DOWN_LONGER_THAN_PATIENCE,
    MEASURED_LIMIT,
    NEVER_ANSWERING,
    REFUSING,
    SOME_REQUESTS_FAILING,
    DictionaryWaiting,
    LookedUp,
    Recording,
    Script,
    StageStarted,
    Watched,
    record,
)
from tests.fakes.dictionary_stage_scenarios import (
    EVERY_MECHANISM_IN_TURN,
    HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
    OUTAGE_LONGER_THAN_THE_ANALYSIS_WAITS,
    PROVIDER_THAT_ANSWERS_SLOWLY,
    TWENTY_FOUR_LOOKUPS_AT_ONCE,
)
from tests.fakes.progress import (
    held_words,
)
from tests.infrastructure.dictionary.doubles import (
    FakeMonotonicClock,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    LookupCounts,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    _RECOVERY_JITTER,
    LookupActivity,
    ProviderActivity,
    RecentRequest,
    RequestOutcome,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.services.cli.operator import (
    OperatorProgress,
)
from wordwinnow.services.cli.operator_look import (
    WORDS,
)
from wordwinnow.services.cli.operator_seen import (
    Keeper,
)
from wordwinnow.services.cli.operator_states import (
    RECOVERY_JITTER,
    State,
    next_clause,
)

_FIXTURES: Final = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
    / "fixtures"
    / "operator"
)

# NOTE:
# The frames of the reference set: its name, the script it is drawn from, the second of the recording, the width of
# the terminal, and whether the terminal can draw only ASCII.
_FRAMES: Final = (
    (
        "healthy",
        HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
        26.0,
        80,
        False,
    ),
    (
        "rate-limited",
        HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
        12.0,
        80,
        False,
    ),
    (
        "struggling",
        EVERY_MECHANISM_IN_TURN,
        24.5,
        80,
        False,
    ),
    (
        "backing-off",
        EVERY_MECHANISM_IN_TURN,
        18.0,
        80,
        False,
    ),
    (
        "failing-just-opened",
        EVERY_MECHANISM_IN_TURN,
        26.5,
        80,
        False,
    ),
    (
        "failing",
        EVERY_MECHANISM_IN_TURN,
        31.0,
        80,
        False,
    ),
    (
        "on-trial",
        EVERY_MECHANISM_IN_TURN,
        35.5,
        80,
        False,
    ),
    (
        "recovering",
        EVERY_MECHANISM_IN_TURN,
        47.5,
        80,
        False,
    ),
    (
        "no-answer",
        EVERY_MECHANISM_IN_TURN,
        85.0,
        80,
        False,
    ),
    (
        "slow",
        PROVIDER_THAT_ANSWERS_SLOWLY,
        20.0,
        80,
        False,
    ),
    (
        "let-go",
        OUTAGE_LONGER_THAN_THE_ANALYSIS_WAITS,
        68.5,
        80,
        False,
    ),
    (
        "idle-at-the-end",
        EVERY_MECHANISM_IN_TURN,
        119.0,
        80,
        False,
    ),
    (
        "crowd",
        TWENTY_FOUR_LOOKUPS_AT_ONCE,
        30.0,
        80,
        False,
    ),
    (
        "narrow-terminal",
        EVERY_MECHANISM_IN_TURN,
        31.0,
        60,
        False,
    ),
    (
        "ascii-on-trial",
        EVERY_MECHANISM_IN_TURN,
        35.5,
        80,
        True,
    ),
)

_FIVE: Final = (
    EVERY_MECHANISM_IN_TURN,
    HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
    PROVIDER_THAT_ANSWERS_SLOWLY,
    OUTAGE_LONGER_THAN_THE_ANALYSIS_WAITS,
    TWENTY_FOUR_LOOKUPS_AT_ONCE,
)

_EIGHT: Final = (
    ANSWERING,
    SOME_REQUESTS_FAILING,
    REFUSING,
    DOWN_AND_BACK,
    DOWN_LONGER_THAN_PATIENCE,
    NEVER_ANSWERING,
    ANSWERING_SLOWLY,
    MEASURED_LIMIT,
)

_ESCAPE: Final = compile_pattern(
    pattern=r"\x1b\[([0-9;]*)m",
)

_ANY_ESCAPE: Final = compile_pattern(
    pattern=r"\x1b\[[0-9;?]*[A-Za-z]",
)

_COLORS: Final = (
    "black",
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
)

_HUES: Final = frozenset(
    (
        "green",
        "yellow",
        "red",
        "magenta",
    ),
)

_RECORDINGS: Final[dict[Script, Recording]] = {}

_SECOND: Final = 1e-6


@final
class _Ascii(
    StringIO,
):
    """
    A stream that reports the encoding `ascii`, so Rich draws for a terminal
    that can show nothing else.
    """

    encoding = "ascii"


@final
class _Cell:
    """
    One cell of what a terminal receives: a character, and how it is drawn.
    """

    def __init__(
        self,
        *,
        character: str,
        bold: bool,
        dim: bool,
        reverse: bool,
        foreground: str | None,
        background: str | None,
    ) -> None:
        self.character: Final = character

        self.bold: Final = bold

        self.dim: Final = dim

        self.reverse: Final = reverse

        self.foreground: Final = foreground

        self.background: Final = background


def _recorded(
    script: Script,
    /,
) -> Recording:
    if script not in _RECORDINGS:
        _RECORDINGS[script] = record(
            script=script,
        )

    return _RECORDINGS[script]


def _asked_console(
    *,
    columns: int,
    rows: int = 200,
    ascii_form: bool = False,
) -> Console:
    """
    The terminal the view asks about its width, its height, and what it can
    draw; the view is drawn on another console, below.
    """

    return Console(
        force_terminal=False,
        file=_Ascii() if ascii_form else StringIO(),
        width=columns,
        height=rows,
        highlight=False,
    )


def _progress(
    *,
    columns: int = 80,
    rows: int = 200,
    ascii_form: bool = False,
) -> tuple[OperatorProgress, FakeMonotonicClock]:
    clock = FakeMonotonicClock()

    return (
        OperatorProgress(
            console=_asked_console(
                columns=columns,
                rows=rows,
                ascii_form=ascii_form,
            ),
            clock=clock,
        ),
        clock,
    )


def _heard(
    progress: OperatorProgress,
    clock: FakeMonotonicClock,
    entry: object,
    /,
) -> None:
    """
    Give the view what the recording says it heard, at the time it heard it.
    """

    match entry:
        case Watched():
            clock.now = entry.at

            progress.watched(
                activity=entry.activity,
            )

        case StageStarted():
            clock.now = entry.at

            progress.stage_started(
                stage=entry.stage,
                steps=entry.steps,
            )

        case LookedUp():
            clock.now = entry.at

            progress.looked_up(
                lookup=entry.lookup,
            )

        case DictionaryWaiting():
            clock.now = entry.at

            progress.dictionary_waiting(
                held=entry.held,
                ending=entry.ending,
                retry_in_seconds=entry.retry_in_seconds,
                patience_left_seconds=entry.patience_left_seconds,
            )


def _reads(
    script: Script,
    /,
    *,
    columns: int = 80,
    rows: int = 200,
    ascii_form: bool = False,
):
    """
    The view after each read of the recording, with the second it was read at;
    one view, advanced as the recording goes.
    """

    (
        progress,
        clock,
    ) = _progress(
        columns=columns,
        rows=rows,
        ascii_form=ascii_form,
    )

    started = False

    for entry in _recorded(
        script,
    ).timeline:
        _heard(
            progress,
            clock,
            entry,
        )

        started = started or isinstance(
            entry,
            StageStarted,
        )

        if started and isinstance(
            entry,
            Watched,
        ):
            yield (
                entry.at,
                progress,
            )


def _view_at(
    script: Script,
    second: float,
    /,
    *,
    columns: int = 80,
    rows: int = 200,
    ascii_form: bool = False,
) -> OperatorProgress:
    """
    The view as the last read at or before `second` leaves it.

    What the recording heard at the instant of that read, after it, is not
    replayed, so a report that comes just after a read is not in the frame.
    """

    reads = tuple(
        _reads(
            script,
            columns=columns,
            rows=rows,
            ascii_form=ascii_form,
        ),
    )

    last = max(
        index
        for (
            index,
            (
                at,
                _,
            ),
        ) in enumerate(
            iterable=reads,
        )
        if at <= second + _SECOND
    )

    (
        progress,
        clock,
    ) = _progress(
        columns=columns,
        rows=rows,
        ascii_form=ascii_form,
    )

    for entry in _recorded(
        script,
    ).timeline:
        _heard(
            progress,
            clock,
            entry,
        )

        if (
            isinstance(
                entry,
                Watched,
            )
            and entry.at >= reads[last][0] - _SECOND
        ):
            break

    return progress


def _plain(
    progress: OperatorProgress,
    /,
    *,
    columns: int = 80,
    ascii_form: bool = False,
) -> tuple[str, ...]:
    """
    The rows of the panel as text a person reads, without a color or an escape
    sequence.
    """

    stream = _Ascii() if ascii_form else StringIO()

    Console(
        color_system=None,
        force_terminal=False,
        file=stream,
        width=columns,
        height=200,
        highlight=False,
    ).print(
        progress.panel(),
    )

    return tuple(line.rstrip() for line in stream.getvalue().splitlines())


def _sent(
    progress: OperatorProgress,
    /,
    *,
    columns: int = 80,
    color: bool,
    ascii_form: bool = False,
) -> str:
    """
    What a terminal receives for the panel: its text and the escape sequences
    that style it.
    """

    stream = _Ascii() if ascii_form else StringIO()

    Console(
        color_system="standard",
        force_terminal=True,
        file=stream,
        width=columns,
        height=200,
        no_color=not color,
        markup=False,
        emoji=False,
        highlight=False,
        legacy_windows=False,
    ).print(
        progress.panel(),
    )

    return stream.getvalue()


def _apply(
    state: dict[str, object],
    parameters: str,
    /,
) -> None:
    codes = (
        tuple(
            int(
                code,
            )
            for code in parameters.split(
                sep=";",
            )
        )
        if parameters
        else (0,)
    )

    index = 0

    while index < len(
        codes,
    ):
        code = codes[index]

        if code == 0:
            state.update(
                bold=False,
                dim=False,
                reverse=False,
                foreground=None,
                background=None,
            )

        elif code == 1:
            state["bold"] = True

        elif code == 2:
            state["dim"] = True

        elif code == 7:
            state["reverse"] = True

        elif 30 <= code <= 37:
            state["foreground"] = _COLORS[code - 30]

        elif 90 <= code <= 97:
            state["foreground"] = "bright_" + _COLORS[code - 90]

        elif code == 39:
            state["foreground"] = None

        elif 40 <= code <= 47:
            state["background"] = _COLORS[code - 40]

        elif code == 49:
            state["background"] = None

        else:
            raise ValueError(
                f"a code this test does not read: {code}",
            )

        index += 1


def _cells(
    output: str,
    /,
) -> list[list[_Cell]]:
    """
    The rows of cells a terminal shows for what it received, each with how it
    is drawn.
    """

    rows: list[list[_Cell]] = []

    row: list[_Cell] = []

    state: dict[str, object] = {
        "bold": False,
        "dim": False,
        "reverse": False,
        "foreground": None,
        "background": None,
    }

    index = 0

    while index < len(
        output,
    ):
        matched = _ESCAPE.match(
            string=output,
            pos=index,
        )

        if matched is not None:
            _apply(
                state,
                matched.group(
                    1,
                ),
            )

            index = matched.end()

            continue

        if output[index] == "\n":
            rows.append(
                row,
            )

            row = []

        else:
            row.append(
                _Cell(
                    character=output[index],
                    bold=bool(
                        state["bold"],
                    ),
                    dim=bool(
                        state["dim"],
                    ),
                    reverse=bool(
                        state["reverse"],
                    ),
                    foreground=(
                        state["foreground"]
                        if isinstance(
                            state["foreground"],
                            str,
                        )
                        else None
                    ),
                    background=(
                        state["background"]
                        if isinstance(
                            state["background"],
                            str,
                        )
                        else None
                    ),
                ),
            )

        index += 1

    return rows


def _canvas_rows(
    progress: OperatorProgress,
    /,
) -> tuple[Text, ...]:
    """
    The rows of the loop before Rich draws them, which Rich would crop
    silently if one were longer than its panel.
    """

    panel = progress.panel()

    assert isinstance(
        panel,
        Panel,
    )

    rows = panel.renderable

    assert isinstance(
        rows,
        Group,
    )

    return tuple(
        row
        for row in rows.renderables
        if isinstance(
            row,
            Text,
        )
    )


def _frame_height(
    progress: OperatorProgress,
    /,
    *,
    columns: int = 80,
) -> int:
    """
    How many rows the whole frame takes in the terminal the view draws on: the
    learner's line and the panel under it, as the display draws them.
    """

    console = Console(
        force_terminal=False,
        file=StringIO(),
        width=columns,
        height=200,
    )

    return len(
        console.render_lines(
            renderable=progress._render(),
            options=console.options,
            pad=False,
        ),
    )


def _badge(
    progress: OperatorProgress,
    /,
) -> str:
    matched = search(
        pattern=r"dictionary  (.+?) +─",
        string=_plain(
            progress,
        )[0],
    )

    assert matched is not None

    return matched.group(
        1,
    )


def _last_activity(
    recording: Recording,
    /,
) -> DictionaryActivity:
    return tuple(
        entry
        for entry in recording.timeline
        if isinstance(
            entry,
            Watched,
        )
    )[-1].activity


_CLOSED: Final = ProviderActivity(
    circuit=BreakerState.CLOSED,
    retry_in_seconds=None,
    cooldown_seconds=None,
    consecutive_failures=0,
    failure_threshold=5,
    tokens=4.0,
    burst=4,
    requests_per_second=2.0,
    waiting_for_budget=0,
    timeout_seconds=30.0,
    lookups=(),
    retries=MappingProxyType(
        mapping={},
    ),
    failed_trials=0,
    opened_by=None,
    recent=(),
    last_answer_seconds=None,
)

_NOTHING: Final = LookupCounts(
    cached=0,
    found=0,
    not_found=0,
    unavailable=MappingProxyType(
        mapping={},
    ),
)


def _asking(
    lemma: str,
    seconds: float,
    /,
    *,
    trial: bool = False,
) -> LookupActivity:
    return LookupActivity(
        lemma=lemma,
        waiting_for=WaitCause.PROVIDER,
        attempt=1,
        seconds=seconds,
        trial=trial,
    )


def _drawn_with(
    provider: ProviderActivity,
    /,
    *,
    steps: int = 100,
) -> OperatorProgress:
    """
    A view that has read `provider` once, in a stage of `steps` words.
    """

    (
        progress,
        _,
    ) = _progress()

    progress.stage_started(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=steps,
    )

    progress.watched(
        activity=DictionaryActivity(
            counts=_NOTHING,
            provider=provider,
        ),
    )

    return progress


def _stored(
    *,
    steps: int,
    done: int,
    unavailable: int = 0,
    waiting: int | None = 0,
    next_in: float | None = None,
) -> ProcessingProgress:
    """
    What a worker last stored, as the command that waits for the analysis
    hears it.
    """

    return ProcessingProgress(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=steps,
        done=done,
        unavailable=unavailable,
        dictionary_paused=False,
        reported_at=datetime(
            year=1970,
            month=1,
            day=1,
            tzinfo=UTC,
        ),
        dictionary_waiting_words=waiting,
        dictionary_next_in_seconds=next_in,
    )


# NOTE:
# A trial in flight with one lookup waiting for its answer, one pausing before its second attempt, and one waiting for
# a token.
_DURING: Final = DictionaryActivity(
    counts=LookupCounts(
        cached=32,
        found=35,
        not_found=3,
        unavailable=MappingProxyType(
            mapping={
                "provider_error": 1,
                "circuit_open": 2,
            },
        ),
    ),
    provider=ProviderActivity(
        circuit=BreakerState.HALF_OPEN,
        retry_in_seconds=None,
        cooldown_seconds=None,
        consecutive_failures=0,
        failure_threshold=5,
        tokens=1.4,
        burst=4,
        requests_per_second=2.0,
        waiting_for_budget=3,
        timeout_seconds=30.0,
        lookups=(
            LookupActivity(
                lemma="photograph",
                waiting_for=WaitCause.PROVIDER,
                attempt=1,
                seconds=18.2,
                trial=True,
            ),
            LookupActivity(
                lemma="mask",
                waiting_for=WaitCause.TRIAL,
                attempt=1,
                seconds=18.1,
            ),
            LookupActivity(
                lemma="groom",
                waiting_for=WaitCause.RETRY,
                attempt=2,
                seconds=0.9,
                delay_seconds=1.7,
                reason=FailureReason.PROVIDER_ERROR,
            ),
            LookupActivity(
                lemma="rocket",
                waiting_for=WaitCause.BUDGET,
                attempt=1,
                seconds=0.4,
            ),
        ),
        retries=MappingProxyType(
            mapping={
                FailureReason.TIMED_OUT: 3,
                FailureReason.PROVIDER_ERROR: 1,
            },
        ),
    ),
)


def _terminal(
    stream: StringIO,
    /,
) -> Console:
    return Console(
        force_terminal=True,
        force_interactive=True,
        file=stream,
        width=110,
    )


def _drawn(
    stream: StringIO,
    /,
) -> str:
    """
    What `stream` shows, without the sequences that style it and move the
    cursor.
    """

    return sub(
        pattern=r"\x1b\[[0-9;?]*[A-Za-z]",
        repl="",
        string=stream.getvalue(),
    )


@final
class TestOperatorProgress:
    def test_a_stream_that_is_not_a_terminal_receives_nothing(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=Console(
                file=stream,
            ),
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=156,
            )

            progress.watched(
                activity=_DURING,
            )

        assert not progress.shown

        assert stream.getvalue() == ""

    def test_the_line_says_what_the_words_the_stage_holds_wait_for(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=160,
            ),
        ) as progress:
            progress.stage_started(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=40,
            )

            progress.dictionary_waiting(
                held=(
                    held_words(
                        reason=FailureReason.CIRCUIT_OPEN,
                        turned_away=3,
                        turned_away_wake_in_seconds=9.0,
                    ),
                ),
                ending=False,
                retry_in_seconds=9.0,
                patience_left_seconds=171.0,
            )

        drawn = _drawn(
            stream,
        )

        assert "the dictionary is not answering; next check in 9 s" in drawn

        assert "3 waiting, WordNet's meaning within 2 min 51 s" in drawn

    def test_a_dictionary_it_cannot_read_is_named_with_the_reason(
        self,
    ) -> None:
        stream = StringIO()

        with OperatorProgress(
            console=_terminal(
                stream,
            ),
            refresh_per_second=1,
        ) as progress:
            progress.unwatched(
                reason="the enrichment service does not answer at http://enrichment.invalid",
            )

        drawn = _drawn(
            stream,
        )

        for expected in (
            "─ dictionary ─",
            "the enrichment service does not answer at http://enrichment.invalid",
        ):
            assert expected in drawn, expected

    def test_before_the_first_read_the_panel_says_it_is_reading(
        self,
    ) -> None:
        (
            progress,
            _,
        ) = _progress()

        assert any(
            "reading what the dictionary is doing" in row
            for row in _plain(
                progress,
            )
        )

        progress.watched(
            activity=DictionaryActivity(
                counts=_NOTHING,
                provider=_CLOSED,
            ),
        )

        assert not any(
            "reading what the dictionary is doing" in row
            for row in _plain(
                progress,
            )
        )

    def test_the_recovery_jitter_is_the_clients(
        self,
    ) -> None:
        assert RECOVERY_JITTER == _RECOVERY_JITTER


@final
class TestTheReferenceFrames:
    @mark.parametrize(
        argnames=(
            "name",
            "script",
            "second",
            "columns",
            "ascii_form",
        ),
        argvalues=_FRAMES,
    )
    def test_a_frame_is_its_reference_frame(
        self,
        *,
        name: str,
        script: Script,
        second: float,
        columns: int,
        ascii_form: bool,
    ) -> None:
        progress = _view_at(
            script,
            second,
            columns=columns,
            ascii_form=ascii_form,
        )

        expected = (_FIXTURES / f"{name}.txt").read_text(
            encoding="utf-8",
        )

        assert (
            "\n".join(
                _plain(
                    progress,
                    columns=columns,
                    ascii_form=ascii_form,
                ),
            )
            + "\n"
            == expected
        )

    def test_every_reference_frame_has_a_file(
        self,
    ) -> None:
        assert sorted(
            path.stem
            for path in _FIXTURES.glob(
                pattern="*.txt",
            )
        ) == sorted(
            name
            for (
                name,
                *_,
            ) in _FRAMES
        )


@final
class TestEveryRowIsWhole:
    @mark.parametrize(
        argnames="columns",
        argvalues=(
            80,
            100,
            120,
        ),
    )
    def test_each_row_is_the_panels_width_in_each_form_for_every_second_of_two_scenarios(
        self,
        *,
        columns: int,
    ) -> None:
        width = max(
            min(
                columns - 1,
                100,
            ),
            40,
        )

        checked = 0

        for script in (
            EVERY_MECHANISM_IN_TURN,
            TWENTY_FOUR_LOOKUPS_AT_ONCE,
        ):
            for form in (
                "color",
                "no color",
                "ascii",
            ):
                ascii_form = form == "ascii"

                for (
                    second,
                    progress,
                ) in _reads(
                    script,
                    columns=columns,
                    ascii_form=ascii_form,
                ):
                    if (
                        abs(
                            second
                            - round(
                                number=second,
                            ),
                        )
                        > _SECOND
                    ):
                        continue

                    # NOTE:
                    # The rows of the loop are measured before Rich draws them, because inside a panel Rich crops a
                    # longer row silently and the rendered panel alone would not show it.
                    for canvas in _canvas_rows(
                        progress,
                    ):
                        assert canvas.cell_len == width - 4

                    sent = _sent(
                        progress,
                        columns=columns,
                        color=form == "color",
                        ascii_form=ascii_form,
                    )

                    for row in sub(
                        pattern=_ANY_ESCAPE,
                        repl="",
                        string=sent,
                    ).splitlines():
                        assert (
                            len(
                                row,
                            )
                            == width
                        ), (
                            script,
                            second,
                            form,
                            row,
                        )

                        assert (
                            len(
                                row,
                            )
                            < columns
                        )

                    checked += 1

        assert checked > 600


@final
class TestTheTitle:
    def test_a_request_that_hangs_makes_the_title_no_answer_from_the_fifth_second_until_it_times_out(
        self,
    ) -> None:
        recording = _recorded(
            NEVER_ANSWERING,
        )

        watched = tuple(
            entry
            for entry in recording.timeline
            if isinstance(
                entry,
                Watched,
            )
        )

        asked = tuple(
            max(
                (
                    lookup.seconds
                    for lookup in entry.activity.provider.lookups
                    if lookup.waiting_for is WaitCause.PROVIDER
                ),
                default=0.0,
            )
            for entry in watched
        )

        first = next(
            index
            for (
                index,
                seconds,
            ) in enumerate(
                iterable=asked,
            )
            if seconds >= 5.0
        )

        timed_out = next(
            index
            for (
                index,
                entry,
            ) in enumerate(
                iterable=watched,
            )
            if entry.activity.provider.recent
        )

        titles = {}

        for (
            second,
            progress,
        ) in _reads(
            NEVER_ANSWERING,
        ):
            titles[second] = _badge(
                progress,
            )

        for (
            index,
            entry,
        ) in enumerate(
            iterable=watched,
        ):
            if entry.at not in titles:
                continue

            if first <= index < timed_out:
                assert titles[entry.at] == "NO ANSWER", entry.at

            elif index < first:
                assert titles[entry.at] != "NO ANSWER", entry.at

    @mark.parametrize(
        argnames=(
            "earlier",
            "later",
        ),
        argvalues=(
            (
                State.ON_TRIAL,
                State.RATE_LIMITED,
            ),
            (
                State.RATE_LIMITED,
                State.IDLE,
            ),
            (
                State.NO_ANSWER,
                State.STRUGGLING,
            ),
            (
                State.STRUGGLING,
                State.SLOW,
            ),
            (
                State.SLOW,
                State.ANSWERING,
            ),
        ),
    )
    def test_a_read_that_holds_two_conditions_takes_the_earlier_state(
        self,
        *,
        earlier: State,
        later: State,
    ) -> None:
        # NOTE:
        # Each read holds the condition of both states, built here from the guide's table of the states: a half-open
        # breaker and a pause; a pause and no lookup; a request that has hung and a failure in a row; a failure in a
        # row and two slow requests; and two slow requests, which `answering` holds as the otherwise.
        answered = (
            RecentRequest(
                seconds=1.0,
                outcome=RequestOutcome.ANSWERED,
            ),
        )

        reads = MappingProxyType(
            mapping={
                (
                    State.ON_TRIAL,
                    State.RATE_LIMITED,
                ): replace(
                    _CLOSED,
                    circuit=BreakerState.HALF_OPEN,
                    cooldown_seconds=4.0,
                ),
                (
                    State.RATE_LIMITED,
                    State.IDLE,
                ): replace(
                    _CLOSED,
                    cooldown_seconds=4.0,
                ),
                (
                    State.NO_ANSWER,
                    State.STRUGGLING,
                ): replace(
                    _CLOSED,
                    consecutive_failures=2,
                    lookups=(
                        _asking(
                            "foo",
                            7.0,
                        ),
                    ),
                ),
                (
                    State.STRUGGLING,
                    State.SLOW,
                ): replace(
                    _CLOSED,
                    consecutive_failures=1,
                    lookups=(
                        _asking(
                            "foo",
                            6.0,
                        ),
                        _asking(
                            "bar",
                            6.0,
                        ),
                    ),
                    recent=answered,
                ),
                (
                    State.SLOW,
                    State.ANSWERING,
                ): replace(
                    _CLOSED,
                    lookups=(
                        _asking(
                            "foo",
                            6.0,
                        ),
                        _asking(
                            "bar",
                            6.0,
                        ),
                    ),
                    recent=answered,
                ),
            },
        )

        assert (
            _badge(
                _drawn_with(
                    reads[(earlier, later)],
                ),
            )
            == WORDS[earlier].upper()
        )

    def test_the_states_that_cannot_hold_together_are_each_drawn_alone(
        self,
    ) -> None:
        # NOTE:
        # The breaker is in one state, and a word at the client is not idle, so two pairs of the table cannot both
        # hold; each of the four conditions is drawn alone instead.
        answered = (
            RecentRequest(
                seconds=1.0,
                outcome=RequestOutcome.ANSWERED,
            ),
        )

        for (
            provider,
            state,
        ) in (
            (
                replace(
                    _CLOSED,
                    circuit=BreakerState.OPEN,
                    retry_in_seconds=4.0,
                ),
                State.FAILING,
            ),
            (
                replace(
                    _CLOSED,
                    circuit=BreakerState.HALF_OPEN,
                ),
                State.ON_TRIAL,
            ),
            (
                _CLOSED,
                State.IDLE,
            ),
            (
                replace(
                    _CLOSED,
                    lookups=(
                        _asking(
                            "foo",
                            7.0,
                        ),
                    ),
                ),
                State.NO_ANSWER,
            ),
            (
                replace(
                    _CLOSED,
                    lookups=(
                        _asking(
                            "foo",
                            1.0,
                        ),
                    ),
                    recent=answered,
                ),
                State.ANSWERING,
            ),
        ):
            assert (
                _badge(
                    _drawn_with(
                        provider,
                    ),
                )
                == WORDS[state].upper()
            )

    def test_an_open_breaker_is_failing_whatever_else_holds(
        self,
    ) -> None:
        provider = replace(
            _CLOSED,
            circuit=BreakerState.OPEN,
            retry_in_seconds=4.0,
            cooldown_seconds=4.0,
            lookups=(
                _asking(
                    "foo",
                    9.0,
                ),
            ),
            consecutive_failures=0,
        )

        assert (
            _badge(
                _drawn_with(
                    provider,
                ),
            )
            == "FAILING"
        )

    def test_the_eight_states_are_the_titles_table_in_order(
        self,
    ) -> None:
        assert tuple(WORDS[state] for state in State) == (
            "failing",
            "on trial",
            "rate-limited",
            "idle",
            "no answer",
            "struggling",
            "slow",
            "answering",
        )


@final
class TestTheLane:
    def test_a_requests_mark_stays_in_its_cell_through_every_read_of_its_second(
        self,
    ) -> None:
        severity = MappingProxyType(
            mapping={
                "✓": 1,
                "!": 3,
                "✗": 4,
            },
        )

        by_second: dict[int, str] = {}

        reads = 0

        for (
            second,
            progress,
        ) in _reads(
            EVERY_MECHANISM_IN_TURN,
        ):
            (lane,) = (
                row.plain
                for row in _canvas_rows(
                    progress,
                )
                if row.plain.startswith(
                    "last ",
                )
            )

            span = int(
                lane.split()[1],
            )

            cells = lane[-3 - span : -3]

            for (
                age,
                character,
            ) in enumerate(
                iterable=reversed(
                    cells,
                ),
            ):
                absolute = (
                    int(
                        second + _SECOND,
                    )
                    - age
                )

                if character in (
                    "✓",
                    "!",
                    "✗",
                    "W",
                ):
                    held = by_second.get(
                        absolute,
                    )

                    # NOTE:
                    # A second's mark only grows more serious as more requests come back in it, except that the words
                    # let go can take the cell from them, which the guide's account of the last minute gives to `W`.
                    if held is not None and held != "W" and character != "W":
                        assert severity[character] >= severity[held], (
                            second,
                            absolute,
                            held,
                            character,
                        )

                    by_second[absolute] = character

                else:
                    assert absolute not in by_second, (
                        second,
                        absolute,
                        by_second[absolute],
                        character,
                    )

            reads += 1

        assert reads > 400

        assert (
            len(
                by_second,
            )
            > 50
        )


@final
class TestNeverFive:
    def test_no_read_of_any_script_draws_five_marks_over_the_switch(
        self,
    ) -> None:
        drawn = Counter()

        for script in (
            *_FIVE,
            *_EIGHT,
        ):
            for (
                second,
                progress,
            ) in _reads(
                script,
            ):
                rows = _canvas_rows(
                    progress,
                )

                # NOTE:
                # The row between the names and the wire holds the failures over the switch, and nothing else there is
                # drawn with the mark of a failed request.
                marks = rows[1].plain.count(
                    "✗",
                )

                assert marks < 5, (
                    second,
                    rows[1].plain,
                )

                drawn[marks] += 1

        assert drawn[4] > 0

        assert drawn[1] > 0


@final
class TestTheThreeForms:
    @mark.parametrize(
        argnames=(
            "name",
            "script",
            "second",
            "columns",
            "ascii_form",
        ),
        argvalues=_FRAMES,
    )
    def test_without_color_the_text_is_the_same_and_no_color_is_sent(
        self,
        *,
        name: str,
        script: Script,
        second: float,
        columns: int,
        ascii_form: bool,
    ) -> None:
        progress = _view_at(
            script,
            second,
            columns=columns,
            ascii_form=ascii_form,
        )

        colored = _cells(
            _sent(
                progress,
                columns=columns,
                color=True,
                ascii_form=ascii_form,
            ),
        )

        plain = _cells(
            _sent(
                progress,
                columns=columns,
                color=False,
                ascii_form=ascii_form,
            ),
        )

        assert tuple(tuple(cell.character for cell in row) for row in colored) == tuple(
            tuple(cell.character for cell in row) for row in plain
        )

        assert not any(cell.foreground or cell.background for row in plain for cell in row)

        assert any(cell.foreground for row in colored for cell in row) or name in ("narrow-terminal",)

    def test_in_ascii_every_character_of_the_panel_is_printable_ascii(
        self,
    ) -> None:
        for script in (
            EVERY_MECHANISM_IN_TURN,
            TWENTY_FOUR_LOOKUPS_AT_ONCE,
        ):
            for (
                second,
                progress,
            ) in _reads(
                script,
                ascii_form=True,
            ):
                if (
                    abs(
                        second
                        - round(
                            number=second,
                        ),
                    )
                    > _SECOND
                ):
                    continue

                for row in _plain(
                    progress,
                    ascii_form=True,
                ):
                    assert all(
                        32
                        <= ord(
                            character,
                        )
                        < 127
                        for character in row
                    ), (
                        second,
                        row,
                    )

    @mark.parametrize(
        argnames=(
            "name",
            "script",
            "second",
            "columns",
            "ascii_form",
        ),
        argvalues=_FRAMES,
    )
    def test_the_badge_is_reverse_and_not_bold_and_the_name_is_bold_without_hue_in_every_form(
        self,
        *,
        name: str,
        script: Script,
        second: float,
        columns: int,
        ascii_form: bool,
    ) -> None:
        progress = _view_at(
            script,
            second,
            columns=columns,
            ascii_form=ascii_form,
        )

        for color in (
            True,
            False,
        ):
            title = _cells(
                _sent(
                    progress,
                    columns=columns,
                    color=color,
                    ascii_form=ascii_form,
                ),
            )[0]

            text = "".join(cell.character for cell in title)

            word = _badge(
                progress,
            )

            start = text.index(
                f" {word} ",
            )

            badge = title[
                start : start
                + len(
                    word,
                )
                + 2
            ]

            assert all(cell.reverse and not cell.bold for cell in badge), (
                name,
                color,
            )

            where = text.index(
                "dictionary",
            )

            assert all(
                cell.bold and cell.foreground is None
                for cell in title[
                    where : where
                    + len(
                        "dictionary",
                    )
                ]
            ), (
                name,
                color,
            )

    @mark.parametrize(
        argnames="columns",
        argvalues=(
            80,
            100,
        ),
    )
    def test_in_color_no_bold_cell_has_a_hue_none_has_a_background_and_every_hue_is_one_of_the_four(
        self,
        *,
        columns: int,
    ) -> None:
        hues = Counter()

        for script in _FIVE:
            for (
                second,
                progress,
            ) in _reads(
                script,
                columns=columns,
            ):
                if (
                    abs(
                        second
                        - round(
                            number=second,
                        ),
                    )
                    > _SECOND * 4
                    or int(
                        second,
                    )
                    % 2
                ):
                    continue

                for row in _cells(
                    _sent(
                        progress,
                        columns=columns,
                        color=True,
                    ),
                ):
                    for cell in row:
                        assert cell.background is None, (
                            second,
                            cell.character,
                        )

                        assert not (cell.bold and cell.foreground is not None and cell.character != " "), (
                            second,
                            cell.character,
                            cell.foreground,
                        )

                        if cell.foreground is not None:
                            assert cell.foreground in _HUES, (
                                second,
                                cell.foreground,
                            )

                            hues[cell.foreground] += 1

        assert (
            frozenset(
                hues,
            )
            == _HUES
        )


@final
class TestShortAndNarrow:
    @mark.parametrize(
        argnames="rows",
        argvalues=(
            24,
            16,
        ),
    )
    def test_every_read_of_the_tour_draws_a_frame_no_taller_than_the_terminal(
        self,
        *,
        rows: int,
    ) -> None:
        tallest = 0

        for (
            second,
            progress,
        ) in _reads(
            EVERY_MECHANISM_IN_TURN,
            rows=rows,
        ):
            height = _frame_height(
                progress,
            )

            assert height <= rows, (
                second,
                height,
            )

            tallest = max(
                tallest,
                height,
            )

        assert tallest > 10

    def test_a_panel_too_narrow_for_the_loop_draws_its_narrow_form(
        self,
    ) -> None:
        progress = _view_at(
            EVERY_MECHANISM_IN_TURN,
            31.0,
            columns=60,
        )

        rows = _plain(
            progress,
            columns=60,
        )

        assert any("the loop is drawn from 73 columns" in row for row in rows)

        assert not any("last " in row for row in rows)

    def test_a_terminal_of_73_columns_draws_the_loop(
        self,
    ) -> None:
        progress = _view_at(
            EVERY_MECHANISM_IN_TURN,
            31.0,
            columns=73,
        )

        rows = _plain(
            progress,
            columns=73,
        )

        assert any(
            row.startswith(
                "│ last ",
            )
            for row in rows
        )

        assert not any("the loop is drawn from" in row for row in rows)


@final
class TestAnOlderServicesPayload:
    def test_without_the_optional_fields_there_are_no_marks_no_trial_number_and_no_failure(
        self,
    ) -> None:
        older = replace(
            _CLOSED,
            consecutive_failures=None,
            failure_threshold=None,
            failed_trials=None,
            opened_by=None,
            recent=(),
            last_answer_seconds=None,
        )

        progress = _drawn_with(
            replace(
                older,
                lookups=(
                    _asking(
                        "foo",
                        1.0,
                    ),
                ),
            ),
        )

        rows = _canvas_rows(
            progress,
        )

        assert "✗" not in rows[1].plain

        assert "·" not in rows[1].plain.replace(
            "╷",
            "",
        )

        (lane,) = (
            row.plain
            for row in rows
            if row.plain.startswith(
                "last ",
            )
        )

        assert not any(character in lane for character in "✓!✗W")

        opened = _drawn_with(
            replace(
                older,
                circuit=BreakerState.OPEN,
                retry_in_seconds=7.0,
            ),
        )

        text = "\n".join(
            _plain(
                opened,
            ),
        )

        assert "the trial in 7 s" in text

        assert "trial 1" not in text

        assert "breaker open after failed requests in a row" in text

        half = _drawn_with(
            replace(
                older,
                circuit=BreakerState.HALF_OPEN,
            ),
        )

        assert "the trial is next" in "\n".join(
            _plain(
                half,
            ),
        )


@final
class TestDrawingReadsAndNeverWrites:
    def test_drawing_the_same_state_twice_gives_the_same_panel_and_changes_nothing_the_view_keeps(
        self,
    ) -> None:
        recording = _recorded(
            EVERY_MECHANISM_IN_TURN,
        )

        (
            progress,
            clock,
        ) = _progress()

        drawn = 0

        for entry in recording.timeline:
            _heard(
                progress,
                clock,
                entry,
            )

            if (
                not isinstance(
                    entry,
                    Watched,
                )
                or int(
                    entry.at * 4,
                )
                % 6
            ):
                continue

            kept = progress._keeper.seen

            first = _sent(
                progress,
                color=True,
            )

            # NOTE:
            # The display draws as often as ten times a second from another thread, between the reads of the activity
            # that come four times a second, so a draw may come at any moment after a read.
            for step in (
                0.05,
                0.1,
                0.2,
            ):
                clock.now = entry.at + step

                _sent(
                    progress,
                    color=True,
                )

                assert progress._keeper.seen is kept

            clock.now = entry.at

            assert (
                _sent(
                    progress,
                    color=True,
                )
                == first
            )

            assert progress._keeper.seen is kept

            drawn += 1

        assert drawn > 50


def _row_starting(
    progress: OperatorProgress,
    start: str,
    /,
) -> str:
    """
    The one row of the panel that starts with `start` between its borders.
    """

    (found,) = (
        row
        for row in _plain(
            progress,
        )
        if row.startswith(
            f"│ {start}",
        )
    )

    return found


def _watched(
    progress: OperatorProgress,
    provider: ProviderActivity,
    counts: LookupCounts = _NOTHING,
    /,
) -> None:
    progress.watched(
        activity=DictionaryActivity(
            counts=counts,
            provider=provider,
        ),
    )


@final
class TestTheWordsToAsk:
    def test_the_stage_counts_its_words_less_those_done_held_and_at_the_client_and_never_below_nought(
        self,
    ) -> None:
        progress = _drawn_with(
            replace(
                _CLOSED,
                lookups=tuple(
                    _asking(
                        f"foo{number}",
                        1.0,
                    )
                    for number in range(
                        8,
                    )
                ),
            ),
            steps=30,
        )

        for _ in range(
            4,
        ):
            progress.looked_up(
                lookup=_unanswered(),
            )

        assert _plain(
            progress,
        )[1].startswith(
            "│ to ask 18 ",
        )

        progress.dictionary_waiting(
            held=(
                held_words(
                    reason=FailureReason.RATE_LIMITED,
                    turned_away=7,
                    turned_away_wake_in_seconds=9.0,
                    others_sleeping=3,
                ),
            ),
            ending=False,
            retry_in_seconds=9.0,
            patience_left_seconds=170.0,
        )

        assert _plain(
            progress,
        )[1].startswith(
            "│ to ask 8 ",
        )

        assert _row_starting(
            progress,
            "to ask again",
        ).startswith(
            "│ to ask again 10 ",
        )

        progress.dictionary_waiting(
            held=(
                held_words(
                    reason=FailureReason.RATE_LIMITED,
                    turned_away=70,
                    turned_away_wake_in_seconds=9.0,
                ),
            ),
            ending=False,
            retry_in_seconds=9.0,
            patience_left_seconds=170.0,
        )

        assert _plain(
            progress,
        )[1].startswith(
            "│ to ask 0 ",
        )

    def test_a_workers_analysis_counts_its_words_from_its_own_progress_whatever_the_service_is_doing(
        self,
    ) -> None:
        for lookups in (
            (),
            tuple(
                _asking(
                    f"foo{number}",
                    1.0,
                )
                for number in range(
                    16,
                )
            ),
        ):
            (
                progress,
                _,
            ) = _progress()

            progress.processing(
                progress=_stored(
                    steps=198,
                    done=150,
                    waiting=3,
                    next_in=8.0,
                ),
            )

            _watched(
                progress,
                replace(
                    _CLOSED,
                    lookups=lookups,
                ),
            )

            assert _plain(
                progress,
            )[1].startswith(
                "│ to ask 45 ",
            )

            assert _row_starting(
                progress,
                "to ask again",
            ).startswith(
                "│ to ask again 3 ",
            )

            assert (
                _row_starting(
                    progress,
                    "to ask again",
                )
                .rstrip(
                    " │",
                )
                .endswith(
                    "next in 8 s",
                )
            )

    def test_a_workers_analysis_shows_the_tally_as_the_count_alone(
        self,
    ) -> None:
        (
            progress,
            _,
        ) = _progress()

        progress.processing(
            progress=_stored(
                steps=198,
                done=150,
            ),
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        1.0,
                    ),
                ),
            ),
            LookupCounts(
                cached=900,
                found=700,
                not_found=1,
                unavailable=MappingProxyType(
                    mapping={},
                ),
            ),
        )

        assert (
            _row_starting(
                progress,
                "resolved",
            ).rstrip(
                " │",
            )
            == "│ resolved 150 of 198"
        )

    def test_a_workers_analysis_draws_sixteen_lookups_in_at_most_eight_rows_and_one_that_counts_the_rest(
        self,
    ) -> None:
        (
            progress,
            _,
        ) = _progress()

        progress.processing(
            progress=_stored(
                steps=198,
                done=10,
            ),
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=tuple(
                    _asking(
                        f"foo{number}",
                        1.0 + number,
                    )
                    for number in range(
                        16,
                    )
                ),
            ),
        )

        rows = _plain(
            progress,
        )

        assert (
            sum(
                1
                for row in rows
                if row.startswith(
                    "│ foo",
                )
            )
            == 8
        )

        assert any("and 8 more asking" in row for row in rows)

    def test_a_workers_analysis_folds_the_line_at_the_bucket_into_one_row(
        self,
    ) -> None:
        (
            progress,
            _,
        ) = _progress()

        progress.processing(
            progress=_stored(
                steps=198,
                done=10,
            ),
        )

        waiting = tuple(
            LookupActivity(
                lemma=f"bar{number}",
                waiting_for=WaitCause.BUDGET,
                attempt=1,
                seconds=10.0 - number,
            )
            for number in range(
                10,
            )
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    *(
                        _asking(
                            f"foo{number}",
                            1.0,
                        )
                        for number in range(
                            6,
                        )
                    ),
                    *waiting,
                ),
            ),
        )

        rows = _plain(
            progress,
        )

        assert (
            sum(
                1
                for row in rows
                if row.startswith(
                    "│ in line 10",
                )
            )
            == 1
        )

        assert (
            sum(
                1
                for row in rows
                if row.startswith(
                    "│ foo",
                )
            )
            == 6
        )

        assert not any(
            row.startswith(
                "│ bar",
            )
            for row in rows
        )

    def test_words_let_go_are_the_growth_of_the_workers_count_of_words_the_dictionary_left_unanswered(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.processing(
            progress=_stored(
                steps=198,
                done=10,
                unavailable=2,
            ),
        )

        _watched(
            progress,
            _CLOSED,
        )

        assert not any(
            "let go" in row
            for row in _plain(
                progress,
            )
        )

        clock.now = 1.0

        progress.processing(
            progress=_stored(
                steps=198,
                done=60,
                unavailable=5,
            ),
        )

        assert any(
            "── 3 let go: W WordNet's meaning stands in where it has one ──" in row
            for row in _plain(
                progress,
            )
        )

        clock.now = 5.0

        _watched(
            progress,
            _CLOSED,
        )

        assert not any(
            "let go:" in row
            for row in _plain(
                progress,
            )
        )


def _unanswered() -> DictionaryLookup:
    return DictionaryLookup(
        outcome=LookupOutcome.NOT_FOUND,
    )


_NEGATIVE: Final = compile_pattern(
    pattern=r"(?<![\w─-])-\d",
)


@final
class TestACountThatFallsAfterTheServiceRestarts:
    # NOTE:
    # A service that has run a while has counted many answers; one that restarts counts them again from nought, below
    # what the view first read, and the counts taken from that first read must never be drawn below nought.
    _AFTER: Final = LookupCounts(
        cached=0,
        found=0,
        not_found=0,
        unavailable=MappingProxyType(
            mapping={},
        ),
    )

    _LONG_RUNNING: Final = LookupCounts(
        cached=28,
        found=1297,
        not_found=61,
        unavailable=MappingProxyType(
            mapping={
                FailureReason.PROVIDER_ERROR: 40,
                FailureReason.TIMED_OUT: 12,
            },
        ),
    )

    def test_a_workers_analysis_draws_no_figure_below_nought_and_no_answered_row(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.processing(
            progress=_stored(
                steps=198,
                done=20,
            ),
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                retries=MappingProxyType(
                    mapping={
                        FailureReason.PROVIDER_ERROR: 30,
                    },
                ),
            ),
            self._LONG_RUNNING,
        )

        for step in range(
            1,
            5,
        ):
            clock.now = step * 0.25

            progress.processing(
                progress=_stored(
                    steps=198,
                    done=20 + step,
                ),
            )

            _watched(
                progress,
                _CLOSED,
                self._AFTER,
            )

            rows = _plain(
                progress,
            )

            assert not any(
                _NEGATIVE.search(
                    string=row,
                )
                for row in rows
            ), rows

            assert not any(
                row.startswith(
                    "│ answered",
                )
                for row in rows
            )

            assert (
                _row_starting(
                    progress,
                    "resolved",
                ).rstrip(
                    " │",
                )
                == f"│ resolved {20 + step} of 198"
            )

    def test_the_stage_here_floors_the_parts_of_the_tally_at_nought(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=198,
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                retries=MappingProxyType(
                    mapping={
                        FailureReason.PROVIDER_ERROR: 30,
                    },
                ),
            ),
            self._LONG_RUNNING,
        )

        clock.now = 0.25

        _watched(
            progress,
            _CLOSED,
            self._AFTER,
        )

        rows = _plain(
            progress,
        )

        assert not any(
            _NEGATIVE.search(
                string=row,
            )
            for row in rows
        ), rows

        assert _row_starting(
            progress,
            "resolved",
        ).rstrip(
            " │",
        ) == ("│ resolved 0 of 198:  0 cache  ✓ 0 found  0 not found  W 0 let go")


@final
class TestALineOfThreeRows:
    def test_a_frame_with_a_line_of_three_rows_is_no_taller_than_the_terminal(
        self,
    ) -> None:
        # NOTE:
        # Under a worker's report of an analysis that has had no answer for half a minute, the learner's line says why
        # in a column that wraps, and takes three rows at 80 columns where it takes two without.
        (
            progress,
            _,
        ) = _progress(
            rows=16,
        )

        progress.processing(
            progress=ProcessingProgress(
                stage=Stage.DICTIONARY_ENRICHMENT,
                steps=116,
                done=65,
                unavailable=2,
                dictionary_paused=False,
                reported_at=datetime.now(
                    tz=UTC,
                )
                - timedelta(
                    seconds=30,
                ),
            ),
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    *(
                        _asking(
                            f"foo{number}",
                            1.0 + number,
                        )
                        for number in range(
                            8,
                        )
                    ),
                    *(
                        LookupActivity(
                            lemma=f"bar{number}",
                            waiting_for=WaitCause.BUDGET,
                            attempt=1,
                            seconds=10.0 - number,
                        )
                        for number in range(
                            4,
                        )
                    ),
                ),
            ),
        )

        panel_rows = len(
            _plain(
                progress,
            ),
        )

        assert (
            _frame_height(
                progress,
            )
            - panel_rows
            == 3
        )

        assert (
            _frame_height(
                progress,
            )
            <= 16
        )

        assert any(
            "and " in row and " more asking" in row
            for row in _plain(
                progress,
            )
        )


@final
class TestTheSentences:
    def test_a_provider_that_answers_slowly_says_how_many_requests_have_asked_5_s_or_more(
        self,
    ) -> None:
        progress = _drawn_with(
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        8.0,
                    ),
                    _asking(
                        "bar",
                        6.0,
                    ),
                    _asking(
                        "baz",
                        5.5,
                    ),
                ),
                recent=(
                    RecentRequest(
                        seconds=1.0,
                        outcome=RequestOutcome.ANSWERED,
                    ),
                ),
            ),
        )

        assert any(
            "answering slowly: 3 requests have asked 5 s or more, the oldest 8 s" in row
            for row in _plain(
                progress,
            )
        )

    def test_a_word_that_backs_off_with_no_failure_in_a_row_says_what_it_waits_out(
        self,
    ) -> None:
        progress = _drawn_with(
            replace(
                _CLOSED,
                lookups=(
                    LookupActivity(
                        lemma="foo",
                        waiting_for=WaitCause.RETRY,
                        attempt=2,
                        seconds=0.1,
                        delay_seconds=0.5,
                        reason=FailureReason.PROVIDER_ERROR,
                    ),
                ),
            ),
        )

        assert any(
            "answering again; a word waits out a backoff after a server error" in row
            for row in _plain(
                progress,
            )
        )

    def test_a_half_open_breaker_before_a_word_asks_names_the_next_trial(
        self,
    ) -> None:
        progress = _drawn_with(
            replace(
                _CLOSED,
                circuit=BreakerState.HALF_OPEN,
            ),
        )

        assert any(
            "breaker half-open: the next word to ask is trial 1" in row
            for row in _plain(
                progress,
            )
        )

    def test_a_stage_with_nothing_at_the_client_says_idle_and_whether_every_word_has_its_answer(
        self,
    ) -> None:
        assert any(
            "idle: no word at the client and none held" in row
            for row in _plain(
                _drawn_with(
                    _CLOSED,
                ),
            )
        )

        assert any(
            "idle: every word has its answer" in row
            for row in _plain(
                _drawn_with(
                    _CLOSED,
                    steps=0,
                ),
            )
        )

    def test_a_pause_that_outlasts_the_breakers_wait_says_the_trial_comes_after_it(
        self,
    ) -> None:
        progress = _drawn_with(
            replace(
                _CLOSED,
                circuit=BreakerState.OPEN,
                retry_in_seconds=4.0,
                cooldown_seconds=9.0,
            ),
        )

        assert any(
            "trial 1 after the pause" in row
            for row in _plain(
                progress,
            )
        )

    def test_a_half_open_breaker_with_a_trial_out_says_when_the_trial_is_answered(
        self,
    ) -> None:
        keeper = Keeper()

        keeper.watched(
            activity=DictionaryActivity(
                counts=_NOTHING,
                provider=replace(
                    _CLOSED,
                    circuit=BreakerState.HALF_OPEN,
                    lookups=(
                        _asking(
                            "foo",
                            1.0,
                            trial=True,
                        ),
                    ),
                ),
            ),
            now=0.0,
        )

        assert keeper.seen is not None

        assert (
            next_clause(
                seen=keeper.seen,
                now=0.0,
                width=100,
            )
            == "when trial 1 is answered; if it fails, 7.5 to 10 s or longer"
        )

    def test_a_breaker_held_open_for_as_long_as_the_provider_asked_says_so_on_a_second_row(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=100,
        )

        _watched(
            progress,
            _CLOSED,
        )

        clock.now = 1.0

        _watched(
            progress,
            replace(
                _CLOSED,
                circuit=BreakerState.OPEN,
                retry_in_seconds=112.0,
                failed_trials=0,
                opened_by=FailureReason.PROVIDER_ERROR,
            ),
        )

        assert any(
            row.startswith(
                "│ next  trial 1 in 1 min 52 s: as long as the provider asked",
            )
            for row in _plain(
                progress,
            )
        )
