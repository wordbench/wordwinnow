"""
Recover analyses that were requested but never finished.

Two things can strand an analysis: the intake process dying between the commit
and the publish, and a worker dying mid-processing.

Both leave a row that no message describes, and this is the explicit path that
hands them back to a worker.
"""

from datetime import (
    timedelta,
)

from wordwinnow.application.ports.analysis_event_publisher import (
    AnalysisEventPublisher,
)
from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)
from wordwinnow.domain.analysis import (
    AnalysisRequested,
    AnalysisStatus,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)


async def requeue_stale_analyses(
    *,
    older_than_seconds: float,
    new_unit_of_work: UnitOfWorkFactory,
    publisher: AnalysisEventPublisher,
    clock: Clock,
) -> tuple[AnalysisId, ...]:
    """
    Republish every analysis that has been waiting or processing too long.

    A requested analysis is stale once it has waited longer than
    `older_than_seconds` since it was requested, and a processing one once it
    has been processing that long.

    A stale requested analysis is republished as it is; a stale processing one
    is failed and reopened, the domain's only way back to requested, which
    clears its start and its failure, and is then republished.
    """

    now = clock.now()

    threshold = now - timedelta(
        seconds=older_than_seconds,
    )

    async with new_unit_of_work() as uow:
        stale = await uow.analyses.list_stale(
            stale_before=threshold,
        )

        for analysis in stale:
            if analysis.status is AnalysisStatus.PROCESSING:
                analysis.fail(
                    at=now,
                    reason="processing was interrupted",
                )

                analysis.reopen(
                    at=now,
                )

                await uow.analyses.save(
                    analysis=analysis,
                )

            analysis.pull_events()

        await uow.commit()

    # NOTE:
    # The store is committed before anything is published, as when an analysis is first requested: a publish that
    # fails leaves a requested analysis the next run finds again, never a message about a change that was rolled back.
    for analysis in stale:
        await publisher.publish(
            analysis=analysis,
            event=AnalysisRequested(
                analysis_id=analysis.id,
                occurred_at=now,
            ),
        )

    return tuple(analysis.id for analysis in stale)
