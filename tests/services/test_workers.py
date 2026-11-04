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
    AsyncGenerator,
    Callable,
    Coroutine,
    Mapping,
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from contextlib import (
    asynccontextmanager,
)
from logging import (
    WARNING,
)
from types import (
    SimpleNamespace,
)
from typing import (
    Any,
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
    LogCaptureFixture,
    MonkeyPatch,
    fixture,
    mark,
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
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services import (
    workers,
)
from wordwinnow.services.workers import (
    LagObserver,
    request_stop,
    run_aggregation_worker,
    run_analysis_worker,
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
        self.pending: Final = list(
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
            (_PARTITION,),
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
            (_PARTITION,),
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


@final
class StoppedError(
    Exception,
):
    """
    Ends a worker where the test has seen what it came for.
    """


@final
class ServedMetrics:
    """
    A worker's metrics that serve nothing and remember where they were told
    to.
    """

    def __init__(
        self,
    ) -> None:
        self.served: Final[list[tuple[int, str]]] = []

    def serve(
        self,
        *,
        port: int,
        address: str,
    ) -> None:
        self.served.append(
            (
                port,
                address,
            ),
        )


@final
class Reached:
    """
    What a worker reached past its check of the listening address, and the
    metrics it served.
    """

    def __init__(
        self,
    ) -> None:
        self.names: Final[list[str]] = []

        self.metrics: Final = ServedMetrics()


@fixture
def reached(
    *,
    monkeypatch: MonkeyPatch,
) -> Reached:
    """
    The workers with every store and the broker replaced by a double that
    notes it was reached, and `StoppedError` raised at the first topic.
    """

    seen = Reached()

    def configure_process(
        *,
        settings: Settings,
        service_name: str,
        tracing: bool = True,
        default_log_level: str = "INFO",
    ) -> None:
        return None

    @asynccontextmanager
    async def worker_profile(
        *,
        settings: Settings,
    ) -> AsyncGenerator[SimpleNamespace]:
        seen.names.append(
            "profile",
        )

        yield SimpleNamespace(
            metrics=seen.metrics,
        )

    def build_facts(
        *,
        settings: Settings,
    ) -> SimpleNamespace:
        seen.names.append(
            "facts",
        )

        async def ensure_schema() -> None:
            raise StoppedError

        return SimpleNamespace(
            ensure_schema=ensure_schema,
        )

    def build_metrics_double() -> ServedMetrics:
        seen.names.append(
            "metrics",
        )

        return seen.metrics

    async def ensure_topics(
        *,
        topics: Sequence[str],
        partitions: int,
        bootstrap_servers: str,
    ) -> None:
        seen.names.append(
            "topics",
        )

        raise StoppedError

    for (
        name,
        value,
    ) in (
        (
            "configure_process",
            configure_process,
        ),
        (
            "worker_profile",
            worker_profile,
        ),
        (
            "build_facts",
            build_facts,
        ),
        (
            "build_metrics",
            build_metrics_double,
        ),
        (
            "ensure_topics",
            ensure_topics,
        ),
    ):
        monkeypatch.setattr(
            target=workers,
            name=name,
            value=value,
        )

    return seen


_WORKERS: Final[tuple[tuple[Callable[..., Coroutine[Any, Any, None]], str], ...]] = (
    (
        run_analysis_worker,
        "the analysis worker's metrics",
    ),
    (
        run_aggregation_worker,
        "the aggregation worker's metrics",
    ),
)


@final
class TestTheMetricsAddress:
    @mark.parametrize(
        argnames=(
            "run",
            "service",
        ),
        argvalues=_WORKERS,
    )
    async def test_an_address_beyond_loopback_outside_the_stack_ends_the_worker_before_it_reaches_anything(
        self,
        *,
        run: Callable[..., Coroutine[Any, Any, None]],
        service: str,
        reached: Reached,
        caplog: LogCaptureFixture,
    ) -> None:
        with (
            caplog.at_level(
                level=WARNING,
            ),
            raises(
                expected_exception=SystemExit,
            ) as ended,
        ):
            await run(
                settings=Settings(
                    listen_address="0.0.0.0",
                    compose_stack=False,
                ),
            )

        assert ended.value.code == 1

        assert reached.names == []

        assert reached.metrics.served == []

        (record,) = caplog.records

        assert record.getMessage() == "listener.refused"

        assert (
            vars(
                record,
            )["service"]
            == service
        )

        assert (
            vars(
                record,
            )["port"]
            == 9464
        )

    @mark.parametrize(
        argnames=(
            "run",
            "service",
        ),
        argvalues=_WORKERS,
    )
    @mark.parametrize(
        argnames=(
            "address",
            "in_the_stack",
        ),
        argvalues=(
            (
                "127.0.0.1",
                False,
            ),
            (
                "::1",
                False,
            ),
            (
                "0.0.0.0",
                True,
            ),
        ),
    )
    async def test_a_worker_serves_its_metrics_on_the_address_the_settings_name_and_logs_no_warning(
        self,
        *,
        run: Callable[..., Coroutine[Any, Any, None]],
        service: str,
        address: str,
        in_the_stack: bool,
        reached: Reached,
        caplog: LogCaptureFixture,
    ) -> None:
        with (
            caplog.at_level(
                level=WARNING,
            ),
            raises(
                expected_exception=StoppedError,
            ),
        ):
            await run(
                settings=Settings(
                    listen_address=address,
                    compose_stack=in_the_stack,
                    metrics_port=9465,
                ),
            )

        assert reached.metrics.served == [
            (
                9465,
                address,
            ),
        ]

        assert caplog.records == []
