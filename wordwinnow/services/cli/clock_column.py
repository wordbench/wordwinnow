"""
A column of the progress line drawn from the moment it is drawn, so a time the
line names counts down between two reports.
"""

from collections.abc import (
    Callable,
)
from datetime import (
    datetime,
)
from typing import (
    Final,
    final,
    override,
)

from rich.console import (
    RenderableType,
)
from rich.progress import (
    ProgressColumn,
    Task,
)


@final
class ClockColumn(
    ProgressColumn,
):
    """
    A column whose content `draw` makes from a task and the moment `clock`
    reads each time the line is drawn.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        draw: Callable[[Task, datetime], RenderableType],
    ) -> None:
        super().__init__()

        self._clock: Final = clock

        self._draw: Final = draw

    @override
    def render(
        self,
        task: Task,
    ) -> RenderableType:
        return self._draw(
            task,
            self._clock(),
        )
