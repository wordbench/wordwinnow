"""
The publisher keys, labels, and traces every record, and turns a broker
failure into the port's error.
"""

from collections.abc import (
    Sequence,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from aiokafka.errors import (
    KafkaConnectionError,
)
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    use_span,
)
from pytest import (
    raises,
)

from tests.infrastructure.messaging.analyses import (
    completed_analysis,
)
from wordwinnow.application.errors import (
    MessagingUnavailableError,
)
from wordwinnow.infrastructure.messaging.codec import (
    decode,
)
from wordwinnow.infrastructure.messaging.messages import (
    AnalysisCompletedV1,
)
from wordwinnow.infrastructure.messaging.publisher import (
    KafkaAnalysisEventPublisher,
)
from wordwinnow.infrastructure.messaging.topics import (
    ANALYSIS_COMPLETED_V1,
)

# NOTE:
# The trace ID of one of the W3C Trace Context specification's example `traceparent` headers, as the 128-bit integer
# OpenTelemetry's span context holds; the header writes it as the 32 hexadecimal digits
# `0af7651916cd43dd8448eb211c80319c`.
_TRACE_ID: Final = 0x0AF7651916CD43DD8448EB211C80319C

# NOTE:
# The same example's parent span ID, as the 64-bit integer the span context holds; the header writes it as the 16
# hexadecimal digits `b7ad6b7169203331`.
_SPAN_ID: Final = 0xB7AD6B7169203331


@final
class RecordingProducer:
    """
    Keeps every record instead of sending it.
    """

    def __init__(
        self,
    ) -> None:
        self.sent: Final[list[tuple[str, bytes, bytes, Sequence[tuple[str, bytes]]]]] = []

    async def send_and_wait(
        self,
        *,
        topic: str,
        value: bytes,
        key: bytes,
        headers: Sequence[tuple[str, bytes]],
    ) -> object:
        self.sent.append(
            (
                topic,
                value,
                key,
                headers,
            ),
        )

        return None


@final
class RefusingProducer:
    """
    Fails every send the way an unreachable broker would.
    """

    async def send_and_wait(
        self,
        *,
        topic: str,
        value: bytes,
        key: bytes,
        headers: Sequence[tuple[str, bytes]],
    ) -> object:
        raise KafkaConnectionError(
            "broker unreachable",
        )


@final
class TestKafkaAnalysisEventPublisher:
    async def test_a_record_is_keyed_by_its_analysis_and_carries_its_headers(
        self,
    ) -> None:
        producer = RecordingProducer()

        publisher = KafkaAnalysisEventPublisher(
            producer=producer,
        )

        analysis = completed_analysis()

        (event,) = analysis.pull_events()

        with use_span(
            span=NonRecordingSpan(
                context=SpanContext(
                    trace_id=_TRACE_ID,
                    span_id=_SPAN_ID,
                    is_remote=False,
                ),
            ),
        ):
            await publisher.publish(
                analysis=analysis,
                event=event,
            )

        (
            (
                topic,
                value,
                key,
                headers,
            ),
        ) = producer.sent

        assert topic == ANALYSIS_COMPLETED_V1

        assert (
            key
            == str(
                object=analysis.id,
            ).encode()
        )

        assert (
            decode(
                data=value,
                model=AnalysisCompletedV1,
            ).analysis_id
            == analysis.id.value
        )

        by_name = MappingProxyType(
            mapping=dict(
                headers,
            ),
        )

        assert by_name["correlation_id"] == key

        # NOTE:
        # The same trace and span IDs written as a `traceparent`, ending in trace flags `00` because the span context
        # above sets none, so the trace is not sampled.
        assert by_name["traceparent"] == b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-00"

    async def test_a_broker_failure_is_the_port_error(
        self,
    ) -> None:
        publisher = KafkaAnalysisEventPublisher(
            producer=RefusingProducer(),
        )

        analysis = completed_analysis()

        (event,) = analysis.pull_events()

        with raises(
            expected_exception=MessagingUnavailableError,
            match="KafkaConnectionError",
        ):
            await publisher.publish(
                analysis=analysis,
                event=event,
            )
