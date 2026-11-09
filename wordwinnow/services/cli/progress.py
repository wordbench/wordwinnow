"""
What the command line shows on standard error while a long command works.

One line, and only on an interactive terminal: what the command is doing, a
count when the size of the work is known, a bar beside it while the line says
what the stage does, and how long the current phase has taken.

The times the line names are counted from the moment it is drawn, so they
count down between two reports.

An analysis a worker processes elsewhere shows the same line, from what the
worker last reported.

The line disappears when the work ends, so a report, JSON, or CSV on standard
output is never touched, and a pipe, a file, or a test receives nothing.
"""

from collections import (
    deque,
)
from collections.abc import (
    Callable,
    Sequence,
)
from dataclasses import (
    dataclass,
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
    RenderableType,
)
from rich.progress import (
    Progress,
    ProgressBar,
    SpinnerColumn,
    Task,
    TaskID,
    TimeElapsedColumn,
)
from rich.text import (
    Text,
)

from wordwinnow.application.dto import (
    DictionaryWait,
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
    LookupOutcome,
)
from wordwinnow.services.cli.clock_column import (
    ClockColumn,
)
from wordwinnow.services.progress import (
    dictionary_wait_of,
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
# What the dictionary stage says once the client's circuit breaker keeps the provider from being asked and the waiting
# time could not cover the next check: the lookups end at once as unavailable, and the report shows WordNet's meaning
# for each, until a trial request is answered.
PAUSED_DICTIONARY: Final = "the dictionary is not answering; WordNet's meaning stands in"

# NOTE:
# What the dictionary stage says, in a report written before the stage named what its words wait for, while its
# lookups wait for the provider to answer again: when the next of them asks, and the time within which WordNet stands
# in for every word still waiting.
#
# Only such a report draws it, so a command line reading an older worker keeps drawing what that worker's fields mean.
WAITING_DICTIONARY: Final = "waiting for the dictionary: next try {retry}, WordNet {give_up}"

# NOTE:
# What the dictionary stage says while the dictionary does not answer or asked the client to pause, in the report's
# own words: the dictionary does not answer, and WordNet's meaning is what stands in.
NOT_ANSWERING_DICTIONARY: Final = "the dictionary is not answering"

ASKED_TO_PAUSE_DICTIONARY: Final = "paused, as the dictionary asked"

# NOTE:
# How long the dictionary stage may still take, at the pace of its last minute, and nothing while the provider is
# paused or any word waits to be asked again, or before the minute holds enough answers to say anything; the minute
# starts again after every pause and wait.
#
# One time, rounded as coarsely as such an estimate errs: simulated stages whose pace held were within a few per cent,
# while a stage whose share of cached words changed, or which another analysis began to share the provider with, was
# off by a sixth to two fifths; a range from the pace of each half of the minute would be narrower than those errors
# while the pace held, and would run from seconds to minutes after a pause.
_ESTIMATE_WINDOW: Final = timedelta(
    seconds=60,
)

_ESTIMATE_LEAST_SPAN: Final = timedelta(
    seconds=10,
)

_ESTIMATE_LEAST_ANSWERS: Final = 10

# NOTE:
# How long the dictionary stage may go without an answer before the line says so in place of the estimate: three of
# its recent gaps between answers, which a steady pace passes about once in twenty gaps, and five seconds at the
# least.
_QUIET_GAPS: Final = 3

_LEAST_QUIET: Final = timedelta(
    seconds=5,
)

_BAR_WIDTH: Final = 24


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class _DictionaryLine:
    """
    What the line says of the words that wait, with the moments its times are
    counted down to: the wait, how many words, the next check or ask, and the
    end of the time WordNet's meaning stands in within.
    """

    wait: DictionaryWait

    words: int

    next_at: datetime | None

    within_at: datetime | None


@final
class _LineFields(
    TypedDict,
):
    """
    What the line's columns read besides the description: the count, the
    finishing time the estimate gives, the moments a wait ends in a report
    written before the words' waits were named, what the line says of the
    words that wait, whether it draws the bar, since when the stage has had no
    answer, and how long a quiet may last before the line says so.
    """

    count: str

    finish: datetime | None

    waiting: tuple[datetime, datetime] | None

    dictionary: _DictionaryLine | None

    bar: bool

    quiet_since: datetime | None

    quiet_after: timedelta


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


def _about(
    seconds: float,
    /,
) -> str:
    # NOTE:
    # Five seconds under a minute, ten under two, half a minute under ten, and a minute beyond: steps of a sixth to a
    # tenth of the time, as coarse as the estimate's own error.
    if seconds < 10:
        return "a few seconds"

    step = 5 if seconds < 60 else 10 if seconds < 120 else 30 if seconds < 600 else 60

    return _duration(
        round(
            number=seconds / step,
        )
        * step,
    )


def _time_left(
    finish: datetime,
    now: datetime,
    /,
) -> str:
    left = _about(
        max(
            0.0,
            (finish - now).total_seconds(),
        ),
    )

    return "a few seconds left" if left == "a few seconds" else f"about {left} left"


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


def _said(
    line: _DictionaryLine,
    now: datetime,
    /,
) -> str | None:
    """
    What the description says of the words that wait, or `None` when it says
    what the stage does.
    """

    if line.wait is DictionaryWait.WAITING_WORDS and line.next_at is not None:
        words = "1 word waits" if line.words == 1 else f"{line.words:,} words wait"

        return f"{words} to be asked again; next {
            _in(
                line.next_at,
                now,
            )
        }"

    if line.wait is DictionaryWait.NOT_ANSWERING:
        return (
            NOT_ANSWERING_DICTIONARY
            if line.next_at is None
            else f"{NOT_ANSWERING_DICTIONARY}; next check {
                _in(
                    line.next_at,
                    now,
                )
            }"
        )

    if line.wait is DictionaryWait.ASKED_TO_PAUSE:
        return (
            ASKED_TO_PAUSE_DICTIONARY
            if line.next_at is None
            else f"{ASKED_TO_PAUSE_DICTIONARY}; resumes {
                _in(
                    line.next_at,
                    now,
                )
            }"
        )

    return None


def _description(
    task: Task,
    now: datetime,
    /,
) -> Text:
    """
    What the work is doing, with the times of a wait counted from `now`.
    """

    line = task.fields.get(
        "dictionary",
    )

    if line is not None:
        said = _said(
            line,
            now,
        )

        if said is not None:
            return Text(
                text=said,
            )

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


def _bar(
    task: Task,
    now: datetime,
    /,
) -> RenderableType:
    """
    The bar of the stage's count, or nothing while the line says what words
    wait for, so that the words have the room.
    """

    if not task.fields.get(
        "bar",
        True,
    ):
        return Text(
            text="",
        )

    # NOTE:
    # Built as `BarColumn.render` builds it with the width this line gives it, so the line draws what it drew with
    # that column, and a `Text` with nothing in it takes no width of the line.
    return ProgressBar(
        total=(
            max(
                0,
                task.total,
            )
            if task.total is not None
            else None
        ),
        completed=max(
            0,
            task.completed,
        ),
        width=_BAR_WIDTH,
        pulse=not task.started,
        animation_time=task.get_time(),
    )


def _quiet(
    task: Task,
    now: datetime,
    /,
) -> int | None:
    """
    For how many seconds the stage has had no answer, once that is longer than
    a quiet may last, or `None`.
    """

    quiet_since = task.fields.get(
        "quiet_since",
    )

    if quiet_since is None or now - quiet_since < task.fields["quiet_after"]:
        return None

    return int(
        (now - quiet_since).total_seconds(),
    )


def _count_and_time(
    task: Task,
    now: datetime,
    /,
) -> Text:
    """
    The count, and what the line adds to it: how many words wait and for how
    long WordNet's meaning stands in, or how long the stage may still take or
    how long it has gone without an answer, counted from `now`.
    """

    count = task.fields["count"]

    line = task.fields.get(
        "dictionary",
    )

    if line is not None and line.wait is DictionaryWait.SOME_WORDS:
        waiting = f"{count}, {line.words:,} waiting" if count else f"{line.words:,} waiting"

        quiet = _quiet(
            task,
            now,
        )

        return Text(
            text=waiting if quiet is None else f"{waiting}, no answer for {quiet} s",
        )

    if line is not None:
        return Text(
            text=_bounded(
                line,
                now,
            ),
        )

    quiet = _quiet(
        task,
        now,
    )

    if quiet is not None:
        return Text(
            text=f"{count}, no answer for {quiet} s",
        )

    finish = task.fields.get(
        "finish",
    )

    if finish is None:
        return Text(
            text=count,
        )

    return Text(
        text=f"{count}, {
            _time_left(
                finish,
                now,
            )
        }",
    )


def _bounded(
    line: _DictionaryLine,
    now: datetime,
    /,
) -> str:
    """
    How many words wait, when the line counts them, and for how long WordNet's
    meaning stands in for them.
    """

    if line.within_at is None:
        return ""

    bound = f"WordNet's meaning {
        _within(
            line.within_at,
            now,
        )
    }"

    if line.wait is DictionaryWait.WAITING_WORDS:
        return bound

    return f"{line.words:,} waiting, {bound}"


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
        # No percentage, and a time left only for the dictionary stage: the steps of other phases can differ a
        # hundredfold in length, as the level experiment's candidates do, so a count and the time spent are the only
        # figures always true there, while the dictionary stage keeps a pace the provider's budget and the cache set,
        # so its recent pace says when it may finish.
        self._progress: Final = Progress(
            SpinnerColumn(),
            ClockColumn(
                clock=clock,
                draw=_description,
            ),
            ClockColumn(
                clock=clock,
                draw=_bar,
            ),
            ClockColumn(
                clock=clock,
                draw=_count_and_time,
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

        self._held: Sequence[HeldWords] = ()

        self._held_at: datetime | None = None

        self._patience_left = 0.0

        # NOTE:
        # Whether a worker's report draws the line, and, then, what it says of the words that wait: nothing when the
        # report says no wait or was written before it said what words wait for.
        self._stored = False

        self._reported: _DictionaryLine | None = None

        self._legacy = False

        self._drawn: tuple[object, ...] = ()

        self._answers: Final[deque[tuple[datetime, int]]] = deque()

        # NOTE:
        # Since when the dictionary stage has had no answer: its start, its last answer, or the end of a wait.
        self._quiet_since: datetime | None = None

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

        self._held = ()

        self._held_at = None

        self._stored = False

        self._reported = None

        self._legacy = False

        self._answers.clear()

        self._quiet_since = None

        # NOTE:
        # Each phase is a task of its own, because Rich's reset keeps a task's total when it is given None: a phase
        # whose steps are unknown, begun after a counted one, would draw that phase's total as an empty bar rather
        # than the pulse that says the work goes on.
        if self._task is not None:
            self._progress.remove_task(
                task_id=self._task,
            )

        self._task = self._progress.add_task(
            description=description,
            total=steps,
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

        if stage is not Stage.DICTIONARY_ENRICHMENT or self._task is None:
            return

        self._quiet_since = self._clock()

        self._progress.update(
            task_id=self._task,
            **self._fields(),
        )

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

        self._paused = paused_after(
            paused=self._paused,
            lookup=lookup,
        )

        now = self._clock()

        self._quiet_since = now

        self._answered(
            now,
        )

        before = self._drawn

        fields = self._fields()

        # NOTE:
        # A change of what the line says is drawn at once rather than at the next tick, so it is never lost between
        # two lookups.
        #
        # The words a word's end leaves held were reported just before this call, and nothing was drawn from them, so
        # what is drawn here is the state the end leaves and never the state between the two.
        self._progress.update(
            task_id=self._task,
            advance=1,
            description=self._description(
                Stage.DICTIONARY_ENRICHMENT,
            ),
            refresh=self._drawn != before,
            **fields,
        )

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        if self._task is None:
            return

        now = self._clock()

        self._held = held

        self._held_at = now

        self._patience_left = patience_left_seconds

        self._waiting_until = _until(
            now,
            retry_in_seconds,
            patience_left_seconds,
        )

        if self._waiting_until is not None:
            self._answers.clear()

        # NOTE:
        # A wait explains its own quiet, so the quiet is counted again from its end.
        self._quiet_since = now

        # NOTE:
        # A word that leaves is drawn by the `looked_up` that follows it at once, with the count and the dictionary's
        # state it brings, and a line drawn between the two would say a state that no moment of the stage had.
        if ending:
            return

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

        reported_at = progress.reported_at.astimezone(
            tz=self._clock().tzinfo,
        )

        waiting_until = _until(
            reported_at,
            progress.dictionary_retry_in_seconds,
            progress.dictionary_patience_left_seconds or 0.0,
        )

        # NOTE:
        # A report says when it was written, so an answer, or the start or end of a wait, dates the quiet from then.
        if progress.done > self._done or (waiting_until is None) is not (self._waiting_until is None):
            self._quiet_since = reported_at

        self._done = progress.done

        self._unavailable = progress.unavailable

        self._paused = progress.dictionary_paused

        self._waiting_until = waiting_until

        self._stored = True

        # NOTE:
        # A report written before the line named what words wait for says so by having no count of them, and draws as
        # the report it was: the stage, the pause, or the old waiting text.
        self._legacy = progress.dictionary_waiting_words is None

        self._reported = (
            None
            if progress.dictionary_wait is None
            else _DictionaryLine(
                wait=progress.dictionary_wait,
                words=progress.dictionary_waiting_words or 0,
                next_at=_moment(
                    reported_at,
                    progress.dictionary_next_in_seconds,
                ),
                within_at=_moment(
                    reported_at,
                    progress.dictionary_wordnet_within_seconds,
                ),
            )
        )

        if progress.stage is Stage.DICTIONARY_ENRICHMENT:
            self._answered(
                reported_at,
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

    def _answered(
        self,
        at: datetime,
        /,
    ) -> None:
        if self._paused or self._waiting_until is not None:
            self._answers.clear()

            return

        self._answers.append(
            (
                at,
                self._done,
            ),
        )

        while at - self._answers[0][0] > _ESTIMATE_WINDOW:
            self._answers.popleft()

    def _finish(
        self,
    ) -> datetime | None:
        """
        When the stage finishes at the pace of the last minute's answers, or
        `None` when they cannot say.
        """

        if (
            self._steps is None
            or self._paused
            or self._waiting_until is not None
            or len(
                self._answers,
            )
            < 2
        ):
            return None

        (
            first_at,
            first_done,
        ) = self._answers[0]

        (
            last_at,
            last_done,
        ) = self._answers[-1]

        if last_at - first_at < _ESTIMATE_LEAST_SPAN or last_done - first_done < _ESTIMATE_LEAST_ANSWERS:
            return None

        return last_at + (last_at - first_at) * ((self._steps - last_done) / (last_done - first_done))

    def _quiet_after(
        self,
    ) -> timedelta:
        if (
            len(
                self._answers,
            )
            < 2
        ):
            return _LEAST_QUIET

        (
            first_at,
            first_done,
        ) = self._answers[0]

        last_done = self._answers[-1][1]

        if last_done <= first_done:
            return _LEAST_QUIET

        # NOTE:
        # A worker's report enters the window whether or not it brought an answer, so the gaps are measured up to the
        # report that brought the last one, or the quiet itself would lengthen the gap it is measured against.
        answered_at = next(
            at
            for (
                at,
                done,
            ) in self._answers
            if done == last_done
        )

        return max(
            _LEAST_QUIET,
            (answered_at - first_at) / (last_done - first_done) * _QUIET_GAPS,
        )

    def _line(
        self,
    ) -> _DictionaryLine | None:
        """
        What the line says of the words that wait, as the words held and the
        stage's own count stand now, or `None` when it says nothing of them.
        """

        if self._stored:
            return self._reported

        if self._held_at is None:
            return None

        state = dictionary_wait_of(
            held=self._held,
            patience_left_seconds=self._patience_left,
            steps=self._steps,
            done=self._done,
            paused=self._paused,
        )

        if state.wait is None:
            return None

        return _DictionaryLine(
            wait=state.wait,
            words=state.words,
            next_at=_moment(
                self._held_at,
                state.next_in_seconds,
            ),
            within_at=_moment(
                self._held_at,
                state.wordnet_within_seconds,
            ),
        )

    def _fields(
        self,
    ) -> _LineFields:
        """
        What the line's columns read besides the description, as it stands.
        """

        line = self._line()

        some_words = line is not None and line.wait is DictionaryWait.SOME_WORDS

        waiting = self._waiting_until if self._legacy else None

        # NOTE:
        # The quiet is said only while the stage counts answers and nothing else explains it: some words waiting for
        # their own reasons do not explain a quiet, while a wait the line names does, as does a paused dictionary,
        # which answers every word at once.
        counting = self._steps is not None and not self._paused and (self._waiting_until is None or some_words)

        # NOTE:
        # The bar is drawn while the line says what the stage does, and not while it names a wait that needs the room,
        # as a report written before that drew the old waiting text beside it.
        bar = waiting is not None or (line is None and not self._paused) or some_words

        self._drawn = (
            None if line is None else line.wait,
            0 if line is None else line.words,
            line is None or line.next_at is None,
            line is None or line.within_at is None,
            waiting is None,
            self._paused,
        )

        return _LineFields(
            count=_count(
                self._done,
                self._steps,
                self._unavailable,
            ),
            finish=self._finish(),
            waiting=waiting,
            dictionary=line,
            bar=bar,
            quiet_since=self._quiet_since if counting else None,
            quiet_after=self._quiet_after(),
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


def _moment(
    reported: datetime,
    seconds: float | None,
    /,
) -> datetime | None:
    if seconds is None:
        return None

    return reported + timedelta(
        seconds=seconds,
    )
