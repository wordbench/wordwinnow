"""
The publisher of the local mode, where there is no bus.

The local mode runs the pipeline in the same process that requested it, so an
event has no other process to reach; the publisher records nothing and raises
nothing, and the composition root says so where it is chosen.
"""

from typing import (
    final,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisEvent,
)


@final
class NullPublisher:
    """
    Accepts every event and delivers it nowhere.

    Satisfies `AnalysisEventPublisher` structurally.
    """

    async def publish(
        self,
        *,
        analysis: Analysis,
        event: AnalysisEvent,
    ) -> None:
        return None
