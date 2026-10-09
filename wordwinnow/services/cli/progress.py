"""
What the command line shows on standard error while a long command works.

One line, and only on an interactive terminal: what the command is doing, a
bar and a count when the size of the work is known, and how long the current
phase has taken.

An analysis a worker processes elsewhere shows the same line, from what the
worker last reported.

The line disappears when the work ends, so a report, JSON, or CSV on standard
output is never touched, and a pipe, a file, or a test receives nothing.
"""

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
)
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
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


def _count(
    done: int,
    steps: int | None,
    unavailable: int,
    /,
) -> str:
    if steps is None:
        return ""

    count = f"{done:,} of {steps:,}"

    if not unavailable:
        return count

    return f"{count}, {unavailable:,} unavailable"


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
    ) -> None:
        # NOTE:
        # No percentage and no time remaining: the steps of one phase can differ a hundredfold in length, as the level
        # experiment's candidates do, so a count and the time spent are the only figures that are always true.
        self._progress: Final = Progress(
            SpinnerColumn(),
            TextColumn(
                text_format="{task.description}",
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

        if self._task is None:
            self._task = self._progress.add_task(
                description=description,
                total=steps,
                count=_count(
                    self._done,
                    steps,
                    self._unavailable,
                ),
            )

            return

        self._progress.reset(
            task_id=self._task,
            total=steps,
            description=description,
            count=_count(
                self._done,
                steps,
                self._unavailable,
            ),
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
            count=_count(
                self._done,
                self._steps,
                self._unavailable,
            ),
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
            description=PAUSED_DICTIONARY if paused else STAGE_DESCRIPTIONS[Stage.DICTIONARY_ENRICHMENT],
            refresh=changed,
            count=_count(
                self._done,
                self._steps,
                self._unavailable,
            ),
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

        # NOTE:
        # A report arrives once per poll rather than once per word, so each one is drawn at once instead of at the
        # next tick.
        self._progress.update(
            task_id=self._task,
            completed=progress.done,
            description=PAUSED_DICTIONARY if progress.dictionary_paused else STAGE_DESCRIPTIONS[progress.stage],
            refresh=True,
            count=_count(
                self._done,
                self._steps,
                self._unavailable,
            ),
        )
