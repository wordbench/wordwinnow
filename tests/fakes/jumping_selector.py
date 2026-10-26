"""
The selector under the virtual-time loop: where a loop would block until its
next timer, it moves a virtual clock to that timer instead.
"""

from selectors import (
    SelectorKey,
    SelectSelector,
)
from typing import (
    Final,
    final,
    override,
)

# NOTE:
# How long the loop waits, in real time, for a worker thread's result when nothing else is scheduled.
_GRACE_SECONDS: Final = 5.0


@final
class VirtualClock:
    """
    A loop's time, which only the selector moves.
    """

    def __init__(
        self,
    ) -> None:
        self.now = 0.0


@final
class JumpingSelector(
    SelectSelector,
):
    """
    A selector that never blocks: what is ready is returned at once, and a
    wait for the next timer becomes a jump of the clock by the same time.
    """

    def __init__(
        self,
        *,
        clock: VirtualClock,
    ) -> None:
        super().__init__()

        self._clock: Final = clock

    @override
    def select(
        self,
        timeout: float | None = None,
    ) -> list[tuple[SelectorKey, int]]:
        ready = super().select(
            timeout=0,
        )

        if ready or timeout == 0:
            return ready

        # WARN:
        # Nothing ready and no timer means every task waits for something outside the loop: a worker thread's result,
        # which reaches the loop through the selector and is waited for in real time, or something nobody will ever
        # do, which on a real loop hangs for ever and is reported here once the grace is over.
        if timeout is None:
            arrived = super().select(
                timeout=_GRACE_SECONDS,
            )

            if arrived:
                return arrived

            raise RuntimeError(
                "the virtual loop has nothing ready and no timer scheduled, so its tasks wait for ever",
            )

        self._clock.now += timeout

        return []
