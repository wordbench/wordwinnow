"""
How a long command reports what it is doing: one phase at a time, each with
the number of steps it takes when that number is known.

The work reports here, and the command line decides whether a person sees it.

A worker reports the same way into the analysis store, where a command waiting
for the analysis elsewhere reads it.

A view that shows what the dictionary is doing reads it on an interval, in
this process or from the enrichment service.
"""

from asyncio import (
    Task,
    create_task,
    wait,
)
from asyncio import (
    sleep as asyncio_sleep,
)
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Sequence,
)
from contextlib import (
    asynccontextmanager,
)
from dataclasses import (
    dataclass,
)
from datetime import (
    datetime,
    timedelta,
)
from logging import (
    getLogger,
)
from types import (
    TracebackType,
)
from typing import (
    Final,
    Protocol,
    Self,
    final,
)

from wordwinnow.application.dto import (
    DictionaryWait,
    ProcessingProgress,
)
from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.pipeline_progress import (
    HeldWords,
    PipelineProgress,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    MAX_DELAY_SECONDS,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)

type ReadActivity = Callable[[], Awaitable[DictionaryActivity]]

# NOTE:
# How often a worker writes its progress at most: often enough that a count read once a second keeps moving, and
# seldom enough that a long analysis costs one small write a second.
STORED_PROGRESS_INTERVAL_SECONDS: Final = 1.0

# NOTE:
# How often a view of the dictionary's mechanics reads them: often enough that a pause before a retry, a quarter of a
# second at the shortest, is seen.
ACTIVITY_INTERVAL_SECONDS: Final = 0.25

_logger: Final = getLogger(
    name="wordwinnow.progress",
)


class WorkProgress(
    Protocol,
):
    """
    Hears the phases of a piece of work and the steps of each.
    """

    def begin(
        self,
        *,
        description: str,
        steps: int | None,
    ) -> None:
        """
        A new phase begins, and takes `steps` steps when the number is known.
        """

        ...

    def describe(
        self,
        *,
        description: str,
    ) -> None:
        """
        The current phase goes on, now described as `description`.
        """

        ...

    def advance(
        self,
    ) -> None:
        """
        One step of the current phase has ended.
        """

        ...


class AnalysisProgress(
    WorkProgress,
    PipelineProgress,
    Protocol,
):
    """
    Hears an analysis run in this process: the command's own phases, such as
    loading the lists, and then the pipeline's stages.
    """


class RemoteAnalysisProgress(
    WorkProgress,
    Protocol,
):
    """
    Hears an analysis run elsewhere: the command's own phases, and how far the
    worker processing it last said it had come.
    """

    def processing(
        self,
        *,
        progress: ProcessingProgress,
    ) -> None:
        """
        The worker last reported `progress`.
        """

        ...


@final
class ActivityUnreadableError(
    RuntimeError,
):
    """
    Raised when the dictionary's activity cannot be read, with the reason as
    its message.
    """


class DictionaryWatch(
    Protocol,
):
    """
    Hears what the dictionary is doing, for a view that shows its mechanics.
    """

    def watched(
        self,
        *,
        activity: DictionaryActivity,
    ) -> None:
        """
        The dictionary was doing `activity` when it was last read.
        """

        ...

    def unwatched(
        self,
        *,
        reason: str,
    ) -> None:
        """
        The dictionary's activity cannot be shown, for `reason`.
        """

        ...


@asynccontextmanager
async def watching(
    *,
    read: ReadActivity,
    watch: DictionaryWatch,
    interval_seconds: float = ACTIVITY_INTERVAL_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio_sleep,
) -> AsyncGenerator[None]:
    """
    Tell `watch` what `read` finds every `interval_seconds` while the block
    runs.

    A read that fails tells `watch` why and is tried again at the next
    interval; it never reaches the work inside the block.
    """

    async def keep() -> None:
        while True:
            try:
                activity = await read()

            except ActivityUnreadableError as exception:
                watch.unwatched(
                    reason=str(
                        object=exception,
                    ),
                )

            else:
                watch.watched(
                    activity=activity,
                )

            await sleep(
                interval_seconds,
            )

    keeping = create_task(
        coro=keep(),
    )

    try:
        yield

    finally:
        keeping.cancel()

        # NOTE:
        # Waiting rather than awaiting the task lets its cancellation end it without raising here, while a
        # cancellation of the work itself still propagates.
        await wait(
            fs=(keeping,),
        )


def paused_after(
    *,
    paused: bool,
    lookup: DictionaryLookup,
) -> bool:
    """
    Whether the dictionary is paused once `lookup` has ended: a refusal by the
    open circuit pauses it, an answer ends the pause, and a word left
    unanswered for another reason leaves it as it was.
    """

    # NOTE:
    # The pause ends only with an answer: a request already in flight when the circuit opened can still end unanswered
    # for another reason, and the dictionary is no more answering for it.
    if lookup.failure_reason == FailureReason.CIRCUIT_OPEN:
        return True

    if lookup.outcome is LookupOutcome.UNAVAILABLE:
        return paused

    return False


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DictionaryWaitState:
    """
    What the dictionary stage waits for, as a command line or a stored report
    says it.

    `wait` is `None` when no wait is named, `words` how many words wait,
    `next_in_seconds` the seconds until the next check or ask the wait names,
    and `wordnet_within_seconds` the seconds within which WordNet's meaning
    stands in for every word still waiting, each `None` when the wait names
    none.

    The seconds count from the moment of the report the state was drawn from.
    """

    wait: DictionaryWait | None

    words: int

    next_in_seconds: float | None

    wordnet_within_seconds: float | None


def dictionary_wait_of(
    *,
    held: Sequence[HeldWords],
    patience_left_seconds: float,
    steps: int | None,
    done: int,
    paused: bool,
) -> DictionaryWaitState:
    """
    What the dictionary stage waits for, given the words it holds, the waiting
    time left, and its own count of `steps` and words `done`, with `paused`
    saying the dictionary is paused.

    A word waits for its own reasons, for the dictionary to answer, or for the
    pause the dictionary asked for, by the reason its last refusal gave, so
    the same words draw the same wait in a command line and in a report.
    """

    # NOTE:
    # A word counts toward the wait of the dictionary's circuit or its pause until the dictionary answers a held word
    # after that word's last refusal, and toward its own wait while it sleeps or until then.
    #
    # The word whose own failure opened the circuit keeps that failure's reason, so it counts toward its own wait.
    circuit: HeldWords | None = None

    pause: HeldWords | None = None

    own_words = 0

    own_wake: float | None = None

    own_bound = False

    for words in held:
        if words.reason == FailureReason.CIRCUIT_OPEN:
            circuit = words

            continue

        if words.reason == FailureReason.RATE_LIMITED:
            pause = words

            continue

        own_words += words.turned_away + words.others_sleeping

        if words.wake_in_seconds is not None and (own_wake is None or words.wake_in_seconds < own_wake):
            own_wake = words.wake_in_seconds

        # NOTE:
        # WordNet's meaning is named for words that wait long or wait again, because a word's own retry after its
        # attempts waits a second or two, and a bound of minutes beside it answers a question nobody asked.
        #
        # The wait a word was given is the client's own boundary: the client keeps a wait of `MAX_DELAY_SECONDS` or
        # less inside a lookup and hands a longer one back.
        if (words.longest_wait_seconds or 0.0) > MAX_DELAY_SECONDS or words.repeated:
            own_bound = True

    counted = (
        own_words + (0 if circuit is None else circuit.turned_away) + (0 if pause is None else pause.turned_away)
    )

    # NOTE:
    # The dictionary's circuit comes before its pause, the pause before the dictionary's own paused state, and that
    # before the words' own waits, so that the wait that explains the most is the one named.
    for (
        scope,
        named,
    ) in (
        (
            circuit,
            DictionaryWait.NOT_ANSWERING,
        ),
        (
            pause,
            DictionaryWait.ASKED_TO_PAUSE,
        ),
    ):
        if scope is None or not scope.turned_away:
            continue

        return DictionaryWaitState(
            wait=named,
            words=counted,
            next_in_seconds=scope.turned_away_wake_in_seconds,
            wordnet_within_seconds=patience_left_seconds,
        )

    if paused:
        return DictionaryWaitState(
            wait=None,
            words=0,
            next_in_seconds=None,
            wordnet_within_seconds=None,
        )

    # NOTE:
    # Only waiting words are left when every word not yet ended is one of them, and the wait is named only while one
    # of them sleeps, because a word that wakes to ask again is no longer waiting for its time.
    if steps is not None and own_wake is not None and own_words == steps - done:
        return DictionaryWaitState(
            wait=DictionaryWait.WAITING_WORDS,
            words=own_words,
            next_in_seconds=own_wake,
            wordnet_within_seconds=patience_left_seconds if own_bound else None,
        )

    return DictionaryWaitState(
        wait=DictionaryWait.SOME_WORDS if own_words else None,
        words=own_words,
        next_in_seconds=None,
        wordnet_within_seconds=None,
    )


@final
class Unwatched:
    """
    Progress that nobody watches, for work run from a test or another program.

    Satisfies `AnalysisProgress` and `RemoteAnalysisProgress` structurally.
    """

    def begin(
        self,
        *,
        description: str,
        steps: int | None,
    ) -> None:
        return

    def describe(
        self,
        *,
        description: str,
    ) -> None:
        return

    def advance(
        self,
    ) -> None:
        return

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        return

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        return

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        return

    def processing(
        self,
        *,
        progress: ProcessingProgress,
    ) -> None:
        return


UNWATCHED: Final = Unwatched()


@final
class StoredProgress:
    """
    Keeps how far a worker has come with an analysis in the analysis store, at
    most once per interval and only when it changed, for whoever reads the
    analysis meanwhile.

    Satisfies `PipelineProgress` structurally, and runs as an async context
    manager around the processing.

    A write that fails is logged once and never reaches the analysis.
    """

    def __init__(
        self,
        *,
        analysis_id: AnalysisId,
        new_unit_of_work: UnitOfWorkFactory,
        clock: Clock,
        interval_seconds: float = STORED_PROGRESS_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio_sleep,
    ) -> None:
        self._analysis_id: Final = analysis_id

        self._new_unit_of_work: Final = new_unit_of_work

        self._clock: Final = clock

        self._interval: Final = interval_seconds

        self._sleep: Final = sleep

        self._stage: Stage | None = None

        self._steps: int | None = None

        self._done = 0

        self._unavailable = 0

        self._paused = False

        self._waiting_until: tuple[datetime, datetime] | None = None

        self._held: Sequence[HeldWords] = ()

        self._held_at: datetime | None = None

        self._patience_left = 0.0

        self._changed = False

        self._warned = False

        self._keeping: Task[None] | None = None

    async def __aenter__(
        self,
    ) -> Self:
        self._keeping = create_task(
            coro=self._keep(),
        )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        if self._keeping is None:
            return

        self._keeping.cancel()

        # NOTE:
        # Waiting rather than awaiting the task lets its cancellation end it without raising here, while a
        # cancellation of the processing itself still propagates.
        await wait(
            fs=(self._keeping,),
        )

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        self._stage = stage

        self._steps = steps

        self._done = 0

        self._unavailable = 0

        self._paused = False

        self._waiting_until = None

        self._held = ()

        self._held_at = None

        self._changed = True

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        self._done += 1

        if lookup.outcome is LookupOutcome.UNAVAILABLE:
            self._unavailable += 1

        self._paused = paused_after(
            paused=self._paused,
            lookup=lookup,
        )

        self._changed = True

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        now = self._clock.now()

        # NOTE:
        # The held words are kept with the moment they were reported and the state is drawn from them when the report
        # is written, a turn of the loop of its own, so a word that is leaving is never written between its release
        # and its `looked_up`, and `ending` has nothing to add here.
        self._held = held

        self._held_at = now

        self._patience_left = patience_left_seconds

        # NOTE:
        # A wait is kept as the moments it ends, read on the clock the report is stamped with, so a report written a
        # second later says a second less.
        self._waiting_until = (
            None
            if retry_in_seconds is None
            else (
                now
                + timedelta(
                    seconds=retry_in_seconds,
                ),
                now
                + timedelta(
                    seconds=patience_left_seconds,
                ),
            )
        )

        self._changed = True

    async def flush(
        self,
    ) -> None:
        """
        Write the progress now, if it changed since the last write.
        """

        if not self._changed or self._stage is None:
            return

        self._changed = False

        reported_at = self._clock.now()

        waiting = self._waiting_until

        state = dictionary_wait_of(
            held=self._held,
            patience_left_seconds=self._patience_left,
            steps=self._steps,
            done=self._done,
            paused=self._paused,
        )

        progress = ProcessingProgress(
            stage=self._stage,
            steps=self._steps,
            done=self._done,
            unavailable=self._unavailable,
            dictionary_paused=self._paused,
            reported_at=reported_at,
            dictionary_retry_in_seconds=(
                None
                if waiting is None
                else max(
                    0.0,
                    (waiting[0] - reported_at).total_seconds(),
                )
            ),
            dictionary_patience_left_seconds=(
                None
                if waiting is None
                else max(
                    0.0,
                    (waiting[1] - reported_at).total_seconds(),
                )
            ),
            dictionary_waiting_words=state.words,
            dictionary_wait=state.wait,
            dictionary_next_in_seconds=_seconds_left(
                self._held_at,
                state.next_in_seconds,
                reported_at,
            ),
            dictionary_wordnet_within_seconds=_seconds_left(
                self._held_at,
                state.wordnet_within_seconds,
                reported_at,
            ),
        )

        try:
            async with self._new_unit_of_work() as uow:
                await uow.analyses.record_progress(
                    analysis_id=self._analysis_id,
                    progress=progress,
                )

                await uow.commit()

        except Exception as exception:
            # WARN:
            # Progress is for whoever watches, so a write that fails is tried again at the next interval rather than
            # failing the analysis, and only the first failure is logged.
            self._changed = True

            if not self._warned:
                self._warned = True

                kind = type(
                    exception,
                ).__name__

                _logger.warning(
                    msg="progress.unrecorded",
                    extra={
                        "analysis_id": str(
                            object=self._analysis_id,
                        ),
                        "reason": f"{kind}: {exception}",
                    },
                )

    async def _keep(
        self,
    ) -> None:
        while True:
            await self._sleep(
                self._interval,
            )

            await self.flush()


def _seconds_left(
    reported: datetime | None,
    seconds: float | None,
    now: datetime,
    /,
) -> float | None:
    # NOTE:
    # A time the state names counts from the moment its words were reported, so a report written later says less.
    if reported is None or seconds is None:
        return None

    return max(
        0.0,
        (
            reported
            + timedelta(
                seconds=seconds,
            )
            - now
        ).total_seconds(),
    )
