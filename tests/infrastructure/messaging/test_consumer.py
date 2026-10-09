"""
The consume loop commits after success, commits a poison record only when told
to, and otherwise lets the failure out with the offset uncommitted.

A rebalance takes a partition only between records, and a handled record whose
partition was lost anyway is left for its next owner rather than stopping the
loop.
"""

from asyncio import (
    Event,
    create_task,
    sleep,
)
from collections.abc import (
    Mapping,
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from logging import (
    WARNING,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from aiokafka import (
    ConsumerRecord,
    TopicPartition,
)
from aiokafka.errors import (
    CommitFailedError,
    IllegalStateError,
    KafkaError,
)
from opentelemetry.context import (
    Context,
)
from opentelemetry.trace import (
    get_current_span,
)
from pytest import (
    LogCaptureFixture,
    raises,
)

from wordwinnow.infrastructure.log_payload import (
    current_correlation_id,
)
from wordwinnow.infrastructure.messaging.consumer import (
    Record,
    consume,
    topic_lag,
)
from wordwinnow.infrastructure.messaging.handoff import (
    PartitionHandoff,
)
from wordwinnow.infrastructure.messaging.topics import (
    ANALYSIS_REQUESTED_V1,
)

_TOPIC: Final = ANALYSIS_REQUESTED_V1

_PARTITION: Final = TopicPartition(
    topic=_TOPIC,
    partition=0,
)

# NOTE:
# A W3C Trace Context `traceparent` header, one of the specification's own examples: version `00`, trace ID
# `0af7651916cd43dd8448eb211c80319c`, parent span ID `b7ad6b7169203331`, and trace flags `01`, sampled.
_TRACEPARENT: Final = b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

# NOTE:
# The header's trace ID as the consumer recovers it: OpenTelemetry holds a trace ID as a 128-bit integer, not as the
# header's 32 hexadecimal digits.
_TRACE_ID: Final = 0x0AF7651916CD43DD8448EB211C80319C


@final
class FakeConsumer:
    """
    Serves a fixed sequence of records and remembers what was committed.
    """

    def __init__(
        self,
        *,
        records: Sequence[Record],
        end_offset: int,
    ) -> None:
        self.pending: Final[list[Record]] = list(
            records,
        )

        self.committed: Final[list[Mapping[TopicPartition, int]]] = []

        self._ends: Final = MappingProxyType(
            mapping={
                TopicPartition(
                    topic=_TOPIC,
                    partition=0,
                ): end_offset,
            },
        )

        self._position = 0

    async def getone(
        self,
    ) -> Record:
        record = self.pending.pop(
            0,
        )

        self._position = record.offset + 1

        return record

    async def commit(
        self,
        *,
        offsets: Mapping[TopicPartition, int],
    ) -> None:
        # NOTE:
        # A real commit is a round trip to the broker, so other tasks run before it lands.
        await sleep(
            delay=0,
        )

        self.committed.append(
            offsets,
        )

    def assignment(
        self,
    ) -> AbstractSet[TopicPartition]:
        return frozenset(
            self._ends,
        )

    async def end_offsets(
        self,
        *,
        partitions: Sequence[TopicPartition],
    ) -> Mapping[TopicPartition, int]:
        return {partition: self._ends[partition] for partition in partitions}

    async def position(
        self,
        *,
        partition: TopicPartition,
    ) -> int:
        return self._position


@final
class RevokingConsumer:
    """
    Serves records from one partition and refuses the first commit the way
    aiokafka does when a rebalance has intervened, with the partition either
    gone from the assignment or still in it; the next record comes after the
    partition is assigned again.
    """

    def __init__(
        self,
        *,
        records: Sequence[Record],
        refusal: KafkaError,
        holds_partition: bool,
    ) -> None:
        self.pending: Final[list[Record]] = list(
            records,
        )

        self.committed: Final[list[Mapping[TopicPartition, int]]] = []

        self._refusal: Final = refusal

        self._holds_partition: Final = holds_partition

        self._refused = False

        self._assigned = True

    async def getone(
        self,
    ) -> Record:
        self._assigned = True

        return self.pending.pop(
            0,
        )

    async def commit(
        self,
        *,
        offsets: Mapping[TopicPartition, int],
    ) -> None:
        if not self._refused:
            self._refused = True

            self._assigned = self._holds_partition

            raise self._refusal

        self.committed.append(
            offsets,
        )

    def assignment(
        self,
    ) -> AbstractSet[TopicPartition]:
        if not self._assigned:
            return frozenset()

        return frozenset(
            {
                _PARTITION,
            },
        )

    async def end_offsets(
        self,
        *,
        partitions: Sequence[TopicPartition],
    ) -> Mapping[TopicPartition, int]:
        raise AssertionError(
            "the loop does not ask for end offsets",
        )

    async def position(
        self,
        *,
        partition: TopicPartition,
    ) -> int:
        raise AssertionError(
            "the loop does not ask for positions",
        )


def _record(
    offset: int,
    value: bytes | None,
    /,
) -> Record:
    return ConsumerRecord(
        topic=_TOPIC,
        partition=0,
        offset=offset,
        timestamp=0,
        timestamp_type=0,
        key=b"bar",
        value=value,
        checksum=None,
        serialized_key_size=3,
        serialized_value_size=0,
        headers=[
            (
                "correlation_id",
                b"bar",
            ),
            (
                "traceparent",
                _TRACEPARENT,
            ),
        ],
    )


def _refuse(
    record: Record,
    exception: Exception,
    /,
) -> bool:
    return False


def _accept(
    record: Record,
    exception: Exception,
    /,
) -> bool:
    return True


@final
class TestConsume:
    async def test_each_record_is_handled_in_its_context_and_then_committed(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
                _record(
                    1,
                    b"second",
                ),
            ),
            end_offset=2,
        )

        seen: list[tuple[bytes, str | None, int]] = []

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            seen.append(
                (
                    value,
                    current_correlation_id(),
                    get_current_span(
                        context=context,
                    )
                    .get_span_context()
                    .trace_id,
                ),
            )

        await consume(
            consumer=consumer,
            handoff=PartitionHandoff(),
            handler=handler,
            should_continue=lambda: bool(
                consumer.pending,
            ),
            on_failure=_refuse,
        )

        assert seen == [
            (
                b"first",
                "bar",
                _TRACE_ID,
            ),
            (
                b"second",
                "bar",
                _TRACE_ID,
            ),
        ]

        partition = TopicPartition(
            topic=_TOPIC,
            partition=0,
        )

        assert consumer.committed == [
            {
                partition: 1,
            },
            {
                partition: 2,
            },
        ]

        assert current_correlation_id() is None

    async def test_a_poison_record_is_committed_when_the_caller_says_so(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    None,
                ),
                _record(
                    1,
                    b"second",
                ),
            ),
            end_offset=2,
        )

        handled: list[bytes] = []

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            handled.append(
                value,
            )

        failures: list[tuple[int, str]] = []

        def on_failure(
            record: Record,
            exception: Exception,
            /,
        ) -> bool:
            failures.append(
                (
                    record.offset,
                    type(
                        exception,
                    ).__name__,
                ),
            )

            return True

        await consume(
            consumer=consumer,
            handoff=PartitionHandoff(),
            handler=handler,
            should_continue=lambda: bool(
                consumer.pending,
            ),
            on_failure=on_failure,
        )

        assert handled == [
            b"second",
        ]

        assert failures == [
            (
                0,
                "ValueError",
            ),
        ]

        partition = TopicPartition(
            topic=_TOPIC,
            partition=0,
        )

        assert consumer.committed == [
            {
                partition: 1,
            },
            {
                partition: 2,
            },
        ]

    async def test_a_failure_the_caller_refuses_propagates_uncommitted(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
            ),
            end_offset=1,
        )

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            raise RuntimeError(
                "cannot handle",
            )

        with raises(
            expected_exception=RuntimeError,
            match="cannot handle",
        ):
            await consume(
                consumer=consumer,
                handoff=PartitionHandoff(),
                handler=handler,
                should_continue=lambda: bool(
                    consumer.pending,
                ),
                on_failure=_refuse,
            )

        assert consumer.committed == []

        assert current_correlation_id() is None

    async def test_a_stopped_loop_reads_nothing(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
            ),
            end_offset=1,
        )

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            raise AssertionError(
                "the handler must not run",
            )

        await consume(
            consumer=consumer,
            handoff=PartitionHandoff(),
            handler=handler,
            should_continue=lambda: False,
            on_failure=_accept,
        )

        assert (
            len(
                consumer.pending,
            )
            == 1
        )


@final
class TestRebalance:
    async def test_a_record_whose_partition_was_revoked_is_left_for_redelivery(
        self,
        *,
        caplog: LogCaptureFixture,
    ) -> None:
        consumer = RevokingConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
                _record(
                    1,
                    b"second",
                ),
            ),
            refusal=IllegalStateError(
                f"Partition {_PARTITION} is not assigned",
            ),
            holds_partition=False,
        )

        handled: list[bytes] = []

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            handled.append(
                value,
            )

        with caplog.at_level(
            level=WARNING,
            logger="wordwinnow.messaging.consumer",
        ):
            await consume(
                consumer=consumer,
                handoff=PartitionHandoff(),
                handler=handler,
                should_continue=lambda: bool(
                    consumer.pending,
                ),
                on_failure=_refuse,
            )

        assert handled == [
            b"first",
            b"second",
        ]

        assert consumer.committed == [
            {
                _PARTITION: 2,
            },
        ]

        assert tuple(
            (
                record.getMessage(),
                vars(
                    record,
                )["offset"],
                vars(
                    record,
                )["reason"],
            )
            for record in caplog.records
        ) == (
            (
                "message.commit_lost",
                0,
                "IllegalStateError",
            ),
        )

    async def test_a_commit_the_group_refused_is_left_for_redelivery(
        self,
    ) -> None:
        consumer = RevokingConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
                _record(
                    1,
                    b"second",
                ),
            ),
            refusal=CommitFailedError(
                "the group has already rebalanced",
            ),
            holds_partition=True,
        )

        handled: list[bytes] = []

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            handled.append(
                value,
            )

        await consume(
            consumer=consumer,
            handoff=PartitionHandoff(),
            handler=handler,
            should_continue=lambda: bool(
                consumer.pending,
            ),
            on_failure=_refuse,
        )

        assert handled == [
            b"first",
            b"second",
        ]

        assert consumer.committed == [
            {
                _PARTITION: 2,
            },
        ]

    async def test_a_partition_still_held_is_not_mistaken_for_a_lost_one(
        self,
    ) -> None:
        consumer = RevokingConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
            ),
            refusal=IllegalStateError(
                "No partitions assigned",
            ),
            holds_partition=True,
        )

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            return

        with raises(
            expected_exception=IllegalStateError,
        ):
            await consume(
                consumer=consumer,
                handoff=PartitionHandoff(),
                handler=handler,
                should_continue=lambda: bool(
                    consumer.pending,
                ),
                on_failure=_refuse,
            )

        assert consumer.committed == []


@final
class TestPartitionHandoff:
    async def test_a_revocation_waits_until_the_record_in_hand_is_committed(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
            ),
            end_offset=1,
        )

        handoff = PartitionHandoff()

        started = Event()

        release = Event()

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            started.set()

            await release.wait()

        loop = create_task(
            coro=consume(
                consumer=consumer,
                handoff=handoff,
                handler=handler,
                should_continue=lambda: bool(
                    consumer.pending,
                ),
                on_failure=_refuse,
            ),
        )

        await started.wait()

        committed_when_revoked: list[list[Mapping[TopicPartition, int]]] = []

        async def revoke() -> None:
            await handoff.on_partitions_revoked(
                frozenset(
                    {
                        _PARTITION,
                    },
                ),
            )

            committed_when_revoked.append(
                list(
                    consumer.committed,
                ),
            )

        revocation = create_task(
            coro=revoke(),
        )

        await sleep(
            delay=0,
        )

        assert not revocation.done()

        release.set()

        await loop

        await revocation

        assert committed_when_revoked == [
            [
                {
                    _PARTITION: 1,
                },
            ],
        ]

    async def test_a_revocation_between_records_goes_ahead_at_once(
        self,
    ) -> None:
        revocation = create_task(
            coro=PartitionHandoff().on_partitions_revoked(
                frozenset(
                    {
                        _PARTITION,
                    },
                ),
            ),
        )

        await sleep(
            delay=0,
        )

        assert revocation.done()

    async def test_a_record_whose_handling_failed_releases_a_revocation(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
            ),
            end_offset=1,
        )

        handoff = PartitionHandoff()

        started = Event()

        release = Event()

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            started.set()

            await release.wait()

            raise RuntimeError(
                "cannot handle",
            )

        loop = create_task(
            coro=consume(
                consumer=consumer,
                handoff=handoff,
                handler=handler,
                should_continue=lambda: bool(
                    consumer.pending,
                ),
                on_failure=_refuse,
            ),
        )

        await started.wait()

        revocation = create_task(
            coro=handoff.on_partitions_revoked(
                frozenset(
                    {
                        _PARTITION,
                    },
                ),
            ),
        )

        await sleep(
            delay=0,
        )

        assert not revocation.done()

        release.set()

        with raises(
            expected_exception=RuntimeError,
            match="cannot handle",
        ):
            await loop

        await sleep(
            delay=0,
        )

        assert revocation.done()

        assert consumer.committed == []


@final
class TestTopicLag:
    async def test_lag_is_the_end_offset_minus_the_position(
        self,
    ) -> None:
        consumer = FakeConsumer(
            records=(
                _record(
                    0,
                    b"first",
                ),
            ),
            end_offset=5,
        )

        assert await topic_lag(
            consumer=consumer,
        ) == {
            (
                _TOPIC,
                0,
            ): 5,
        }

        await consumer.getone()

        assert await topic_lag(
            consumer=consumer,
        ) == {
            (
                _TOPIC,
                0,
            ): 4,
        }
