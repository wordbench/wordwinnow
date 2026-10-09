"""
How analysis events leave the process.

The port takes domain events; the adapter turns them into versioned
integration messages, because the message schema is a contract with other
services and belongs with the transport.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisEvent,
)


class AnalysisEventPublisher(
    Protocol,
):
    """
    Publishes what happened to an analysis for other services to react to.

    Raises `MessagingUnavailableError` from the application's errors when the
    message could not be handed to the broker.
    """

    async def publish(
        self,
        *,
        analysis: Analysis,
        event: AnalysisEvent,
    ) -> None:
        """
        Publish `event`, with whatever of `analysis` the message carries.
        """

        ...
