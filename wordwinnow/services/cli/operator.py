"""
What the command line shows an operator while an analysis runs: the learner's
line, and under it what the dictionary is doing.

Under the line stands one panel, whose border and title say at a glance how
the provider has been answering the client: answering, rate-limited, or
failing.

In it stand the circuit, with why it opened; when the client next sends a
request, whenever something holds its requests back; the last minute of the
client's own requests, one mark a second, and when one was last answered if
that was longer ago; how the dictionary's lookups have been answered since the
view began, the ones it turned away and the retries; the request budget; and
the lookups in flight, counted by what they wait for, with each one asking the
provider or pausing before a retry on a row of its own, a bar against its
timeout or its pause.

Every row is what the client saw of its own requests or what it decided; none
says what the provider did otherwise, which the client cannot see.

Like the learner's line, the view is drawn on standard error, only at an
interactive terminal, and disappears when the analysis ends.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from math import (
    ceil,
)
from time import (
    monotonic,
)
from types import (
    MappingProxyType,
    TracebackType,
)
from typing import (
    Final,
    Self,
    final,
)

from rich.console import (
    Console,
    Group,
    RenderableType,
)
from rich.live import (
    Live,
)
from rich.panel import (
    Panel,
)
from rich.progress_bar import (
    ProgressBar,
)
from rich.table import (
    Table,
)
from rich.text import (
    Text,
)

from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    LookupActivity,
    ProviderActivity,
    RequestOutcome,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
    MonotonicClock,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.services.cli.progress import (
    TerminalProgress,
)

# NOTE:
# The most lookups asking the provider or pausing before a retry drawn one to a row: the eight the defaults allow an
# analysis at once; the lookups waiting for anything else are only counted, because a row each would say one thing
# many times.
_ROWS: Final = 8

_ON_A_ROW: Final = frozenset(
    (
        WaitCause.PROVIDER,
        WaitCause.RETRY,
    ),
)

# NOTE:
# The widths that keep a row of a lookup in flight within 80 columns, its longest description but one included.
_LEMMA_WIDTH: Final = 10

_BAR_WIDTH: Final = 10

# NOTE:
# What each reason a lookup went unanswered for, or was asked again for, is called in the view; keyed by string, since
# an analysis's counts of unanswered lookups name each reason by its value.
_REASONS: Final[MappingProxyType[str, str]] = MappingProxyType(
    mapping={
        FailureReason.CIRCUIT_OPEN: "held back",
        FailureReason.TIMED_OUT: "timed out",
        FailureReason.TRANSPORT_ERROR: "connection failed",
        FailureReason.RATE_LIMITED: "rate limited",
        FailureReason.PROVIDER_ERROR: "server error",
        FailureReason.INVALID_RESPONSE: "unreadable answer",
    },
)

# NOTE:
# What the lookups in flight wait for, in the order the view counts them: the ones at the provider first, and then the
# ones held back, nearest the provider first; short enough that all but one fit on a row of 80 columns.
_WAITING: Final = MappingProxyType(
    mapping={
        WaitCause.PROVIDER: "asking",
        WaitCause.RETRY: "retrying",
        WaitCause.TRIAL: "behind the trial",
        WaitCause.COOLDOWN: "paused",
        WaitCause.BUDGET: "queued for a token",
    },
)

_READING: Final = "reading what the dictionary is doing"

# NOTE:
# The last minute of the client's own requests, one mark a second, the newest on the right: the worst outcome that
# came back in that second, a failure over a refusal over an answer, or a dot when none did; a minute holds six of the
# trials an open circuit sends, so their rhythm shows.
_MINUTE: Final = 60

_MARKS: Final = MappingProxyType(
    mapping={
        RequestOutcome.ANSWERED: (
            "✓",
            "green",
            1,
        ),
        RequestOutcome.REFUSED: (
            "!",
            "yellow",
            2,
        ),
        RequestOutcome.FAILED: (
            "✗",
            "red",
            3,
        ),
    },
)

# NOTE:
# The widest the panel grows: its longest row fits within it, and on a wider terminal the eye still reads each row
# from its label in one sweep.
_PANEL_WIDTH: Final = 100


@final
class OperatorProgress:
    """
    The learner's line and, under it, what the dictionary is doing.

    Satisfies `AnalysisProgress`, `RemoteAnalysisProgress`, and
    `DictionaryWatch` structurally, and shows nothing unless its console is an
    interactive terminal.

    The counts are the dictionary's since the first activity the view hears,
    which every later one is counted from: the analysis's own while the
    dictionary serves no other.
    """

    def __init__(
        self,
        *,
        console: Console,
        refresh_per_second: float = 10,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._line: Final = TerminalProgress(
            console=console,
        )

        self._console: Final = console

        self._clock: Final = clock

        self._read: tuple[DictionaryActivity, float] | None = None

        self._baseline: DictionaryActivity | None = None

        self._unwatched: str | None = None

        # NOTE:
        # The display draws the view once as it is built, so everything the view reads is set before it.
        self._live: Final = Live(
            console=console,
            refresh_per_second=refresh_per_second,
            transient=True,
            get_renderable=self._render,
        )

    @property
    def shown(
        self,
    ) -> bool:
        """
        Whether a person sees this view at all.
        """

        return self._line.shown

    def __enter__(
        self,
    ) -> Self:
        if self.shown:
            self._live.start()

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        self._live.stop()

    def begin(
        self,
        *,
        description: str,
        steps: int | None,
    ) -> None:
        self._line.begin(
            description=description,
            steps=steps,
        )

    def describe(
        self,
        *,
        description: str,
    ) -> None:
        self._line.describe(
            description=description,
        )

    def advance(
        self,
    ) -> None:
        self._line.advance()

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        self._line.stage_started(
            stage=stage,
            steps=steps,
        )

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        self._line.looked_up(
            lookup=lookup,
        )

    def dictionary_waiting(
        self,
        *,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        self._line.dictionary_waiting(
            retry_in_seconds=retry_in_seconds,
            patience_left_seconds=patience_left_seconds,
        )

    def processing(
        self,
        *,
        progress: ProcessingProgress,
    ) -> None:
        self._line.processing(
            progress=progress,
        )

    def watched(
        self,
        *,
        activity: DictionaryActivity,
    ) -> None:
        if self._baseline is None:
            self._baseline = activity

        # NOTE:
        # The view is drawn from another thread, so what it reads is replaced whole rather than changed in place.
        self._read = (
            activity,
            self._clock(),
        )

        self._unwatched = None

    def unwatched(
        self,
        *,
        reason: str,
    ) -> None:
        self._unwatched = reason

    def _render(
        self,
    ) -> RenderableType:
        return Group(
            self._line.line,
            self._panel(),
        )

    def _panel(
        self,
    ) -> RenderableType:
        read = self._read

        unwatched = self._unwatched

        if unwatched is not None or read is None:
            return _framed(
                Text(
                    text=unwatched or _READING,
                ),
                "dictionary",
                "dim",
                self._console.width,
            )

        (
            activity,
            read_at,
        ) = read

        # NOTE:
        # Between two reads, each wait goes on growing at the pace of the clock, so a bar never stands still and a
        # countdown counts down.
        since = self._clock() - read_at

        provider = activity.provider

        baseline = self._baseline or activity

        grid = Table.grid(
            padding=(
                0,
                2,
            ),
        )

        grid.add_column(
            style="dim",
            no_wrap=True,
        )

        grid.add_column()

        grid.add_row(
            "circuit",
            _circuit(
                provider,
            ),
        )

        # NOTE:
        # The next request stands apart from the circuit because a pause the provider asked for holds the trial too:
        # it is the later of the two, and the row is there only while something holds the requests back.
        next_try = _next_try(
            provider,
            since,
        )

        if next_try is not None:
            grid.add_row(
                "next try",
                next_try,
            )

        grid.add_row(
            "last minute",
            _last_minute(
                provider,
                since,
            ),
        )

        # NOTE:
        # How long ago a request was last answered says how long the provider has not answered the client, once that
        # is longer than the minute above shows.
        if provider.last_answer_seconds is not None and provider.last_answer_seconds + since >= _MINUTE:
            grid.add_row(
                "last answer",
                f"{
                    _ago(
                        provider.last_answer_seconds + since,
                    )
                } ago",
            )

        grid.add_row(
            "answered",
            _answered(
                activity,
                baseline,
            ),
        )

        # NOTE:
        # A word the dictionary turned away may be asked again and answered, so the row counts refusals, not words
        # left without a definition, which the report counts.
        grid.add_row(
            "turned away",
            _by_reason(
                activity.counts.unavailable,
                baseline.counts.unavailable,
            ),
        )

        grid.add_row(
            "retries",
            _by_reason(
                provider.retries,
                baseline.provider.retries,
            ),
        )

        grid.add_row(
            "budget",
            f"{provider.tokens:.1f} of {provider.burst} tokens, {provider.requests_per_second:g} a second",
        )

        grid.add_row(
            "in flight",
            _in_flight(
                provider.lookups,
                provider.timeout_seconds,
                since,
            ),
        )

        (
            health,
            color,
        ) = _health(
            provider,
            since,
        )

        return _framed(
            grid,
            f"dictionary: {health}",
            color,
            self._console.width,
        )


def _framed(
    renderable: RenderableType,
    title: str,
    color: str,
    width: int,
    /,
) -> Panel:
    # WARN:
    # The panel stops a column short of a narrower terminal's edge: a row that fills the last column makes some
    # terminals wrap before the line ends, so each redraw would start a line too low.
    return Panel(
        renderable=renderable,
        title=Text(
            text=title,
            style=f"bold {color}",
        ),
        title_align="left",
        border_style=color,
        padding=(
            0,
            1,
        ),
        width=max(
            min(
                width - 1,
                _PANEL_WIDTH,
            ),
            40,
        ),
    )


def _health(
    provider: ProviderActivity,
    since: float,
    /,
) -> tuple[str, str]:
    """
    How the provider has been answering the client, in a word and a color:
    failing while the circuit is not closed, rate-limited while a pause it
    asked for holds, failing while requests fail in a row, and answering
    otherwise.
    """

    if provider.circuit is not BreakerState.CLOSED:
        return (
            "failing",
            "red",
        )

    if (
        _left(
            provider.cooldown_seconds,
            since,
        )
        is not None
    ):
        return (
            "rate-limited",
            "yellow",
        )

    if provider.consecutive_failures:
        return (
            "failing",
            "yellow",
        )

    return (
        "answering",
        "green",
    )


def _left(
    seconds: float | None,
    since: float,
    /,
) -> float | None:
    """
    What is left of a wait of `seconds` at the last read, `since` seconds
    later, or `None` when there was none or it is over.
    """

    if seconds is None or seconds - since <= 0:
        return None

    return seconds - since


def _circuit(
    provider: ProviderActivity,
    /,
) -> Text:
    match provider.circuit:
        case BreakerState.CLOSED:
            count = provider.consecutive_failures

            threshold = provider.failure_threshold

            # NOTE:
            # The breaker's own count stands apart from the turned away below, which adds up every reason, the open
            # circuit's refusals included, and never starts again, so the two are never read as one number.
            if not count or threshold is None:
                return Text.from_markup(
                    text="[green]closed[/green]",
                )

            return Text.from_markup(
                text=(
                    f"[green]closed[/green], [yellow]{count} of {threshold} requests failed in a row[/yellow]; it "
                    f"opens at {threshold}"
                ),
            )

        case BreakerState.OPEN:
            # NOTE:
            # What opened the circuit, and which trial failed last, say whether the provider is newly down or still
            # down; a request already on its way when the circuit opened still gets its answer, but no lookup sends
            # another until the trial.
            if provider.failed_trials is None:
                return Text.from_markup(
                    text="[red]open[/red]",
                )

            return Text.from_markup(
                text=f"[red]open[/red] after {
                    _opening(
                        provider,
                    )
                }",
            )

        case BreakerState.HALF_OPEN:
            waiting = sum(1 for lookup in provider.lookups if lookup.waiting_for is WaitCause.TRIAL)

            trial = _trial(
                provider,
            )

            if any(lookup.trial for lookup in provider.lookups):
                # NOTE:
                # The trial is one request, and every other lookup waits for its answer.
                return Text.from_markup(
                    text=f"[yellow]half-open[/yellow]: {trial} is in flight, {waiting} {
                        _plural(
                            waiting,
                            'lookup waits',
                            'lookups wait',
                        )
                    } for it",
                )

            return Text.from_markup(
                text=f"[yellow]half-open[/yellow]: the next lookup is {trial}",
            )


def _trial(
    provider: ProviderActivity,
    /,
) -> str:
    if provider.failed_trials is None:
        return "the trial"

    return f"trial {provider.failed_trials + 1}"


def _opening(
    provider: ProviderActivity,
    /,
) -> str:
    cause = (
        f"{provider.failure_threshold or ''} failed requests".lstrip()
        if not provider.failed_trials
        else f"trial {provider.failed_trials} failed"
    )

    if provider.opened_by is None:
        return cause

    return f"{cause} ({
        _REASONS.get(
            provider.opened_by,
            provider.opened_by,
        )
    })"


def _next_try(
    provider: ProviderActivity,
    since: float,
    /,
) -> str | None:
    """
    When the client next sends a request, while the circuit or a pause the
    provider asked for holds its requests back, and `None` otherwise.
    """

    pause = _left(
        provider.cooldown_seconds,
        since,
    )

    held = (
        ""
        if pause is None
        else f"in {
            _seconds(
                pause,
            )
        }, when the provider's pause ends"
    )

    trial = _trial(
        provider,
    )

    match provider.circuit:
        case BreakerState.CLOSED:
            return held or None

        case BreakerState.OPEN:
            due = (
                _left(
                    provider.retry_in_seconds,
                    since,
                )
                or 0.0
            )

            if pause is not None and pause > due:
                return f"{trial} {held}"

            return f"{trial} in {
                _seconds(
                    due,
                )
            }"

        case BreakerState.HALF_OPEN:
            if held:
                return f"{trial} {held}"

            if any(lookup.trial for lookup in provider.lookups):
                return f"when {trial} is answered"

            return f"{trial}, with the next lookup"


def _last_minute(
    provider: ProviderActivity,
    since: float,
    /,
) -> Text:
    worst: list[RequestOutcome | None] = [
        None,
    ] * _MINUTE

    for request in provider.recent:
        age = request.seconds + since

        if age >= _MINUTE:
            continue

        whole = int(
            age,
        )

        slot = _MINUTE - 1 - whole

        held = worst[slot]

        if held is None or _MARKS[request.outcome][2] > _MARKS[held][2]:
            worst[slot] = request.outcome

    strip = Text()

    for outcome in worst:
        if outcome is None:
            strip.append(
                text="·",
                style="dim",
            )

        else:
            (
                mark,
                color,
                _,
            ) = _MARKS[outcome]

            strip.append(
                text=mark,
                style=color,
            )

    return strip


def _ago(
    seconds: float,
    /,
) -> str:
    (
        minutes,
        rest,
    ) = divmod(
        int(
            seconds,
        ),
        60,
    )

    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def _seconds(
    seconds: float,
    /,
) -> str:
    # NOTE:
    # Rounded up, as the learner's line rounds its times, so the two name the same second for the same moment.
    return f"{
        ceil(
            seconds,
        )
    } s"


def _answered(
    activity: DictionaryActivity,
    baseline: DictionaryActivity,
    /,
) -> str:
    counts = activity.counts

    before = baseline.counts

    cached = counts.cached - before.cached

    found = counts.found - before.found

    not_found = counts.not_found - before.not_found

    return f"{cached + found + not_found:,}: {cached:,} from the cache, {found:,} found, {not_found:,} not found"


def _by_reason[K: str](
    now: Mapping[K, int],
    before: Mapping[K, int],
    /,
) -> Text:
    grown = _grown(
        now,
        before,
    )

    if not grown:
        return Text(
            text="none",
            style="dim",
        )

    reasons = ", ".join(
        f"{count:,} {
            _REASONS.get(
                reason,
                reason,
            )
        }"
        for (
            reason,
            count,
        ) in sorted(
            grown.items(),
            key=_largest_first,
        )
    )

    total = sum(
        grown.values(),
    )

    return Text(
        text=f"{total:,}: {reasons}",
    )


def _largest_first[K: str](
    item: tuple[K, int],
    /,
) -> tuple[int, str]:
    (
        reason,
        count,
    ) = item

    # NOTE:
    # The reasons refuse to be ordered among themselves, so their names break a tie.
    return (
        -count,
        str(
            object=reason,
        ),
    )


def _grown[K](
    now: Mapping[K, int],
    before: Mapping[K, int],
    /,
) -> dict[K, int]:
    """
    How much each count in `now` has grown since `before`, for the counts that
    grew.
    """

    grown: dict[K, int] = {}

    for (
        key,
        count,
    ) in now.items():
        earlier = before.get(
            key,
            0,
        )

        if count > earlier:
            grown[key] = count - earlier

    return grown


def _in_flight(
    lookups: Sequence[LookupActivity],
    timeout_seconds: float,
    since: float,
    /,
) -> RenderableType:
    if not lookups:
        return Text(
            text="nothing",
            style="dim",
        )

    counts = ", ".join(
        f"{count} {label}"
        for (
            cause,
            label,
        ) in _WAITING.items()
        if (count := sum(1 for lookup in lookups if lookup.waiting_for is cause))
    )

    shown = tuple(lookup for lookup in lookups if lookup.waiting_for in _ON_A_ROW)

    if not shown:
        return Text(
            text=counts,
        )

    rows = Table.grid(
        padding=(
            0,
            1,
        ),
    )

    rows.add_column(
        width=_LEMMA_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )

    rows.add_column(
        no_wrap=True,
        overflow="ellipsis",
    )

    rows.add_column(
        width=_BAR_WIDTH,
    )

    rows.add_column(
        justify="right",
        no_wrap=True,
    )

    for lookup in shown[:_ROWS]:
        seconds = lookup.seconds + since

        rows.add_row(
            lookup.lemma,
            _doing(
                lookup,
            ),
            _bar(
                lookup,
                timeout_seconds,
                seconds,
            ),
            f"{seconds:.1f} s",
        )

    hidden = (
        len(
            shown,
        )
        - _ROWS
    )

    if hidden > 0:
        rows.add_row(
            Text(
                text=f"and {hidden} more",
                style="dim",
            ),
        )

    return Group(
        Text(
            text=counts,
        ),
        rows,
    )


def _doing(
    lookup: LookupActivity,
    /,
) -> str:
    if lookup.waiting_for is WaitCause.RETRY:
        reason = (
            _REASONS.get(
                lookup.reason,
                lookup.reason,
            )
            if lookup.reason is not None
            else "failed"
        )

        return f"attempt {lookup.attempt} in {lookup.delay_seconds or 0.0:.1f} s, {reason}"

    trial = ", the trial" if lookup.trial else ""

    return f"asking, attempt {lookup.attempt}{trial}"


def _bar(
    lookup: LookupActivity,
    timeout_seconds: float,
    seconds: float,
    /,
) -> RenderableType:
    total = timeout_seconds if lookup.waiting_for is WaitCause.PROVIDER else lookup.delay_seconds

    if not total:
        return Text()

    return ProgressBar(
        total=total,
        completed=min(
            seconds,
            total,
        ),
        width=_BAR_WIDTH,
    )


def _plural(
    count: int,
    one: str,
    many: str,
    /,
) -> str:
    return one if count == 1 else many
