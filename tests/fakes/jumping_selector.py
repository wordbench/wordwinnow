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
        # Nothing ready and no timer means every task waits for something nobody will ever do: on a real loop that
        # hangs for ever, so it is reported at once instead.
        if timeout is None:
            raise RuntimeError(
                "the virtual loop has nothing ready and no timer scheduled, so its tasks wait for ever",
            )

        self._clock.now += timeout

        return []
