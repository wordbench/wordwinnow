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
    Analysis,
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
    `older_than_seconds` since it was requested, and a processing one once its
    worker has gone that long without reporting how far it has come, or,
    before any report, since it started.

    A stale requested analysis is republished as it is; a stale processing one
    is failed and reopened, the domain's only way back to requested, which
    clears its start and its failure, and is then republished.

    A processing analysis that its run completed or failed, or that another
    run started again, after it was found stale is left as it is and not
    republished.
    """

    now = clock.now()

    threshold = now - timedelta(
        seconds=older_than_seconds,
    )

    requeued: list[Analysis] = []

    async with new_unit_of_work() as uow:
        stale = await uow.analyses.list_stale(
            stale_before=threshold,
        )

        for analysis in stale:
            started_at = analysis.started_at

            if analysis.status is AnalysisStatus.PROCESSING and started_at is not None:
                analysis.fail(
                    at=now,
                    reason="processing was interrupted",
                )

                analysis.reopen(
                    at=now,
                )

                # WARN:
                # The analysis can change after the list above was read, and an unconditional save would turn a result
                # back into a request or take an analysis from a run that has just started it, so only the run found
                # stale is handed back.
                if not await uow.analyses.save_while_processing(
                    analysis=analysis,
                    started_at=started_at,
                ):
                    continue

            analysis.pull_events()

            requeued.append(
                analysis,
            )

        await uow.commit()

    # NOTE:
    # The store is committed before anything is published, as when an analysis is first requested: a publish that
    # fails leaves a requested analysis the next run finds again, never a message about a change that was rolled back.
    for analysis in requeued:
        await publisher.publish(
            analysis=analysis,
            event=AnalysisRequested(
                analysis_id=analysis.id,
                occurred_at=now,
            ),
        )

    return tuple(analysis.id for analysis in requeued)
