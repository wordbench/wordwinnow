"""
Record that a learner wants a document analyzed, and hand it to a worker.
"""

from wordwinnow.application.errors import (
    AnalysisNotPublishedError,
    MessagingUnavailableError,
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
    AnalysisOptions,
)
from wordwinnow.domain.analysis import (
    request_an_analysis as domain_request_an_analysis,
)
from wordwinnow.domain.document import (
    Document,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)


async def request_an_analysis(
    *,
    document: Document,
    profile: LearnerProfile,
    options: AnalysisOptions,
    new_unit_of_work: UnitOfWorkFactory,
    publisher: AnalysisEventPublisher,
    clock: Clock,
) -> Analysis:
    """
    Store a new analysis and publish that it was requested.

    The analysis is committed before the message is sent, so a worker can
    never receive an analysis that does not exist; if the message cannot be
    sent, the analysis stays requested, the caller learns why, and the requeue
    command publishes it once it has waited long enough.
    """

    analysis = domain_request_an_analysis(
        document=document,
        profile=profile,
        options=options,
        at=clock.now(),
    )

    async with new_unit_of_work() as uow:
        await uow.analyses.add(
            analysis=analysis,
        )

        await uow.commit()

    events = analysis.pull_events()

    try:
        for event in events:
            await publisher.publish(
                analysis=analysis,
                event=event,
            )

    except MessagingUnavailableError as exception:
        kind = type(
            exception,
        ).__name__

        raise AnalysisNotPublishedError(
            f"analysis {analysis.id} was stored but its request was not published ({kind})",
        ) from exception

    return analysis
