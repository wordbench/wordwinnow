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
    AsyncIterator,
    Awaitable,
    Callable,
)
from contextlib import (
    asynccontextmanager,
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
    ProcessingProgress,
)
from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.pipeline_progress import (
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
) -> AsyncIterator[None]:
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
            fs={
                keeping,
            },
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
            fs={
                self._keeping,
            },
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

    async def flush(
        self,
    ) -> None:
        """
        Write the progress now, if it changed since the last write.
        """

        if not self._changed or self._stage is None:
            return

        self._changed = False

        progress = ProcessingProgress(
            stage=self._stage,
            steps=self._steps,
            done=self._done,
            unavailable=self._unavailable,
            dictionary_paused=self._paused,
            reported_at=self._clock.now(),
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
