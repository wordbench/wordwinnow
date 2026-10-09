"""
The consumer loop, and the broker administration a service does at startup.

The loop commits an offset only after its record was handled, so a record
whose handling failed is delivered again unless the caller decides it is
poison.

Delivery is at least once: a rebalance moves a partition only between records,
and a record whose partition is lost anyway before its offset is committed is
delivered again to the partition's next owner.
"""

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from logging import (
    getLogger,
)
from typing import (
    Final,
    Protocol,
    cast,
)

from aiokafka import (
    AIOKafkaConsumer,
    ConsumerRecord,
    TopicPartition,
)
from aiokafka.admin import (
    AIOKafkaAdminClient,
    NewTopic,
)
from aiokafka.errors import (
    CommitFailedError,
    IllegalStateError,
    KafkaError,
    TopicAlreadyExistsError,
    for_code,
)
from opentelemetry.context import (
    Context,
)
from opentelemetry.propagate import (
    extract,
)

from wordwinnow.application.errors import (
    MessagingUnavailableError,
)
from wordwinnow.infrastructure.log_payload import (
    bind_correlation_id,
)
from wordwinnow.infrastructure.messaging.codec import (
    CORRELATION_ID_HEADER,
)
from wordwinnow.infrastructure.messaging.handoff import (
    PartitionHandoff,
)

type Record = ConsumerRecord[bytes, bytes]

type Handler = Callable[[bytes, Context], Awaitable[None]]

type FailureHandler = Callable[[Record, Exception], bool]

_logger: Final = getLogger(
    name="wordwinnow.messaging.consumer",
)

# NOTE:
# How long a rebalance waits for a record in hand, which aiokafka otherwise sets to the ten-second session timeout.
#
# The other members' join requests wait at the broker that long and aiokafka abandons a request after forty seconds,
# so thirty is the most that is safe; it covers an analysis without the dictionary or with a warm cache, and a record
# handled for longer than this during a rebalance is delivered again.
_REBALANCE_TIMEOUT_MS: Final = 30_000


class _TopicErrors(
    Protocol,
):
    """
    The per-topic outcome of a create-topics request, name and error code
    first.
    """

    topic_errors: Sequence[tuple[str, int, *tuple[object, ...]]]


class MessageConsumer(
    Protocol,
):
    """
    The part of a started `aiokafka.AIOKafkaConsumer` the loop uses.
    """

    async def getone(
        self,
    ) -> Record:
        """
        Wait for the next record.
        """

        ...

    async def commit(
        self,
        *,
        offsets: Mapping[TopicPartition, int],
    ) -> None:
        """
        Commit the given next-to-read offsets for the consumer group.
        """

        ...

    # WARN:
    # `AbstractSet` is the precise type here, not a loose one: aiokafka answers with its assignment's own `frozenset`,
    # or with a new `set` while it has none, and the loop only tests membership and iterates.
    def assignment(
        self,
    ) -> AbstractSet[TopicPartition]:
        """
        The partitions currently assigned to this consumer.
        """

        ...

    async def end_offsets(
        self,
        *,
        partitions: Sequence[TopicPartition],
    ) -> Mapping[TopicPartition, int]:
        """
        The high watermark of each partition, from the broker.
        """

        ...

    async def position(
        self,
        *,
        partition: TopicPartition,
    ) -> int:
        """
        The offset of the next record this consumer will read.
        """

        ...


def build_consumer(
    *,
    topics: Sequence[str],
    group_id: str,
    bootstrap_servers: str,
    handoff: PartitionHandoff,
) -> AIOKafkaConsumer:
    """
    Create the consumer for a service; the caller starts and stops it.

    Offsets are committed by the loop, never automatically, and a new group
    starts from the beginning so nothing published before it joined is lost.

    `handoff` is the one the loop is given, which is how a rebalance knows a
    record is still being handled.
    """

    # NOTE:
    # Automatic commits would make a crash mid-record a silent loss, which is why they are off.
    consumer = AIOKafkaConsumer(
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        rebalance_timeout_ms=_REBALANCE_TIMEOUT_MS,
    )

    consumer.subscribe(
        topics=tuple(
            topics,
        ),
        listener=handoff,
    )

    return consumer


async def consume(
    *,
    consumer: MessageConsumer,
    handoff: PartitionHandoff,
    handler: Handler,
    should_continue: Callable[[], bool],
    on_failure: FailureHandler,
) -> None:
    """
    Hand every record to `handler` and commit its offset once it is handled.

    A record whose handling raised is committed anyway when `on_failure`
    returns `True`, which is how the caller parks a poison message and where
    the caller logs and counts it; otherwise the exception propagates with the
    offset uncommitted, and the record is delivered again.

    A handled record whose partition this consumer no longer owns cannot be
    committed, and it is logged and left to be delivered again to the
    partition's next owner while the loop goes on, so a handler must tolerate
    seeing a record twice.
    """

    while should_continue():
        record = await consumer.getone()

        bind_correlation_id(
            correlation_id=_correlation_id_of(
                record.headers,
            ),
        )

        try:
            with handoff.holding():
                await _handle(
                    record,
                    handler,
                    on_failure,
                )

                await _commit(
                    consumer,
                    record,
                )

        finally:
            bind_correlation_id(
                correlation_id=None,
            )


async def _handle(
    record: Record,
    handler: Handler,
    on_failure: FailureHandler,
    /,
) -> None:
    try:
        value = record.value

        if value is None:
            raise ValueError(
                "the record carries no value",
            )

        await handler(
            value,
            extract(
                carrier=_carrier_of(
                    record.headers,
                ),
            ),
        )

    except Exception as exception:
        if not on_failure(
            record,
            exception,
        ):
            raise


async def _commit(
    consumer: MessageConsumer,
    record: Record,
    /,
) -> None:
    partition = TopicPartition(
        topic=record.topic,
        partition=record.partition,
    )

    try:
        await consumer.commit(
            offsets={
                partition: record.offset + 1,
            },
        )

    except CommitFailedError as exception:
        _log_commit_lost(
            record,
            exception,
        )

    except IllegalStateError as exception:
        # WARN:
        # aiokafka raises this one class for every partition it does not hold, so it means a lost partition only when
        # the assignment agrees; a partition still held is a fault, and it propagates.
        if partition in consumer.assignment():
            raise

        _log_commit_lost(
            record,
            exception,
        )


def _log_commit_lost(
    record: Record,
    exception: KafkaError,
    /,
) -> None:
    _logger.warning(
        msg="message.commit_lost",
        extra={
            "topic": record.topic,
            "partition": record.partition,
            "offset": record.offset,
            "reason": type(
                exception,
            ).__name__,
        },
    )


def _carrier_of(
    headers: Sequence[tuple[str, bytes]],
    /,
) -> dict[str, str]:
    return {
        name: value.decode(
            errors="replace",
        )
        for (
            name,
            value,
        ) in headers
    }


def _correlation_id_of(
    headers: Sequence[tuple[str, bytes]],
    /,
) -> str | None:
    return _carrier_of(
        headers,
    ).get(
        CORRELATION_ID_HEADER,
    )


async def topic_lag(
    *,
    consumer: MessageConsumer,
) -> dict[tuple[str, int], int]:
    """
    How many records each assigned partition still holds ahead of this
    consumer, keyed by topic and partition.
    """

    assigned = tuple(
        consumer.assignment(),
    )

    if not assigned:
        return {}

    ends = await consumer.end_offsets(
        partitions=assigned,
    )

    lag: dict[tuple[str, int], int] = {}

    for partition in assigned:
        position = await consumer.position(
            partition=partition,
        )

        lag[
            (
                partition.topic,
                partition.partition,
            )
        ] = ends[partition] - position

    return lag


async def ensure_topics(
    *,
    topics: Sequence[str],
    partitions: int,
    bootstrap_servers: str,
) -> None:
    """
    Create the topics that do not exist yet, with one replica each.

    Raises `MessagingUnavailableError` when the broker cannot be reached or
    refuses a topic.
    """

    admin = AIOKafkaAdminClient(
        bootstrap_servers=bootstrap_servers,
    )

    try:
        await admin.start()

        response = await admin.create_topics(
            new_topics=[
                NewTopic(
                    name=topic,
                    num_partitions=partitions,
                    replication_factor=1,
                )
                for topic in topics
            ],
        )

    except KafkaError as exception:
        kind = type(
            exception,
        ).__name__

        raise MessagingUnavailableError(
            f"the message broker at {bootstrap_servers} could not be reached to create topics ({kind})",
        ) from exception

    finally:
        await admin.close()

    # NOTE:
    # The broker answers per topic with an error code instead of raising, and an existing topic is the outcome this
    # function exists to tolerate.
    #
    # `create_topics` is annotated with the protocol's base response; every version of the real one carries the codes
    # under this name.
    topic_errors = cast(
        typ=_TopicErrors,
        val=response,
    ).topic_errors

    for topic_error in topic_errors:
        name = topic_error[0]

        error_code = topic_error[1]

        if error_code in {
            0,
            TopicAlreadyExistsError.errno,
        }:
            continue

        kind = for_code(
            error_code=error_code,
        ).__name__

        raise MessagingUnavailableError(
            f"topic {name} could not be created ({kind}, Kafka error code {error_code})",
        )
