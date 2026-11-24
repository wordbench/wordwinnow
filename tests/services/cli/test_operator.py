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
from collections.abc import (
    Mapping,
)
from dataclasses import (
    dataclass,
    replace,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from io import (
    BytesIO,
    StringIO,
    TextIOWrapper,
)
from itertools import (
    pairwise,
)
from math import (
    inf,
)
from pathlib import (
    Path,
)
from re import (
    DOTALL,
    search,
    sub,
)
from re import (
    compile as compile_pattern,
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
    WordTruth,
    record,
)
from tests.fakes.dictionary_stage_scenarios import (
    EVERY_MECHANISM_IN_TURN,
    HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
    LONG_PAUSES_AND_REQUESTS_THAT_NEVER_COME_BACK,
    OUTAGE_AS_THE_OPERATING_GUIDE_DESCRIBES_IT,
    OUTAGE_LONGER_THAN_THE_ANALYSIS_WAITS,
    PROVIDER_THAT_ANSWERS_SLOWLY,
    TWENTY_FOUR_LOOKUPS_AT_ONCE,
)
from tests.fakes.ports import (
    EPOCH,
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
from wordwinnow.services.cli.operator_canvas import (
    Canvas,
)
from wordwinnow.services.cli.operator_look import (
    WORDS,
)
from wordwinnow.services.cli.operator_rows import (
    MARKS_AT_MOST,
    WORD_ROWS,
    Ending,
    Followed,
)
from wordwinnow.services.cli.operator_seen import (
    Keeper,
)
from wordwinnow.services.cli.operator_states import (
    RECOVERY_JITTER,
    State,
    duration,
    held_of,
    next_clause,
)
from wordwinnow.services.cli.terminal_text import (
    keep_cells_on,
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
# The frames of the reference set: its name, the script it is drawn from, the second of the recording, the width and
# the height of the terminal, and whether the terminal can draw only ASCII.
_FRAMES: Final = (
    (
        "healthy",
        HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
        26.0,
        80,
        200,
        False,
    ),
    (
        "rate-limited",
        HEALTHY_AGAINST_THE_PROVIDERS_OWN_LIMIT,
        12.0,
        80,
        200,
        False,
    ),
    (
        "struggling",
        EVERY_MECHANISM_IN_TURN,
        24.5,
        80,
        200,
        False,
    ),
    (
        "backing-off",
        EVERY_MECHANISM_IN_TURN,
        18.0,
        80,
        200,
        False,
    ),
    (
        "failing-just-opened",
        EVERY_MECHANISM_IN_TURN,
        26.5,
        80,
        200,
        False,
    ),
    (
        "failing",
        EVERY_MECHANISM_IN_TURN,
        31.0,
        80,
        200,
        False,
    ),
    (
        "on-trial",
        EVERY_MECHANISM_IN_TURN,
        35.5,
        80,
        200,
        False,
    ),
    (
        "recovering",
        EVERY_MECHANISM_IN_TURN,
        47.5,
        80,
        200,
        False,
    ),
    (
        "refused-word",
        EVERY_MECHANISM_IN_TURN,
        55.0,
        80,
        200,
        False,
    ),
    (
        "no-answer",
        EVERY_MECHANISM_IN_TURN,
        85.0,
        80,
        200,
        False,
    ),
    (
        "slow",
        PROVIDER_THAT_ANSWERS_SLOWLY,
        20.0,
        80,
        200,
        False,
    ),
    (
        "let-go",
        OUTAGE_LONGER_THAN_THE_ANALYSIS_WAITS,
        68.5,
        80,
        200,
        False,
    ),
    (
        "long-pause-held",
        LONG_PAUSES_AND_REQUESTS_THAT_NEVER_COME_BACK,
        70.0,
        80,
        200,
        False,
    ),
    (
        "trial-that-hangs",
        LONG_PAUSES_AND_REQUESTS_THAT_NEVER_COME_BACK,
        160.0,
        80,
        200,
        False,
    ),
    (
        "documented-outage",
        OUTAGE_AS_THE_OPERATING_GUIDE_DESCRIBES_IT,
        60.0,
        80,
        200,
        False,
    ),
    (
        "idle-at-the-end",
        EVERY_MECHANISM_IN_TURN,
        119.0,
        80,
        200,
        False,
    ),
    (
        "crowd",
        TWENTY_FOUR_LOOKUPS_AT_ONCE,
        30.0,
        80,
        200,
        False,
    ),
    (
        "folded-short-terminal",
        EVERY_MECHANISM_IN_TURN,
        55.0,
        80,
        16,
        False,
    ),
    (
        "folded-on-trial",
        EVERY_MECHANISM_IN_TURN,
        35.5,
        80,
        14,
        False,
    ),
    (
        "folded-eight-asking",
        PROVIDER_THAT_ANSWERS_SLOWLY,
        20.0,
        80,
        16,
        False,
    ),
    (
        "narrow-terminal",
        EVERY_MECHANISM_IN_TURN,
        31.0,
        60,
        200,
        False,
    ),
    (
        "wide-terminal",
        EVERY_MECHANISM_IN_TURN,
        24.5,
        120,
        200,
        False,
    ),
    (
        "ascii-struggling",
        EVERY_MECHANISM_IN_TURN,
        24.5,
        80,
        200,
        True,
    ),
    (
        "ascii-on-trial",
        EVERY_MECHANISM_IN_TURN,
        35.5,
        80,
        200,
        True,
    ),
    (
        "ascii-rate-limited",
        EVERY_MECHANISM_IN_TURN,
        55.0,
        80,
        200,
        True,
    ),
)

_GUIDE: Final = (
    Path(
        __file__,
    )
    .resolve()
    .parents[3]
    / "docs"
    / "operating.md"
)

_FENCED: Final = compile_pattern(
    pattern=r"```text\n(.*?)```\n",
    flags=DOTALL,
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

# NOTE:
# The numbers of rows the view keeps an assignment of the fold's rows for, from one to the loop's eight.
_FOLD_BUDGETS: Final = frozenset(
    range(
        1,
        WORD_ROWS + 1,
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


@final
class _WallClock:
    """
    A wall clock that moves only when a test moves it, and that starts at the
    instant `_stored` reports at.
    """

    def __init__(
        self,
    ) -> None:
        self.now = EPOCH

    def __call__(
        self,
    ) -> datetime:
        return self.now


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
            wall_clock=_WallClock(),
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

    assert all(
        isinstance(
            row,
            Text,
        )
        for row in rows.renderables
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


# NOTE:
# What the provider did to one lookup, by the recorder's truth, and what the view made of it at the read the lookup
# left the client at: the lookup is its lemma's stay at the client, which ends at the first read without the lemma.
@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _Left:
    followed: Followed

    ending: Ending | None

    marks: tuple[RequestOutcome, ...]


_LEFT: Final[dict[Script, tuple[_Left, ...]]] = {}


def _truth_of(
    script: Script,
    /,
) -> MappingProxyType[tuple[str, float], tuple[Ending | None, tuple[RequestOutcome, ...]]]:
    """
    How each lookup that left the client ended, and the requests of it that
    came back failed or refused, by the recorder's truth, keyed by its lemma
    and the read it left at.

    A lookup was turned away if a sleep of a word with its lemma began at or
    after the read before; otherwise its ending is the answer the stage handed
    back for those words.

    Its requests are those for the lemma that arrived at or after the end of
    the latest sleep that ended no later than the read that first saw it, and
    no later than the read it left at, and that came back: `!` for a 429, `✗`
    for a timeout or any status but 200 and 404.
    """

    recording = _recorded(
        script,
    )

    words: dict[str, list[WordTruth]] = {}

    for word in recording.words:
        words.setdefault(
            word.lemma.strip().lower(),
            [],
        ).append(
            word,
        )

    truth: dict[tuple[str, float], tuple[Ending | None, tuple[RequestOutcome, ...]]] = {}

    first: dict[str, float] = {}

    before: tuple[float, frozenset[str]] | None = None

    for entry in recording.timeline:
        if not isinstance(
            entry,
            Watched,
        ):
            continue

        now = frozenset(lookup.lemma for lookup in entry.activity.provider.lookups)

        for lemma in now:
            first.setdefault(
                lemma,
                entry.at,
            )

        if before is not None:
            for lemma in sorted(
                before[1] - now,
            ):
                group = words[lemma]

                sleeps = tuple(sleep for word in group for sleep in word.sleeps)

                if any(sleep.began >= before[0] - _SECOND for sleep in sleeps):
                    ending: Ending | None = Ending.TURNED_AWAY

                else:
                    outcomes = frozenset(word.ending.outcome for word in group if word.ending is not None)

                    ending = _TRUTH_ENDINGS.get(
                        outcomes,
                    )

                begun = max(
                    (sleep.ended for sleep in sleeps if sleep.ended <= first[lemma] + _SECOND),
                    default=-inf,
                )

                requests = MappingProxyType(
                    mapping={request.number: request for word in group for request in word.requests},
                )

                marks = tuple(
                    RequestOutcome.REFUSED if request.status == 429 else RequestOutcome.FAILED
                    for request in sorted(
                        requests.values(),
                        key=lambda request: request.arrived,
                    )
                    if begun - _SECOND <= request.arrived <= entry.at + _SECOND
                    and request.answered is not None
                    and request.status not in _ANSWERED_STATUSES
                )

                truth[
                    (
                        lemma,
                        entry.at,
                    )
                ] = (
                    ending,
                    marks,
                )

                first.pop(
                    lemma,
                )

        before = (
            entry.at,
            now,
        )

    return MappingProxyType(
        mapping=truth,
    )


_TRUTH_ENDINGS: Final = MappingProxyType(
    mapping={
        frozenset(
            (LookupOutcome.FOUND,),
        ): Ending.FOUND,
        frozenset(
            (LookupOutcome.NOT_FOUND,),
        ): Ending.NOT_FOUND,
        frozenset(
            (LookupOutcome.UNAVAILABLE,),
        ): Ending.LET_GO,
    },
)


def _lookups_that_left(
    script: Script,
    /,
) -> tuple[_Left, ...]:
    """
    Every lookup the view followed that left the client in `script`, as it
    stood at the read it left at, with the truth about it.
    """

    if script not in _LEFT:
        truth = _truth_of(
            script,
        )

        left: list[_Left] = []

        for (
            second,
            progress,
        ) in _reads(
            script,
        ):
            seen = progress._keeper.seen

            assert seen is not None

            for followed in seen.lingering:
                if (
                    followed.left_at is None
                    or abs(
                        followed.left_at - second,
                    )
                    > _SECOND
                ):
                    continue

                known = truth.get(
                    (
                        followed.lemma,
                        second,
                    ),
                )

                if known is None:
                    continue

                left.append(
                    _Left(
                        followed=followed,
                        ending=known[0],
                        marks=known[1],
                    ),
                )

        _LEFT[script] = tuple(
            left,
        )

    return _LEFT[script]


# NOTE:
# The statuses of the provider that answer a request, found or not: any other status, or none, is a failure.
_ANSWERED_STATUSES: Final = frozenset(
    (
        200,
        404,
    ),
)

_SEVEN: Final = (
    *_FIVE,
    LONG_PAUSES_AND_REQUESTS_THAT_NEVER_COME_BACK,
    OUTAGE_AS_THE_OPERATING_GUIDE_DESCRIBES_IT,
)


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
    reported_at: datetime = EPOCH,
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
        reported_at=reported_at,
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
            "rows",
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
        rows: int,
        ascii_form: bool,
    ) -> None:
        progress = _view_at(
            script,
            second,
            columns=columns,
            rows=rows,
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
            "rows",
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
        rows: int,
        ascii_form: bool,
    ) -> None:
        progress = _view_at(
            script,
            second,
            columns=columns,
            rows=rows,
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
            "rows",
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
        rows: int,
        ascii_form: bool,
    ) -> None:
        progress = _view_at(
            script,
            second,
            columns=columns,
            rows=rows,
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

    def test_every_read_of_the_tour_at_60_columns_draws_the_narrow_form(
        self,
    ) -> None:
        reads = 0

        for (
            _,
            progress,
        ) in _reads(
            EVERY_MECHANISM_IN_TURN,
            columns=60,
        ):
            rows = _plain(
                progress,
                columns=60,
            )

            assert any("the loop is drawn from 73 columns" in row for row in rows)

            assert not any(
                row.startswith(
                    "│ last ",
                )
                for row in rows
            )

            reads += 1

        assert reads > 400

    def test_a_workers_sixteen_lookups_fold_to_fewer_rows_in_a_short_terminal(
        self,
    ) -> None:
        (
            progress,
            _,
        ) = _progress(
            rows=16,
        )

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

        assert (
            _frame_height(
                progress,
            )
            <= 16
        )

        assert any(
            row.startswith(
                "│ and ",
            )
            and " more asking " in row
            for row in _plain(
                progress,
            )
        )

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
    @mark.parametrize(
        argnames="rows",
        argvalues=(
            200,
            16,
        ),
    )
    def test_drawing_the_same_state_twice_gives_the_same_panel_and_changes_nothing_the_view_keeps(
        self,
        *,
        rows: int,
    ) -> None:
        recording = _recorded(
            EVERY_MECHANISM_IN_TURN,
        )

        (
            progress,
            clock,
        ) = _progress(
            rows=rows,
        )

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

            # NOTE:
            # What the view keeps is read-only, so a drawing that changed a lookup's marks, its rows, or the word last
            # out would change what it says of itself, which this text holds, contents and all.
            said = repr(
                kept,
            )

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

                assert (
                    repr(
                        kept,
                    )
                    == said
                )

            clock.now = entry.at

            assert (
                _sent(
                    progress,
                    color=True,
                )
                == first
            )

            assert progress._keeper.seen is kept

            assert (
                repr(
                    kept,
                )
                == said
            )

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


def _kept(
    progress: OperatorProgress,
    /,
) -> tuple[object, ...]:
    """
    Everything the view keeps: what the drawing reads, and what the follower
    keeps of its own.
    """

    follower = progress._keeper._follower

    return (
        repr(
            progress._keeper.seen,
        ),
        follower._followed,
        follower._lingering,
        follower._out,
        follower._turned_away,
        follower._number,
        repr(
            follower._before,
        ),
        follower._unavailable_heard,
        follower._loop._slots,
        {
            budget: assigner._slots
            for (
                budget,
                assigner,
            ) in follower._folds.items()
        },
    )


def _followed(
    progress: OperatorProgress,
    /,
) -> tuple[Followed, ...]:
    """
    The lookups at the client that the view follows.
    """

    seen = progress._keeper.seen

    assert seen is not None

    return seen.followed


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

    def test_a_workers_analysis_in_a_short_terminal_folds_the_line_at_the_bucket_into_one_row(
        self,
    ) -> None:
        # NOTE:
        # A row for each word is drawn where the terminal has room for it, and the line at the bucket is folded into
        # one row only where it has not: this terminal leaves the panel 15 rows under a learner's line of two, and a
        # row for each of the eight words that can have one takes 17.
        (
            progress,
            _,
        ) = _progress(
            rows=17,
        )

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


def _timed() -> tuple[OperatorProgress, FakeMonotonicClock, _WallClock]:
    """
    A view that has read a closed provider, with both of its clocks in the
    test's hands.
    """

    clock = FakeMonotonicClock()

    wall = _WallClock()

    progress = OperatorProgress(
        console=_asked_console(
            columns=80,
        ),
        clock=clock,
        wall_clock=wall,
    )

    _watched(
        progress,
        _CLOSED,
    )

    return (
        progress,
        clock,
        wall,
    )


def _pass(
    clock: FakeMonotonicClock,
    wall: _WallClock,
    seconds: float,
    /,
) -> None:
    """
    Let `seconds` go by on both clocks.
    """

    clock.advance(
        seconds=seconds,
    )

    wall.now += timedelta(
        seconds=seconds,
    )


def _wake(
    progress: OperatorProgress,
    clock: FakeMonotonicClock,
    /,
) -> float:
    """
    The wait the view holds for the words to ask again, as of now.
    """

    seen = progress._keeper.seen

    assert seen is not None

    wake = held_of(
        seen=seen,
        now=clock(),
    ).wake_in

    assert wake is not None

    return wake


def _drawn_wait(
    progress: OperatorProgress,
    /,
) -> int:
    """
    The seconds the row of the words to ask again draws after `next in`.
    """

    found = search(
        pattern=r"next in (\d+) s",
        string=_row_starting(
            progress,
            "to ask again",
        ),
    )

    assert found is not None

    return int(
        found.group(
            1,
        ),
    )


@final
class TestAWorkersWaitCountsDownFromWhenItWasWritten:
    def test_one_report_heard_at_every_poll_never_has_its_wait_rise_and_is_drawn_so(
        self,
    ) -> None:
        (
            progress,
            clock,
            wall,
        ) = _timed()

        report = _stored(
            steps=40,
            done=21,
            waiting=19,
            next_in=7.0,
        )

        # NOTE:
        # The worker wrote the report 0.3 s before the command first heard it, and the command hears it again at every
        # poll, once a second, because the worker writes only what changed.
        _pass(
            clock,
            wall,
            0.3,
        )

        waits: list[float] = []

        drawn: list[int] = []

        for poll in range(
            5,
        ):
            # NOTE:
            # The wait just before a hearing is read too, since a hearing that anchors the report anew shows as a rise
            # between the read before it and the read after.
            if poll:
                before = _wake(
                    progress,
                    clock,
                )

                assert (
                    abs(
                        before - (7.0 - (0.3 + poll)),
                    )
                    < 0.01
                ), (
                    poll,
                    before,
                )

                waits.append(
                    before,
                )

            progress.processing(
                progress=report,
            )

            for halfway in (
                0.0,
                0.5,
            ):
                if halfway:
                    _pass(
                        clock,
                        wall,
                        halfway,
                    )

                expected = 7.0 - (0.3 + poll + halfway)

                wake = _wake(
                    progress,
                    clock,
                )

                assert (
                    abs(
                        wake - expected,
                    )
                    < 0.01
                ), (
                    poll,
                    halfway,
                    wake,
                    expected,
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
                        f"next in {
                            duration(
                                seconds=expected,
                            )
                        }",
                    )
                ), (
                    poll,
                    halfway,
                )

                waits.append(
                    wake,
                )

                drawn.append(
                    _drawn_wait(
                        progress,
                    ),
                )

            _pass(
                clock,
                wall,
                0.5,
            )

        assert (
            len(
                waits,
            )
            == 14
        )

        assert all(
            later <= earlier
            for (
                earlier,
                later,
            ) in pairwise(
                waits,
            )
        ), waits

        assert all(
            later <= earlier
            for (
                earlier,
                later,
            ) in pairwise(
                drawn,
            )
        ), drawn

    def test_a_newer_report_sets_its_own_wait_from_when_it_was_written_later_or_sooner(
        self,
    ) -> None:
        (
            progress,
            clock,
            wall,
        ) = _timed()

        _pass(
            clock,
            wall,
            0.3,
        )

        progress.processing(
            progress=_stored(
                steps=40,
                done=21,
                waiting=19,
                next_in=7.0,
            ),
        )

        assert (
            abs(
                _wake(
                    progress,
                    clock,
                )
                - 6.7
            )
            < 0.01
        )

        _pass(
            clock,
            wall,
            1.0,
        )

        # NOTE:
        # The next held word may wake later than the one the report before named, so the wait rises, rightly.
        progress.processing(
            progress=_stored(
                steps=40,
                done=21,
                waiting=19,
                next_in=9.0,
                reported_at=wall.now
                - timedelta(
                    seconds=0.2,
                ),
            ),
        )

        assert (
            abs(
                _wake(
                    progress,
                    clock,
                )
                - 8.8
            )
            < 0.01
        )

        _pass(
            clock,
            wall,
            1.0,
        )

        progress.processing(
            progress=_stored(
                steps=40,
                done=21,
                waiting=19,
                next_in=1.5,
                reported_at=wall.now
                - timedelta(
                    seconds=0.2,
                ),
            ),
        )

        assert (
            abs(
                _wake(
                    progress,
                    clock,
                )
                - 1.3
            )
            < 0.01
        )

    def test_a_report_from_a_clock_ahead_of_the_commands_is_heard_at_age_nought(
        self,
    ) -> None:
        (
            progress,
            clock,
            wall,
        ) = _timed()

        report = _stored(
            steps=40,
            done=21,
            waiting=19,
            next_in=7.0,
            reported_at=wall.now
            + timedelta(
                seconds=5.0,
            ),
        )

        progress.processing(
            progress=report,
        )

        assert (
            abs(
                _wake(
                    progress,
                    clock,
                )
                - 7.0
            )
            < 0.01
        )

    def test_a_report_dated_ahead_of_the_command_never_has_its_wait_rise_at_any_poll(
        self,
    ) -> None:
        (
            progress,
            clock,
            wall,
        ) = _timed()

        report = _stored(
            steps=40,
            done=21,
            waiting=19,
            next_in=7.0,
            reported_at=wall.now
            + timedelta(
                seconds=2.5,
            ),
        )

        waits: list[float] = []

        for poll in range(
            6,
        ):
            if poll:
                waits.append(
                    _wake(
                        progress,
                        clock,
                    ),
                )

            progress.processing(
                progress=report,
            )

            waits.append(
                _wake(
                    progress,
                    clock,
                ),
            )

            _pass(
                clock,
                wall,
                1.0,
            )

        assert (
            len(
                waits,
            )
            == 11
        )

        assert (
            abs(
                waits[0] - 7.0,
            )
            < 0.01
        )

        assert all(
            later <= earlier + 1e-9
            for (
                earlier,
                later,
            ) in pairwise(
                waits,
            )
        ), waits

    def test_a_wall_clock_stepped_back_between_two_hearings_does_not_raise_the_wait(
        self,
    ) -> None:
        (
            progress,
            clock,
            wall,
        ) = _timed()

        report = _stored(
            steps=40,
            done=21,
            waiting=19,
            next_in=7.0,
        )

        _pass(
            clock,
            wall,
            0.3,
        )

        progress.processing(
            progress=report,
        )

        waits = [
            _wake(
                progress,
                clock,
            ),
        ]

        # NOTE:
        # A second goes by on the command's monotonic clock while its wall clock is set back half a second, so the
        # report seems younger at the second hearing than at the first.
        clock.advance(
            seconds=1.0,
        )

        wall.now -= timedelta(
            seconds=0.5,
        )

        waits.append(
            _wake(
                progress,
                clock,
            ),
        )

        progress.processing(
            progress=report,
        )

        waits.append(
            _wake(
                progress,
                clock,
            ),
        )

        assert all(
            later <= earlier + 1e-9
            for (
                earlier,
                later,
            ) in pairwise(
                waits,
            )
        ), waits


@final
class TestACompletedAnalysisIsDrawnComplete:
    def test_after_a_stale_report_the_tally_reads_every_word_resolved_and_none_to_ask(
        self,
    ) -> None:
        (
            progress,
            _,
            _,
        ) = _timed()

        progress.processing(
            progress=_stored(
                steps=40,
                done=38,
                waiting=2,
                next_in=3.0,
            ),
        )

        assert _row_starting(
            progress,
            "resolved",
        ).startswith(
            "│ resolved 38 of 40",
        )

        assert _row_starting(
            progress,
            "to ask again",
        ).startswith(
            "│ to ask again 2 ",
        )

        progress.completed()

        rows = _plain(
            progress,
        )

        assert _row_starting(
            progress,
            "resolved",
        ).startswith(
            "│ resolved 40 of 40",
        )

        assert any(
            row.startswith(
                "│ to ask 0 ",
            )
            for row in rows
        ), rows

        assert not any("to ask again" in row for row in rows), rows

        assert any("idle: every word has its answer" in row for row in rows), rows

    def test_a_view_on_a_terminal_draws_the_completed_state_once_before_it_stops(
        self,
    ) -> None:
        stream = StringIO()

        clock = FakeMonotonicClock()

        with OperatorProgress(
            console=Console(
                force_terminal=True,
                force_interactive=True,
                file=stream,
                width=110,
                height=200,
            ),
            refresh_per_second=0.01,
            clock=clock,
            wall_clock=_WallClock(),
        ) as progress:
            _watched(
                progress,
                _CLOSED,
            )

            progress.processing(
                progress=_stored(
                    steps=40,
                    done=38,
                    waiting=2,
                    next_in=3.0,
                ),
            )

            progress.completed()

            drawn = _drawn(
                stream,
            )

        panels = drawn.split(
            sep="╭",
        )

        assert (
            drawn.count(
                "resolved 40 of 40",
            )
            == 1
        )

        assert "resolved 40 of 40" in panels[-1]

    def test_where_no_dictionary_stage_was_heard_completing_changes_nothing_the_view_draws(
        self,
    ) -> None:
        (
            progress,
            _,
            _,
        ) = _timed()

        before = (
            _plain(
                progress,
            ),
            _kept(
                progress,
            ),
        )

        progress.completed()

        assert (
            _plain(
                progress,
            ),
            _kept(
                progress,
            ),
        ) == before

    def test_where_the_dictionary_report_names_no_steps_completing_changes_nothing_the_view_draws(
        self,
    ) -> None:
        (
            progress,
            _,
            _,
        ) = _timed()

        progress.processing(
            progress=replace(
                _stored(
                    steps=40,
                    done=5,
                    waiting=2,
                    next_in=3.0,
                ),
                steps=None,
            ),
        )

        before = (
            _plain(
                progress,
            ),
            _kept(
                progress,
            ),
        )

        progress.completed()

        assert (
            _plain(
                progress,
            ),
            _kept(
                progress,
            ),
        ) == before


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


@final
class TestEachWordHasARow:
    def test_the_marks_of_every_lookup_the_view_followed_are_the_providers_script_or_it_ended_unknown(
        self,
    ) -> None:
        total = 0

        with_marks = 0

        right = 0

        wrong: list[tuple[str, tuple[RequestOutcome, ...], tuple[RequestOutcome, ...]]] = []

        for script in _SEVEN:
            for left in _lookups_that_left(
                script,
            ):
                total += 1

                truth = left.marks[-MARKS_AT_MOST:]

                matches = left.followed.marks == truth

                if truth:
                    with_marks += 1

                    right += matches

                # NOTE:
                # The one kind of lookup the rules miss: it was asking at the read before and left in the same quarter
                # second as another lookup that ended otherwise, so the counts leave its ending unknown and its last
                # request gets no mark.
                if not matches and left.followed.ending is not Ending.ENDED:
                    wrong.append(
                        (
                            left.followed.lemma,
                            left.followed.marks,
                            truth,
                        ),
                    )

        print(
            f"marks: {total} lookups left the client, {with_marks} with a mark, {right} marked as the provider's "
            f"script says, {with_marks - right} not, all of them ended unknown",
        )

        assert not wrong, wrong

        assert total > 600

        assert right > 0.9 * with_marks

    def test_the_ending_of_every_lookup_is_the_providers_or_unknown_and_never_another(
        self,
    ) -> None:
        total = 0

        known = 0

        right = 0

        wrong: list[tuple[str, Ending, Ending | None]] = []

        for script in _SEVEN:
            for left in _lookups_that_left(
                script,
            ):
                ending = left.followed.ending

                assert ending is not None

                if left.ending is None:
                    continue

                total += 1

                if ending is Ending.ENDED:
                    continue

                known += 1

                if ending is left.ending:
                    right += 1

                else:
                    wrong.append(
                        (
                            left.followed.lemma,
                            ending,
                            left.ending,
                        ),
                    )

        mistaken = len(
            wrong,
        )

        print(
            f"endings: {total} lookups left the client, {known} told, {right} right, {mistaken} wrong, "
            f"{total - known} ended unknown",
        )

        assert not wrong, wrong

        assert total > 700

        assert right > 0.9 * total

    @mark.parametrize(
        argnames="script",
        argvalues=(
            EVERY_MECHANISM_IN_TURN,
            TWENTY_FOUR_LOOKUPS_AT_ONCE,
        ),
    )
    def test_a_word_keeps_its_row_in_the_loop_and_in_each_fold_for_as_long_as_it_has_one(
        self,
        *,
        script: Script,
    ) -> None:
        before: dict[int, Mapping[int, int]] = {}

        compared = 0

        for (
            second,
            progress,
        ) in _reads(
            script,
        ):
            seen = progress._keeper.seen

            assert seen is not None

            # NOTE:
            # The loop's rows are key 0, and each fold's the budget it keeps rows for.
            assignments = MappingProxyType(
                mapping={
                    0: seen.rows.loop,
                    **seen.rows.folded,
                },
            )

            assert seen.rows.folded.keys() == _FOLD_BUDGETS

            for (
                key,
                rows,
            ) in assignments.items():
                now = MappingProxyType(
                    mapping={
                        row.number: slot
                        for (
                            slot,
                            row,
                        ) in enumerate(
                            iterable=rows,
                        )
                        if row is not None
                    },
                )

                assert len(
                    now,
                ) <= (key or WORD_ROWS), (
                    second,
                    key,
                )

                for (
                    number,
                    slot,
                ) in before.get(
                    key,
                    {},
                ).items():
                    if number in now:
                        assert now[number] == slot, (
                            second,
                            key,
                            number,
                        )

                        compared += 1

                before[key] = now

        assert compared > 1000

    def test_more_lookups_than_rows_draw_eight_rows_and_one_that_counts_those_in_line(
        self,
    ) -> None:
        progress = _view_at(
            TWENTY_FOUR_LOOKUPS_AT_ONCE,
            30.0,
        )

        rows = _plain(
            progress,
        )

        words = rows[4:-4]

        assert (
            len(
                words,
            )
            == 9
        )

        assert words[-1].startswith(
            "│ and 16 more in line, 5th to 20th ",
        )

        assert not any(
            row.startswith(
                "│ in line ",
            )
            for row in rows
        )

    def test_a_workers_lookups_of_every_kind_draw_eight_rows_and_one_row_that_counts_the_rest_by_kind(
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
                lookups=(
                    *(
                        _asking(
                            f"foo{number}",
                            1.0 + number,
                        )
                        for number in range(
                            9,
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
                    *(
                        LookupActivity(
                            lemma=f"baz{number}",
                            waiting_for=WaitCause.TRIAL,
                            attempt=1,
                            seconds=1.0,
                        )
                        for number in range(
                            3,
                        )
                    ),
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

        assert any(
            row.startswith(
                "│ and 4 more in line, 1st to 4th, 3 more behind the trial, 1 more asking ",
            )
            for row in rows
        )

    def test_a_lemma_that_is_absent_at_a_read_and_present_again_is_a_new_lookup_with_a_clean_row(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        _watched(
            progress,
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

        (first,) = _followed(
            progress,
        )

        assert first.marks == (RequestOutcome.FAILED,)

        clock.now = 0.25

        _watched(
            progress,
            _CLOSED,
        )

        clock.now = 0.5

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        0.1,
                    ),
                ),
            ),
        )

        (second,) = _followed(
            progress,
        )

        assert second.number != first.number

        assert second.marks == ()

        assert "✗" not in _row_starting(
            progress,
            "foo",
        )

    def test_a_word_behind_the_trial_keeps_the_mark_of_the_request_that_failed_and_says_where_it_waits(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        _watched(
            progress,
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

        clock.now = 0.25

        _watched(
            progress,
            replace(
                _CLOSED,
                circuit=BreakerState.HALF_OPEN,
                lookups=(
                    _asking(
                        "bar",
                        0.1,
                        trial=True,
                    ),
                    LookupActivity(
                        lemma="foo",
                        waiting_for=WaitCause.TRIAL,
                        attempt=2,
                        seconds=0.35,
                    ),
                ),
            ),
        )

        row = _row_starting(
            progress,
            "foo",
        )

        assert "●" in row

        assert (
            row.count(
                "✗",
            )
            == 1
        )

        assert "behind the trial" in row

    def test_a_lemma_whose_attempt_falls_between_two_reads_is_a_new_lookup(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    replace(
                        _asking(
                            "foo",
                            0.5,
                        ),
                        attempt=3,
                    ),
                ),
            ),
        )

        (first,) = _followed(
            progress,
        )

        assert first.attempt == 3

        clock.now = 0.25

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        0.1,
                    ),
                ),
            ),
        )

        (second,) = _followed(
            progress,
        )

        assert second.number != first.number

        assert (
            second.attempt,
            second.marks,
        ) == (
            1,
            (),
        )

    def test_a_lookup_that_earns_six_marks_draws_the_newest_five(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        for attempt in range(
            1,
            8,
        ):
            clock.now = attempt * 0.25

            _watched(
                progress,
                replace(
                    _CLOSED,
                    lookups=(
                        LookupActivity(
                            lemma="foo",
                            waiting_for=WaitCause.RETRY,
                            attempt=attempt,
                            seconds=0.1,
                            delay_seconds=0.5,
                            reason=(FailureReason.RATE_LIMITED if attempt > 5 else FailureReason.PROVIDER_ERROR),
                        ),
                    ),
                ),
            )

        (followed,) = _followed(
            progress,
        )

        assert followed.marks == (
            RequestOutcome.FAILED,
            RequestOutcome.FAILED,
            RequestOutcome.FAILED,
            RequestOutcome.REFUSED,
            RequestOutcome.REFUSED,
        )

        row = _row_starting(
            progress,
            "foo",
        )

        assert "✗✗✗!!" in row

        assert "✗✗✗✗" not in row

    def test_a_held_word_that_has_woken_and_is_at_the_client_is_counted_once_in_to_ask(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        for (
            second,
            lookups,
            unavailable,
        ) in (
            (
                0.0,
                (
                    _asking(
                        "foo",
                        0.5,
                    ),
                ),
                0,
            ),
            (
                0.25,
                (),
                1,
            ),
        ):
            clock.now = second

            _watched(
                progress,
                replace(
                    _CLOSED,
                    lookups=lookups,
                ),
                LookupCounts(
                    cached=0,
                    found=0,
                    not_found=0,
                    unavailable=MappingProxyType(
                        mapping={
                            FailureReason.PROVIDER_ERROR: unavailable,
                        },
                    ),
                ),
            )

        # NOTE:
        # The stage holds the word it was turned away, and reports it with two others; it is asked again, so it is at
        # the client with a word the stage asks for the first time, and the report still counts it among its three.
        clock.now = 0.5

        progress.dictionary_waiting(
            held=(
                held_words(
                    reason=FailureReason.PROVIDER_ERROR,
                    turned_away=3,
                    turned_away_wake_in_seconds=9.0,
                ),
            ),
            ending=False,
            retry_in_seconds=9.0,
            patience_left_seconds=170.0,
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        0.1,
                    ),
                    _asking(
                        "bar",
                        0.1,
                    ),
                ),
            ),
            LookupCounts(
                cached=0,
                found=0,
                not_found=0,
                unavailable=MappingProxyType(
                    mapping={
                        FailureReason.PROVIDER_ERROR: 1,
                    },
                ),
            ),
        )

        assert _plain(
            progress,
        )[1].startswith(
            "│ to ask 26 ",
        )

        assert _row_starting(
            progress,
            "to ask again",
        ).startswith(
            "│ to ask again 2 ",
        )

        assert _row_starting(
            progress,
            "foo",
        ).startswith(
            "│ foo         again ",
        )

    def test_at_the_tours_47_5_s_where_the_report_holds_nothing_to_ask_is_the_recorders_truth(
        self,
    ) -> None:
        recording = _recorded(
            EVERY_MECHANISM_IN_TURN,
        )

        read = tuple(
            entry
            for entry in recording.timeline
            if isinstance(
                entry,
                Watched,
            )
            and entry.at <= 47.5 + _SECOND
        )[-1]

        steps = next(
            entry.steps
            for entry in recording.timeline
            if isinstance(
                entry,
                StageStarted,
            )
        )

        done = sum(
            1
            for entry in recording.timeline
            if isinstance(
                entry,
                LookedUp,
            )
            and entry.at <= read.at + _SECOND
        )

        asleep = sum(1 for word in recording.words for sleep in word.sleeps if sleep.began <= read.at < sleep.ended)

        at_the_client = len(
            read.activity.provider.lookups,
        )

        # NOTE:
        # The words neither done, nor asleep, nor at the client: here the last report holds none of them, so the words
        # woken and not at the client are all there is to ask.
        assert steps is not None

        truth = steps - done - asleep - at_the_client

        progress = _view_at(
            EVERY_MECHANISM_IN_TURN,
            47.5,
        )

        seen = progress._keeper.seen

        assert seen is not None

        assert seen.done == done

        assert seen.report is not None

        assert seen.report.turned_away + seen.report.others_sleeping == 0

        assert any(followed.earlier for followed in seen.followed)

        assert truth == 54

        assert _plain(
            progress,
        )[1].startswith(
            f"│ to ask {truth} ",
        )

    def test_where_the_report_holds_fewer_words_than_the_lookups_marked_again_to_ask_leaves_none_out_or_twice(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        for (
            second,
            lookups,
            unavailable,
        ) in (
            (
                0.0,
                (
                    _asking(
                        "foo",
                        0.5,
                    ),
                ),
                0,
            ),
            (
                0.25,
                (),
                1,
            ),
            (
                0.5,
                (
                    _asking(
                        "foo",
                        0.1,
                    ),
                    _asking(
                        "bar",
                        0.1,
                    ),
                ),
                1,
            ),
        ):
            clock.now = second

            _watched(
                progress,
                replace(
                    _CLOSED,
                    lookups=lookups,
                ),
                LookupCounts(
                    cached=0,
                    found=0,
                    not_found=0,
                    unavailable=MappingProxyType(
                        mapping={
                            FailureReason.PROVIDER_ERROR: unavailable,
                        },
                    ),
                ),
            )

        # NOTE:
        # The stage has reported no held word, and `foo` is at the client marked `again`, so nothing is to ask again
        # and the two lookups at the client are the only words of the thirty that are neither done nor to ask.
        assert _plain(
            progress,
        )[1].startswith(
            "│ to ask 28 ",
        )

        assert not any(
            row.startswith(
                "│ to ask again",
            )
            for row in _plain(
                progress,
            )
        )

    def test_a_view_given_the_services_reads_before_any_stage_or_report_tells_no_ending_from_unavailable_alone(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        for (
            second,
            lookups,
            unavailable,
        ) in (
            (
                0.0,
                (
                    _asking(
                        "foo",
                        0.5,
                    ),
                ),
                0,
            ),
            (
                0.25,
                (),
                1,
            ),
            (
                0.5,
                (
                    _asking(
                        "foo",
                        0.1,
                    ),
                ),
                1,
            ),
        ):
            clock.now = second

            _watched(
                progress,
                replace(
                    _CLOSED,
                    lookups=lookups,
                ),
                LookupCounts(
                    cached=0,
                    found=0,
                    not_found=0,
                    unavailable=MappingProxyType(
                        mapping={
                            FailureReason.PROVIDER_ERROR: unavailable,
                        },
                    ),
                ),
            )

            if second == 0.25:
                row = _row_starting(
                    progress,
                    "foo",
                )

                assert "◂ ended" in row

                assert "turned away" not in row

        assert "again" not in _row_starting(
            progress,
            "foo",
        )

    def test_where_the_service_holds_the_lookups_a_lookup_that_leaves_as_only_unavailable_grows_ended_unknown(
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
            ),
        )

        for (
            second,
            lookups,
            unavailable,
        ) in (
            (
                0.0,
                (
                    _asking(
                        "foo",
                        0.5,
                    ),
                ),
                0,
            ),
            (
                0.25,
                (),
                1,
            ),
        ):
            clock.now = second

            _watched(
                progress,
                replace(
                    _CLOSED,
                    lookups=lookups,
                ),
                LookupCounts(
                    cached=0,
                    found=0,
                    not_found=0,
                    unavailable=MappingProxyType(
                        mapping={
                            FailureReason.PROVIDER_ERROR: unavailable,
                        },
                    ),
                ),
            )

        row = _row_starting(
            progress,
            "foo",
        )

        assert "◂ ended" in row

        assert "turned away" not in row

        assert "let go" not in row

        clock.now = 0.5

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        0.1,
                    ),
                ),
            ),
            LookupCounts(
                cached=0,
                found=0,
                not_found=0,
                unavailable=MappingProxyType(
                    mapping={
                        FailureReason.PROVIDER_ERROR: 1,
                    },
                ),
            ),
        )

        assert "again" not in _row_starting(
            progress,
            "foo",
        )

    def test_a_row_that_has_left_stays_for_a_second_and_the_last_word_out_is_bright_for_one_and_gone_after_ten(
        self,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        "foo",
                        0.5,
                    ),
                ),
            ),
        )

        clock.now = 10.0

        _watched(
            progress,
            _CLOSED,
            LookupCounts(
                cached=0,
                found=0,
                not_found=1,
                unavailable=MappingProxyType(
                    mapping={},
                ),
            ),
        )

        left_at = 10.0

        for (
            after,
            has_row,
            bright,
            on_the_wire,
        ) in (
            (
                0.0,
                True,
                True,
                True,
            ),
            (
                0.75,
                True,
                True,
                True,
            ),
            (
                1.0,
                False,
                False,
                True,
            ),
            (
                9.75,
                False,
                False,
                True,
            ),
            (
                10.0,
                False,
                False,
                False,
            ),
        ):
            clock.now = left_at + after

            _watched(
                progress,
                _CLOSED,
                LookupCounts(
                    cached=0,
                    found=0,
                    not_found=1,
                    unavailable=MappingProxyType(
                        mapping={},
                    ),
                ),
            )

            rows = _plain(
                progress,
            )

            assert (
                any(
                    row.startswith(
                        "│ foo ",
                    )
                    and "✓ not found" in row
                    for row in rows
                )
                is has_row
            ), after

            assert any("◎─ ✓ foo " in row for row in rows) is on_the_wire, after

            if on_the_wire:
                wire = next(
                    row
                    for row in _cells(
                        _sent(
                            progress,
                            color=True,
                        ),
                    )
                    if "".join(cell.character for cell in row).count(
                        "◎─ ✓ foo ",
                    )
                )

                text = "".join(cell.character for cell in wire)

                start = text.index(
                    "✓ foo",
                )

                assert wire[start].dim is not bright, after

                assert wire[start + 2].bold is bright, after

    @mark.parametrize(
        argnames="lemma",
        argvalues=(
            "漢字漢字漢字漢字漢字漢字",
            "e\N{COMBINING ACUTE ACCENT}" * 13,
        ),
    )
    def test_a_lemma_of_wide_characters_or_with_combining_marks_leaves_every_row_its_width_in_cells(
        self,
        *,
        lemma: str,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress()

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        lemma,
                        0.5,
                    ),
                    LookupActivity(
                        lemma=lemma + "s",
                        waiting_for=WaitCause.BUDGET,
                        attempt=1,
                        seconds=0.1,
                    ),
                ),
            ),
        )

        clock.now = 0.25

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    LookupActivity(
                        lemma=lemma + "s",
                        waiting_for=WaitCause.BUDGET,
                        attempt=1,
                        seconds=0.35,
                    ),
                ),
            ),
            LookupCounts(
                cached=0,
                found=1,
                not_found=0,
                unavailable=MappingProxyType(
                    mapping={},
                ),
            ),
        )

        rows = _canvas_rows(
            progress,
        )

        assert (
            len(
                rows,
            )
            > 5
        )

        for row in rows:
            assert row.cell_len == 75, row.plain

        assert any(
            row.plain.startswith(
                lemma[:3],
            )
            for row in rows
        )

        assert any("✓ " in row.plain and "…" in row.plain for row in rows)

    @mark.parametrize(
        argnames="color",
        argvalues=(
            False,
            True,
        ),
    )
    @mark.parametrize(
        argnames="lemma",
        argvalues=(
            "漢字漢字漢字漢字漢字漢字",
            "e\N{COMBINING ACUTE ACCENT}" * 13,
            "na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve" * 3,
            (
                "\N{LATIN CAPITAL LETTER C WITH CEDILLA}a"
                "\N{LATIN SMALL LETTER SHARP S}\N{LATIN SMALL LETTER O WITH STROKE}"
            ),
        ),
    )
    def test_a_lemma_a_stream_cannot_encode_leaves_every_row_its_width_in_characters_where_the_rule_is_set(
        self,
        *,
        lemma: str,
        color: bool,
    ) -> None:
        (
            progress,
            clock,
        ) = _progress(
            ascii_form=True,
        )

        progress.stage_started(
            stage=Stage.DICTIONARY_ENRICHMENT,
            steps=30,
        )

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    _asking(
                        lemma,
                        0.5,
                    ),
                    LookupActivity(
                        lemma=lemma + "s",
                        waiting_for=WaitCause.BUDGET,
                        attempt=1,
                        seconds=0.1,
                    ),
                ),
            ),
        )

        clock.now = 0.25

        _watched(
            progress,
            replace(
                _CLOSED,
                lookups=(
                    LookupActivity(
                        lemma=lemma + "s",
                        waiting_for=WaitCause.BUDGET,
                        attempt=1,
                        seconds=0.35,
                    ),
                ),
            ),
            LookupCounts(
                cached=0,
                found=1,
                not_found=0,
                unavailable=MappingProxyType(
                    mapping={},
                ),
            ),
        )

        # NOTE:
        # Standard error as Python makes it under US-ASCII, except that its error handler is the one the command sets:
        # strict, so that a character the rule left unwritten would raise here, and a row that is a character too wide
        # fails the width below.
        stream = TextIOWrapper(
            buffer=BytesIO(),
            encoding="ascii",
        )

        keep_cells_on(
            streams=(stream,),
        )

        Console(
            color_system="standard" if color else None,
            force_terminal=color,
            file=stream,
            width=80,
            height=200,
            highlight=False,
        ).print(
            progress.panel(),
        )

        stream.flush()

        assert isinstance(
            stream.buffer,
            BytesIO,
        )

        written = stream.buffer.getvalue().decode(
            encoding="ascii",
        )

        assert ("\x1b[" in written) is color

        rows = sub(
            pattern=r"\x1b\[[0-9;]*m",
            repl="",
            string=written,
        ).splitlines()

        widths = frozenset(
            len(
                row,
            )
            for row in rows
        )

        assert (
            len(
                rows,
            )
            > 5
        )

        assert widths == frozenset(
            (
                len(
                    rows[0],
                ),
            ),
        ), rows

        assert (
            max(
                widths,
            )
            <= 80
        )


@final
class TestTheGuide:
    def test_every_frame_the_guide_shows_is_a_panel_the_product_draws_and_every_row_it_quotes_a_whole_row(
        self,
    ) -> None:
        text = _GUIDE.read_text(
            encoding="utf-8",
        )

        start = text.index(
            "\n## The operator view\n",
        )

        section = text[
            start : text.index(
                "\n## ",
                start + 1,
            )
        ]

        fixtures = MappingProxyType(
            mapping={
                path.stem: path.read_text(
                    encoding="utf-8",
                )
                for path in _FIXTURES.glob(
                    pattern="*.txt",
                )
            },
        )

        rows = frozenset(row for fixture in fixtures.values() for row in fixture.splitlines())

        shown = []

        for block in _FENCED.findall(
            string=section,
        ):
            if block in fixtures.values():
                shown.append(
                    block,
                )

                continue

            assert block.splitlines(), block

            for row in block.splitlines():
                assert row in rows, row

        assert (
            len(
                shown,
            )
            >= 5
        )

        assert all(
            block.startswith(
                "╭─ dictionary",
            )
            for block in shown
        )


@final
class TestTheCanvas:
    @mark.parametrize(
        argnames=(
            "puts",
            "expected",
        ),
        argvalues=(
            (
                (
                    (
                        1,
                        "漢",
                    ),
                ),
                " 漢   ",
            ),
            (
                (
                    (
                        1,
                        "漢",
                    ),
                    (
                        2,
                        "x",
                    ),
                ),
                "  x   ",
            ),
            (
                (
                    (
                        1,
                        "漢",
                    ),
                    (
                        0,
                        "x",
                    ),
                    (
                        0,
                        "yz",
                    ),
                ),
                "yz    ",
            ),
            (
                (
                    (
                        1,
                        "漢",
                    ),
                    (
                        0,
                        "xy",
                    ),
                ),
                "xy    ",
            ),
            (
                (
                    (
                        5,
                        "漢",
                    ),
                ),
                "      ",
            ),
            (
                (
                    (
                        0,
                        "e\N{COMBINING ACUTE ACCENT}f",
                    ),
                ),
                "e\N{COMBINING ACUTE ACCENT}f    ",
            ),
        ),
    )
    def test_a_row_is_its_width_in_cells_whatever_is_put_on_it_and_over_what(
        self,
        *,
        puts: tuple[tuple[int, str], ...],
        expected: str,
    ) -> None:
        canvas = Canvas(
            width=6,
        )

        y = canvas.row()

        for (
            x,
            text,
        ) in puts:
            canvas.put(
                y=y,
                x=x,
                text=text,
            )

        (line,) = canvas.lines()

        assert line.plain == expected

        assert line.cell_len == 6


@final
class TestDrawingChangesNothingTheViewKeeps:
    def test_a_view_drawn_at_every_read_and_between_them_keeps_what_one_never_drawn_keeps(
        self,
    ) -> None:
        recording = _recorded(
            EVERY_MECHANISM_IN_TURN,
        )

        (
            drawn,
            drawn_clock,
        ) = _progress(
            rows=16,
        )

        (
            never,
            never_clock,
        ) = _progress(
            rows=16,
        )

        compared = 0

        for entry in recording.timeline:
            _heard(
                drawn,
                drawn_clock,
                entry,
            )

            _heard(
                never,
                never_clock,
                entry,
            )

            if not isinstance(
                entry,
                Watched,
            ):
                continue

            for step in (
                0.0,
                0.05,
                0.2,
            ):
                drawn_clock.now = entry.at + step

                _sent(
                    drawn,
                    color=True,
                )

            drawn_clock.now = entry.at

            assert _kept(
                drawn,
            ) == _kept(
                never,
            ), entry.at

            compared += 1

        assert compared > 400
