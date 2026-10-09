"""
Stopping a worker: at once when it waits for a record, and only after the
record it holds is committed otherwise.

Reading the consumer lag never changes what happens to a record.
"""

from asyncio import (
    CancelledError,
    Event,
    create_task,
    sleep,
    wait_for,
)
from collections.abc import (
    Mapping,
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
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
    IllegalStateError,
)
from opentelemetry.context import (
    Context,
)
from pytest import (
    raises,
)

from wordwinnow.infrastructure.messaging.consumer import (
    Record,
    consume,
)
from wordwinnow.infrastructure.messaging.handoff import (
    PartitionHandoff,
)
from wordwinnow.infrastructure.messaging.topics import (
    ANALYSIS_REQUESTED_V1,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)
from wordwinnow.services.workers import (
    LagObserver,
    request_stop,
)

_PARTITION: Final = TopicPartition(
    topic=ANALYSIS_REQUESTED_V1,
    partition=0,
)


@final
class WaitingConsumer:
    """
    Serves the records it is given, then waits for one that never comes, as an
    idle worker's consumer does.
    """

    def __init__(
        self,
        *,
        records: Sequence[Record],
    ) -> None:
        self.pending: Final[list[Record]] = list(
            records,
        )

        self.committed: Final[list[Mapping[TopicPartition, int]]] = []

    async def getone(
        self,
    ) -> Record:
        if self.pending:
            return self.pending.pop(
                0,
            )

        await Event().wait()

        raise AssertionError(
            "no record ever arrives",
        )

    async def commit(
        self,
        *,
        offsets: Mapping[TopicPartition, int],
    ) -> None:
        self.committed.append(
            offsets,
        )

    def assignment(
        self,
    ) -> AbstractSet[TopicPartition]:
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


@final
class LaggingConsumer:
    """
    Holds one partition five records from its end, or has lost it by the time
    its position is asked for, as aiokafka reports it during a rebalance.
    """

    def __init__(
        self,
        *,
        lost: bool,
    ) -> None:
        self._lost: Final = lost

    async def getone(
        self,
    ) -> Record:
        raise AssertionError(
            "the lag is read without reading records",
        )

    async def commit(
        self,
        *,
        offsets: Mapping[TopicPartition, int],
    ) -> None:
        raise AssertionError(
            "the lag is read without committing",
        )

    def assignment(
        self,
    ) -> AbstractSet[TopicPartition]:
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
        # WARN:
        # `dict.fromkeys` takes its arguments positionally only.
        return dict.fromkeys(
            partitions,
            5,
        )

    async def position(
        self,
        *,
        partition: TopicPartition,
    ) -> int:
        if self._lost:
            raise IllegalStateError(
                f"Partition {partition} is not assigned",
            )

        return 0


def _record(
    value: bytes,
    /,
) -> Record:
    return ConsumerRecord(
        topic=_PARTITION.topic,
        partition=_PARTITION.partition,
        offset=0,
        timestamp=0,
        timestamp_type=0,
        key=b"bar",
        value=value,
        checksum=None,
        serialized_key_size=3,
        serialized_value_size=0,
        headers=[],
    )


def _refuse(
    record: Record,
    exception: Exception,
    /,
) -> bool:
    return False


@final
class TestRequestStop:
    async def test_a_worker_waiting_for_a_record_stops_at_once(
        self,
    ) -> None:
        consumer = WaitingConsumer(
            records=(),
        )

        handoff = PartitionHandoff()

        stop = Event()

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            raise AssertionError(
                "no record is handled",
            )

        consuming = create_task(
            coro=consume(
                consumer=consumer,
                handoff=handoff,
                handler=handler,
                should_continue=lambda: not stop.is_set(),
                on_failure=_refuse,
            ),
        )

        await sleep(
            delay=0,
        )

        request_stop(
            stop=stop,
            handoff=handoff,
            consuming=consuming,
        )

        # NOTE:
        # A loop that ignored the stop would wait forever, so the wait is bounded and a timeout fails the test.
        with raises(
            expected_exception=CancelledError,
        ):
            await wait_for(
                fut=consuming,
                timeout=5,
            )

        assert stop.is_set()

    async def test_a_worker_holding_a_record_commits_it_before_it_stops(
        self,
    ) -> None:
        consumer = WaitingConsumer(
            records=(
                _record(
                    b"first",
                ),
            ),
        )

        handoff = PartitionHandoff()

        stop = Event()

        started = Event()

        release = Event()

        async def handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            started.set()

            await release.wait()

        consuming = create_task(
            coro=consume(
                consumer=consumer,
                handoff=handoff,
                handler=handler,
                should_continue=lambda: not stop.is_set(),
                on_failure=_refuse,
            ),
        )

        await started.wait()

        request_stop(
            stop=stop,
            handoff=handoff,
            consuming=consuming,
        )

        release.set()

        await consuming

        assert not consuming.cancelled()

        assert consumer.committed == [
            {
                _PARTITION: 1,
            },
        ]


@final
class TestLagObserver:
    async def test_the_lag_of_each_partition_is_the_gauge(
        self,
    ) -> None:
        metrics = build_metrics()

        await LagObserver(
            consumer=LaggingConsumer(
                lost=False,
            ),
            metrics=metrics,
        ).observe()

        assert (
            metrics.registry.get_sample_value(
                name="wordwinnow_consumer_lag_messages",
                labels={
                    "topic": _PARTITION.topic,
                    "partition": "0",
                },
            )
            == 5
        )

    async def test_a_lag_the_broker_cannot_give_is_skipped(
        self,
    ) -> None:
        metrics = build_metrics()

        await LagObserver(
            consumer=LaggingConsumer(
                lost=True,
            ),
            metrics=metrics,
        ).observe()

        assert (
            metrics.registry.get_sample_value(
                name="wordwinnow_consumer_lag_messages",
                labels={
                    "topic": _PARTITION.topic,
                    "partition": "0",
                },
            )
            is None
        )
