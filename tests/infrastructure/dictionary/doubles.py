"""
Time as a test moves it: a clock that stands still, and a sleep that records
what it was asked and moves the clock instead of waiting.

Beside them, an observer that records what the client reports.
"""

from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.dictionary.free_dictionary import (
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)


@final
class FakeMonotonicClock:
    """
    A monotonic clock that moves only when a test moves it.
    """

    def __init__(
        self,
    ) -> None:
        self.now = 0.0

    def __call__(
        self,
    ) -> float:
        return self.now

    def advance(
        self,
        *,
        seconds: float,
    ) -> None:
        self.now += seconds


@final
class RecordingSleep:
    """
    A sleep that records every delay and moves the clock by it.
    """

    def __init__(
        self,
        *,
        clock: FakeMonotonicClock,
    ) -> None:
        self._clock: Final = clock

        self.delays: Final[list[float]] = []

    async def __call__(
        self,
        delay: float,
        /,
    ) -> None:
        self.delays.append(
            delay,
        )

        self._clock.advance(
            seconds=delay,
        )


@final
class RecordingObserver:
    """
    An observer that keeps every wait and retry it hears, in order.

    Satisfies `ProviderObserver` structurally.
    """

    def __init__(
        self,
    ) -> None:
        self.waits: Final[list[tuple[WaitCause, float]]] = []

        self.retries: Final[list[FailureReason]] = []

    def waited(
        self,
        *,
        cause: WaitCause,
        seconds: float,
    ) -> None:
        self.waits.append(
            (
                cause,
                seconds,
            ),
        )

    def retried(
        self,
        *,
        reason: FailureReason,
    ) -> None:
        self.retries.append(
            reason,
        )

    def seconds_waited_for(
        self,
        *,
        cause: WaitCause,
    ) -> float:
        """
        The seconds every wait for `cause` added up to.
        """

        return sum(
            seconds
            for (
                waited_for,
                seconds,
            ) in self.waits
            if waited_for is cause
        )
