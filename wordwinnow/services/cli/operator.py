"""
What the command line shows an operator while an analysis runs: the learner's
line, and under it what the dictionary is doing.

Under the line stands one panel that draws the dictionary client as one loop.

Its title says how the provider has been answering, in one of eight states.

The loop shows how a word travels: through the breaker's switch, past the
bucket that hands out tokens, to the provider, and where the words wait on the
way.

Under the wire stand the words at the client, each on a row of its own that it
keeps while it is there, the last minute, and one sentence that says why the
state is what it is.

Every part is what the client saw of its own requests or what it decided; none
says what the provider did otherwise, which the client cannot see.

Like the learner's line, the view is drawn on standard error and only at an
interactive terminal.

When the analysis ends, the learner's line disappears, and the view leaves the
panel's last drawing above what the command prints next, unless the command
ends by an exception, when it leaves nothing.
"""

from collections.abc import (
    Callable,
    Sequence,
)
from datetime import (
    datetime,
)
from time import (
    monotonic,
)
from types import (
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
from rich.text import (
    Text,
)

from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.application.ports.pipeline_progress import (
    HeldWords,
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
from wordwinnow.infrastructure.dictionary.resilience import (
    MonotonicClock,
)
from wordwinnow.services.cli.operator_frame import (
    PANEL_LEAST,
    PANEL_WIDEST,
    loop_panel,
)
from wordwinnow.services.cli.operator_seen import (
    Keeper,
)
from wordwinnow.services.cli.progress import (
    TerminalProgress,
    local_now,
)

_READING: Final = "reading what the dictionary is doing"


@final
class OperatorProgress:
    """
    The learner's line and, under it, what the dictionary is doing.

    Satisfies `AnalysisProgress`, `RemoteAnalysisProgress`, and
    `DictionaryWatch` structurally, and shows nothing unless its console is an
    interactive terminal.

    In the local mode the tally counts the dictionary's lookups since the
    first activity the view hears, the analysis's own while the dictionary
    serves no other.

    In the distributed mode the counts that are this analysis's come from the
    progress its worker stores, and the activity is the service's.
    """

    def __init__(
        self,
        *,
        console: Console,
        refresh_per_second: float = 10,
        clock: MonotonicClock = monotonic,
        wall_clock: Callable[[], datetime] = local_now,
    ) -> None:
        # NOTE:
        # The line is drawn without hue under this view, because over a panel in which green, yellow, red, and magenta
        # each mean one thing, the line's stock pink, yellow, and green would say something false.
        self._line: Final = TerminalProgress(
            console=console,
            hue=False,
        )

        self._console: Final = console

        self._clock: Final = clock

        self._wall_clock: Final = wall_clock

        self._keeper: Final = Keeper()

        self._unwatched: str | None = None

        self._ended = False

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
        # NOTE:
        # A command that returns leaves the panel's last drawing, the completed count among it, drawn for the
        # terminal's rows without the line, whose last report can be a word short of the count, while one that ends by
        # an exception, the interrupt included, erases it all, so that no panel stands above a traceback or an error's
        # line.
        if exc_type is None:
            # NOTE:
            # The state the command ends with is drawn once as it stands before the line goes, as the display draws it
            # once more as it stops, so that a view stopped between two ticks has still drawn the line it ends with.
            if self.shown:
                self._live.refresh()

            self._ended = True

            self._live.transient = False

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

        self._keeper.stage_started(
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

        self._keeper.looked_up(
            lookup=lookup,
            now=self._clock(),
        )

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        self._line.dictionary_waiting(
            held=held,
            ending=ending,
            retry_in_seconds=retry_in_seconds,
            patience_left_seconds=patience_left_seconds,
        )

        self._keeper.dictionary_waiting(
            held=held,
            retry_in_seconds=retry_in_seconds,
            now=self._clock(),
        )

    def processing(
        self,
        *,
        progress: ProcessingProgress,
    ) -> None:
        self._line.processing(
            progress=progress,
        )

        # NOTE:
        # The worker writes its progress only when something changed, so one report is heard at every poll, and the
        # wait it names runs from when it was written, as the learner's line counts it, never from when it was heard.
        # A worker's clock ahead of the command's makes the age negative, and a report is no younger than its own
        # moment.
        age = max(
            (self._wall_clock() - progress.reported_at).total_seconds(),
            0.0,
        )

        self._keeper.processing(
            progress=progress,
            now=self._clock(),
            age=age,
        )

    def completed(
        self,
    ) -> None:
        self._keeper.completed()

        # NOTE:
        # The view stops when the command returns, which can be before the next tick draws what the analysis ended
        # with, so the completed state is drawn once here.
        if self.shown:
            self._live.refresh()

    def watched(
        self,
        *,
        activity: DictionaryActivity,
    ) -> None:
        # NOTE:
        # The view is drawn from another thread, so what it reads is replaced whole rather than changed in place.
        self._keeper.watched(
            activity=activity,
            now=self._clock(),
        )

        self._unwatched = None

    def unwatched(
        self,
        *,
        reason: str,
    ) -> None:
        self._unwatched = reason

    def panel(
        self,
    ) -> RenderableType:
        """
        The panel under the line as it stands now.

        Drawing reads what the view keeps and never changes it.
        """

        seen = self._keeper.seen

        unwatched = self._unwatched

        if unwatched is not None or seen is None:
            return _framed(
                Text(
                    text=unwatched or _READING,
                ),
                "dictionary",
                "dim",
                self._console.width,
            )

        # NOTE:
        # The learner's line takes one row where its parts fit, two where they do not, three where the counts and the
        # time spent do not fit side by side, and more only where a part is wider than its row, so the rows it takes
        # are measured as it is drawn, and the loop folds to what the terminal leaves; once the command has returned
        # the line is gone and takes none.
        line_rows = (
            0
            if self._ended
            else len(
                self._console.render_lines(
                    renderable=self._line.line,
                    options=self._console.options,
                    pad=False,
                ),
            )
        )

        return loop_panel(
            seen=seen,
            now=self._clock(),
            columns=self._console.width,
            rows=self._console.height,
            line_rows=line_rows,
            ascii_only=self._console.options.ascii_only,
        )

    def _render(
        self,
    ) -> RenderableType:
        if self._ended:
            return self.panel()

        return Group(
            self._line.line,
            self.panel(),
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
        width=max(
            min(
                width - 1,
                PANEL_WIDEST,
            ),
            PANEL_LEAST,
        ),
        padding=(
            0,
            1,
        ),
    )
