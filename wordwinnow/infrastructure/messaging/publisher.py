"""
The Kafka adapter of the analysis event publisher.

Every record is keyed by its analysis, so the records of one analysis land on
one partition in order, and every record carries the analysis identifier and
the current trace context in its headers.
"""

from collections.abc import (
    Sequence,
)
from logging import (
    getLogger,
)
from typing import (
    Final,
    Protocol,
    final,
)

from aiokafka import (
    AIOKafkaProducer,
)
from aiokafka.errors import (
    KafkaError,
)
from opentelemetry.propagate import (
    inject,
)

from wordwinnow.application.errors import (
    MessagingUnavailableError,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisEvent,
)
from wordwinnow.infrastructure.messaging.codec import (
    CORRELATION_ID_HEADER,
    encode,
    message_for,
)

_logger: Final = getLogger(
    name="wordwinnow.messaging.publisher",
)


class MessageProducer(
    Protocol,
):
    """
    The part of a started `aiokafka.AIOKafkaProducer` the publisher uses.
    """

    async def send_and_wait(
        self,
        *,
        topic: str,
        value: bytes,
        key: bytes,
        headers: Sequence[tuple[str, bytes]],
    ) -> object:
        """
        Send one record and wait until the broker acknowledged it.
        """

        ...


def build_producer(
    *,
    bootstrap_servers: str,
) -> AIOKafkaProducer:
    """
    Create the producer for a process; the caller starts and stops it.
    """

    return AIOKafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks="all",
    )


@final
class KafkaAnalysisEventPublisher:
    """
    Publishes analysis events as versioned messages on their topics.

    Satisfies `AnalysisEventPublisher` structurally.
    """

    def __init__(
        self,
        *,
        producer: MessageProducer,
    ) -> None:
        self._producer: Final = producer

    async def publish(
        self,
        *,
        analysis: Analysis,
        event: AnalysisEvent,
    ) -> None:
        (
            topic,
            message,
        ) = message_for(
            analysis=analysis,
            event=event,
        )

        correlation_id = str(
            object=analysis.id,
        )

        try:
            await self._producer.send_and_wait(
                topic=topic,
                value=encode(
                    message=message,
                ),
                key=correlation_id.encode(),
                headers=_headers(
                    correlation_id,
                ),
            )

        except KafkaError as exception:
            kind = type(
                exception,
            ).__name__

            raise MessagingUnavailableError(
                f"the message broker could not be reached to publish {topic} for analysis {correlation_id} ({kind})",
            ) from exception

        _logger.info(
            msg="message.published",
            extra={
                "topic": topic,
                "analysis_id": correlation_id,
            },
        )


def _headers(
    correlation_id: str,
    /,
) -> list[tuple[str, bytes]]:
    carrier: dict[str, str] = {}

    inject(
        carrier=carrier,
    )

    return [
        (
            CORRELATION_ID_HEADER,
            correlation_id.encode(),
        ),
        *(
            (
                name,
                value.encode(),
            )
            for (
                name,
                value,
            ) in carrier.items()
        ),
    ]
