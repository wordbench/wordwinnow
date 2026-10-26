"""
What the command line shows on standard error while a long command works.

One line, and only on an interactive terminal: what the command is doing, a
bar and a count when the size of the work is known, and how long the current
phase has taken.

The times the line names are counted from the moment it is drawn, so they
count down between two reports.

An analysis a worker processes elsewhere shows the same line, from what the
worker last reported.

The line disappears when the work ends, so a report, JSON, or CSV on standard
output is never touched, and a pipe, a file, or a test receives nothing.
"""

from collections.abc import (
    Callable,
)
from datetime import (
    datetime,
    timedelta,
)
from math import (
    ceil,
)
from types import (
    MappingProxyType,
    TracebackType,
)
from typing import (
    Final,
    Self,
    TypedDict,
    final,
)

from rich.console import (
    Console,
)
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
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
    LookupOutcome,
)
from wordwinnow.services.cli.clock_column import (
    ClockColumn,
)
from wordwinnow.services.progress import (
    paused_after,
)

# NOTE:
# What each stage of the pipeline is called while it runs.
STAGE_DESCRIPTIONS: Final = MappingProxyType(
    mapping={
        Stage.LINGUISTIC_ANALYSIS: "splitting, tagging, and lemmatizing the text",
        Stage.VOCABULARY_SELECTION: "collecting the vocabulary",
        Stage.LEXICAL_SEMANTICS: "reading each word's senses in WordNet",
        Stage.LEVEL_ASSESSMENT: "giving each word a level",
        Stage.DICTIONARY_ENRICHMENT: "looking the words up in the dictionary",
        Stage.WINNOWING: "placing the words in tiers",
    },
)

# NOTE:
# What the dictionary stage says while the client's circuit breaker keeps the provider from being asked: the lookups
# end at once as unavailable, and the report shows WordNet's gloss for each, until a trial request is answered.
PAUSED_DICTIONARY: Final = "the dictionary is not answering; WordNet stands in"

# NOTE:
# What the dictionary stage says while its lookups wait for the provider to answer again: when the next of them asks,
# and the time within which WordNet stands in for every word still waiting, both known to the second, because the
# analysis lets no waiting word ask past its waiting time.
WAITING_DICTIONARY: Final = "waiting for the dictionary: next try {retry}, WordNet {give_up}"


@final
class _LineFields(
    TypedDict,
):
    """
    What the line's columns read besides the description: the count and the
    moments a wait ends.
    """

    count: str

    waiting: tuple[datetime, datetime] | None


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _count(
    done: int,
    steps: int | None,
    unavailable: int,
    /,
) -> str:
    if steps is None:
        return ""

    count = f"{done:,} of {steps:,}"

    if unavailable:
        count = f"{count}, {unavailable:,} unavailable"

    return count


def _duration(
    seconds: int,
    /,
) -> str:
    (
        minutes,
        rest,
    ) = divmod(
        seconds,
        60,
    )

    if not minutes:
        return f"{rest} s"

    if not rest:
        return f"{minutes} min"

    return f"{minutes} min {rest} s"


def _in(
    moment: datetime,
    now: datetime,
    /,
) -> str:
    seconds = ceil(
        (moment - now).total_seconds(),
    )

    return (
        "now"
        if seconds <= 0
        else f"in {
            _duration(
                seconds,
            )
        }"
    )


def _within(
    moment: datetime,
    now: datetime,
    /,
) -> str:
    seconds = ceil(
        (moment - now).total_seconds(),
    )

    return (
        "now"
        if seconds <= 0
        else f"within {
            _duration(
                seconds,
            )
        }"
    )


def _description(
    task: Task,
    now: datetime,
    /,
) -> Text:
    """
    What the work is doing, with the times of a wait counted from `now`.
    """

    waiting = task.fields.get(
        "waiting",
    )

    if waiting is None:
        return Text(
            text=task.description,
        )

    (
        retry,
        give_up,
    ) = waiting

    return Text(
        text=WAITING_DICTIONARY.format(
            retry=_in(
                retry,
                now,
            ),
            give_up=_within(
                give_up,
                now,
            ),
        ),
    )


@final
class TerminalProgress:
    """
    One line on standard error saying what a command is doing.

    Satisfies `AnalysisProgress` and `RemoteAnalysisProgress` structurally,
    and shows nothing unless its console is an interactive terminal.
    """

    def __init__(
        self,
        *,
        console: Console,
        refresh_per_second: float = 10,
        clock: Callable[[], datetime] = _local_now,
    ) -> None:
        # NOTE:
        # No percentage and no time remaining: the steps of one phase can differ a hundredfold in length, as the level
        # experiment's candidates do, so a count and the time spent are the only figures that are always true.
        self._progress: Final = Progress(
            SpinnerColumn(),
            ClockColumn(
                clock=clock,
                draw=_description,
            ),
            BarColumn(
                bar_width=24,
            ),
            TextColumn(
                text_format="{task.fields[count]}",
            ),
            TimeElapsedColumn(),
            console=console,
            refresh_per_second=refresh_per_second,
            transient=True,
            disable=not console.is_terminal or console.is_dumb_terminal,
        )

        self._task: TaskID | None = None

        self._stage: Stage | None = None

        self._done = 0

        self._steps: int | None = None

        self._unavailable = 0

        self._paused = False

        self._clock: Final = clock

        self._waiting_until: tuple[datetime, datetime] | None = None

    @property
    def shown(
        self,
    ) -> bool:
        """
        Whether a person sees this progress at all.
        """

        return not self._progress.disable

    @property
    def line(
        self,
    ) -> Progress:
        """
        The line itself, for a view that draws it above more and so never
        enters this one.
        """

        return self._progress

    def __enter__(
        self,
    ) -> Self:
        self._progress.start()

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        self._progress.stop()

    def begin(
        self,
        *,
        description: str,
        steps: int | None,
    ) -> None:
        self._stage = None

        self._done = 0

        self._steps = steps

        self._unavailable = 0

        self._paused = False

        self._waiting_until = None

        if self._task is None:
            self._task = self._progress.add_task(
                description=description,
                total=steps,
                **self._fields(),
            )

            return

        self._progress.reset(
            task_id=self._task,
            total=steps,
            description=description,
            **self._fields(),
        )

    def describe(
        self,
        *,
        description: str,
    ) -> None:
        if self._task is None:
            self.begin(
                description=description,
                steps=None,
            )

            return

        self._progress.update(
            task_id=self._task,
            description=description,
        )

    def advance(
        self,
    ) -> None:
        if self._task is None:
            return

        self._done += 1

        self._progress.update(
            task_id=self._task,
            advance=1,
            **self._fields(),
        )

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        self.begin(
            description=STAGE_DESCRIPTIONS[stage],
            steps=steps,
        )

        self._stage = stage

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        if self._task is None:
            return

        self._done += 1

        if lookup.outcome is LookupOutcome.UNAVAILABLE:
            self._unavailable += 1

        paused = paused_after(
            paused=self._paused,
            lookup=lookup,
        )

        changed = paused is not self._paused

        self._paused = paused

        # NOTE:
        # A change of state is drawn at once rather than at the next tick, so it is never lost between two lookups.
        self._progress.update(
            task_id=self._task,
            advance=1,
            description=self._description(
                Stage.DICTIONARY_ENRICHMENT,
            ),
            refresh=changed,
            **self._fields(),
        )

    def dictionary_waiting(
        self,
        *,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        if self._task is None:
            return

        self._waiting_until = _until(
            self._clock(),
            retry_in_seconds,
            patience_left_seconds,
        )

        self._progress.update(
            task_id=self._task,
            description=self._description(
                Stage.DICTIONARY_ENRICHMENT,
            ),
            refresh=True,
            **self._fields(),
        )

    def processing(
        self,
        *,
        progress: ProcessingProgress,
    ) -> None:
        if progress.stage is not self._stage:
            self.stage_started(
                stage=progress.stage,
                steps=progress.steps,
            )

        if self._task is None:
            return

        self._done = progress.done

        self._unavailable = progress.unavailable

        self._paused = progress.dictionary_paused

        self._waiting_until = _until(
            progress.reported_at.astimezone(
                tz=self._clock().tzinfo,
            ),
            progress.dictionary_retry_in_seconds,
            progress.dictionary_patience_left_seconds or 0.0,
        )

        # NOTE:
        # A report arrives once per poll rather than once per word, so each one is drawn at once instead of at the
        # next tick.
        self._progress.update(
            task_id=self._task,
            completed=progress.done,
            description=self._description(
                progress.stage,
            ),
            refresh=True,
            **self._fields(),
        )

    def _fields(
        self,
    ) -> _LineFields:
        """
        What the line's columns read besides the description, as it stands.
        """

        return _LineFields(
            count=_count(
                self._done,
                self._steps,
                self._unavailable,
            ),
            waiting=self._waiting_until,
        )

    def _description(
        self,
        stage: Stage,
        /,
    ) -> str:
        # NOTE:
        # A wait is drawn by its own column, from the moments it ends, so the description says only the stage or the
        # pause.
        if self._paused:
            return PAUSED_DICTIONARY

        return STAGE_DESCRIPTIONS[stage]


def _until(
    now: datetime,
    retry_in_seconds: float | None,
    patience_left_seconds: float,
    /,
) -> tuple[datetime, datetime] | None:
    if retry_in_seconds is None:
        return None

    return (
        now
        + timedelta(
            seconds=retry_in_seconds,
        ),
        now
        + timedelta(
            seconds=patience_left_seconds,
        ),
    )
