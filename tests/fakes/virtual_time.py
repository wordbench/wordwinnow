"""
Time as an event loop can fake it: a loop whose clock jumps to its next timer
whenever nothing is ready.

Concurrent lookups interleave exactly as asyncio schedules them, and a
two-minute outage takes no real time, so a test can state what happens at
second 12 of an outage rather than only in what order things happen.

Nothing a test runs on this loop may use a real socket, because work done
there is invisible to the jump and the clock would run ahead of it.

A test may run code that hands work to a thread while no timer is due, such as
the pipeline's tokenizing: the loop then waits for the thread in real time; a
thread still working while a timer is due would see the clock jump over it.
"""

from asyncio import (
    Runner,
    SelectorEventLoop,
    get_running_loop,
)
from collections.abc import (
    Callable,
    Coroutine,
)
from typing import (
    Any,
    Final,
    final,
    override,
)

from tests.fakes.jumping_selector import (
    JumpingSelector,
    VirtualClock,
)


@final
class _VirtualLoop(
    SelectorEventLoop,
):
    """
    An event loop on virtual time.
    """

    def __init__(
        self,
    ) -> None:
        self._virtual: Final = VirtualClock()

        super().__init__(
            selector=JumpingSelector(
                clock=self._virtual,
            ),
        )

    @override
    def time(
        self,
    ) -> float:
        return self._virtual.now


def run_in_virtual_time[T](
    *,
    main: Callable[[], Coroutine[Any, Any, T]],
) -> T:
    """
    Run `main` to completion on a fresh loop whose clock starts at nought and
    moves only when every task waits for a timer.

    Inside, `get_running_loop().time` is the clock to give whatever takes one,
    and `asyncio.sleep` waits on it.
    """

    async def contained() -> T:
        try:
            return await main()

        finally:
            # NOTE:
            # The default executor is shut down inside the run, where its threads are waited for in real time; the
            # runner's own shutdown waits on the loop's clock, which would jump past them.
            await get_running_loop().shutdown_default_executor()

    with Runner(
        loop_factory=_VirtualLoop,
    ) as runner:
        return runner.run(
            coro=contained(),
        )
