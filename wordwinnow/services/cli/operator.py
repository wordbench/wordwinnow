"""
What the command line shows an operator while an analysis runs: the learner's
line, and under it what the dictionary is doing.

Under the line stand the circuit, the request budget, how the analysis's
lookups have been answered so far, the ones left unanswered and the retries,
and each lookup in flight with what it is waiting for: a request has a bar
against its timeout, and a pause before a retry a bar against the pause's
length.

Like the learner's line, the view is drawn on standard error, only at an
interactive terminal, and disappears when the analysis ends.
"""

from collections.abc import (
    Mapping,
    Sequence,
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
from rich.padding import (
    Padding,
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
# The most lookups in flight drawn one to a row: the eight the defaults allow at once, and room for a second worker's
# at the enrichment service.
_ROWS: Final = 12

# NOTE:
# The widths that keep a row of a lookup in flight, its longest description included, within 110 columns.
_LEMMA_WIDTH: Final = 14

_BAR_WIDTH: Final = 16

# NOTE:
# What each reason a lookup went unanswered for, or was asked again for, is called in the view.
_REASONS: Final[MappingProxyType[str, str]] = MappingProxyType(
    mapping={
        FailureReason.CIRCUIT_OPEN: "circuit open",
        FailureReason.TIMED_OUT: "timed out",
        FailureReason.TRANSPORT_ERROR: "connection failed",
        FailureReason.RATE_LIMITED: "rate limited",
        FailureReason.PROVIDER_ERROR: "server error",
        FailureReason.INVALID_RESPONSE: "unreadable answer",
    },
)

_READING: Final = "reading what the dictionary is doing"


@final
class OperatorProgress:
    """
    The learner's line and, under it, what the dictionary is doing.

    Satisfies `AnalysisProgress`, `RemoteAnalysisProgress`, and
    `DictionaryWatch` structurally, and shows nothing unless its console is an
    interactive terminal.

    The counts are the analysis's own: the first activity it hears is what
    every later one is counted from.
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
            # WARN:
            # Padding widens every row to the terminal's width unless told not to, and a row that fills the last
            # column makes some terminals wrap before the line ends, so each redraw starts a line too low.
            Padding(
                renderable=self._mechanics(),
                pad=(
                    0,
                    0,
                    0,
                    2,
                ),
                expand=False,
            ),
        )

    def _mechanics(
        self,
    ) -> RenderableType:
        grid = Table.grid(
            padding=(
                0,
                2,
            ),
        )

        grid.add_column(
            style="dim",
        )

        grid.add_column()

        read = self._read

        unwatched = self._unwatched

        if unwatched is not None or read is None:
            grid.add_row(
                "dictionary",
                unwatched or _READING,
            )

            return grid

        (
            activity,
            read_at,
        ) = read

        # NOTE:
        # Between two reads, each wait goes on growing at the pace of the clock, so a bar never stands still.
        since = self._clock() - read_at

        provider = activity.provider

        grid.add_row(
            "circuit",
            _circuit(
                provider,
                since,
            ),
        )

        grid.add_row(
            "budget",
            _budget(
                provider,
            ),
        )

        baseline = self._baseline or activity

        grid.add_row(
            "answered",
            _answered(
                activity,
                baseline,
            ),
        )

        grid.add_row(
            "unavailable",
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
            "in flight",
            _in_flight(
                provider.lookups,
                provider.timeout_seconds,
                since,
            ),
        )

        return grid


def _circuit(
    provider: ProviderActivity,
    since: float,
    /,
) -> Text:
    match provider.circuit:
        case BreakerState.CLOSED:
            return Text.from_markup(
                text="[green]closed[/green]: lookups ask the provider",
            )

        case BreakerState.OPEN:
            remaining = max(
                0.0,
                (provider.retry_in_seconds or 0.0) - since,
            )

            # NOTE:
            # A lookup the circuit admitted before it opened still makes its remaining attempts, as the rows under it
            # show, so only a new lookup is kept from the provider.
            return Text.from_markup(
                text=f"[red]open[/red]: no new lookup asks the provider for {remaining:.0f} s more",
            )

        case BreakerState.HALF_OPEN:
            waiting = sum(1 for lookup in provider.lookups if lookup.waiting_for is WaitCause.TRIAL)

            if any(lookup.trial for lookup in provider.lookups):
                # NOTE:
                # The trial is a lookup, which may take all its attempts, and the others wait for its outcome rather
                # than for one request's answer.
                return Text.from_markup(
                    text=f"[yellow]half-open[/yellow]: one lookup is the trial, and {waiting} {
                        _plural(
                            waiting,
                            'lookup waits',
                            'lookups wait',
                        )
                    } for its outcome",
                )

            return Text.from_markup(
                text="[yellow]half-open[/yellow]: the next lookup tries the provider",
            )


def _budget(
    provider: ProviderActivity,
    /,
) -> str:
    waiting = provider.waiting_for_budget

    queue = (
        f"; {waiting} {
            _plural(
                waiting,
                'lookup',
                'lookups',
            )
        } waiting for one"
        if waiting
        else ""
    )

    return (
        f"{provider.tokens:.1f} of {provider.burst} tokens, refilled at {provider.requests_per_second:g} a second"
        f"{queue}"
    )


def _answered(
    activity: DictionaryActivity,
    baseline: DictionaryActivity,
    /,
) -> str:
    counts = activity.counts

    before = baseline.counts

    return (
        f"{counts.cached - before.cached} from the cache, {counts.found - before.found} found and "
        f"{counts.not_found - before.not_found} not found by the provider"
    )


def _by_reason[K: str](
    now: Mapping[K, int],
    before: Mapping[K, int],
    /,
) -> str:
    grown = _grown(
        now,
        before,
    )

    if not grown:
        return "none"

    reasons = ", ".join(
        f"{count} {
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

    return f"{
        sum(
            grown.values(),
        )
    } ({reasons})"


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

    rows = Table.grid(
        padding=(
            0,
            2,
        ),
    )

    rows.add_column(
        width=_LEMMA_WIDTH,
        no_wrap=True,
        overflow="ellipsis",
    )

    rows.add_column(
        no_wrap=True,
    )

    rows.add_column(
        width=_BAR_WIDTH,
    )

    rows.add_column(
        justify="right",
        no_wrap=True,
    )

    for lookup in lookups[:_ROWS]:
        seconds = lookup.seconds + since

        rows.add_row(
            lookup.lemma,
            _waiting_for(
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
            lookups,
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

    return rows


def _waiting_for(
    lookup: LookupActivity,
    /,
) -> str:
    match lookup.waiting_for:
        case WaitCause.TRIAL:
            return "waiting for the trial's outcome"

        case WaitCause.BUDGET:
            return "waiting for a token"

        case WaitCause.PROVIDER:
            trial = ", the trial" if lookup.trial else ""

            return f"asking the provider, attempt {lookup.attempt}{trial}"

        case WaitCause.RETRY:
            reason = (
                _REASONS.get(
                    lookup.reason,
                    lookup.reason,
                )
                if lookup.reason is not None
                else "failed"
            )

            return f"pausing {lookup.delay_seconds or 0.0:.1f} s before attempt {lookup.attempt} ({reason})"


def _bar(
    lookup: LookupActivity,
    timeout_seconds: float,
    seconds: float,
    /,
) -> RenderableType:
    total = (
        timeout_seconds
        if lookup.waiting_for is WaitCause.PROVIDER
        else lookup.delay_seconds
        if lookup.waiting_for is WaitCause.RETRY
        else None
    )

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
