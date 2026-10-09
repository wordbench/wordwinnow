"""
The publisher and the consumer loop against a real broker: a record published
with a trace reaches a handler with that trace and its analysis bound, and its
offset is committed.

A consumer that joins the group while another is handling a record takes its
partitions only after that record is committed, so the record is handled once.

The broker is the one `make up` starts, and the tests skip when none answers.
"""

from asyncio import (
    CancelledError,
    Event,
    Task,
    create_task,
    sleep,
    wait_for,
)
from asyncio import (
    run as run_coroutine,
)
from contextlib import (
    AsyncExitStack,
    suppress,
)
from logging import (
    INFO,
)
from os import (
    environ,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)
from uuid import (
    uuid4,
)

from aiokafka import (
    AIOKafkaConsumer,
    TopicPartition,
)
from opentelemetry.context import (
    Context,
)
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    get_current_span,
    use_span,
)
from pytest import (
    LogCaptureFixture,
    fixture,
    mark,
    skip,
)

from tests.infrastructure.messaging.analyses import (
    completed_analysis,
)
from wordwinnow.application.dto import (
    facts_of,
)
from wordwinnow.application.errors import (
    MessagingUnavailableError,
)
from wordwinnow.infrastructure.log_payload import (
    current_correlation_id,
)
from wordwinnow.infrastructure.messaging.codec import (
    decode,
    facts_from,
)
from wordwinnow.infrastructure.messaging.consumer import (
    Record,
    build_consumer,
    consume,
    ensure_topics,
    topic_lag,
)
from wordwinnow.infrastructure.messaging.handoff import (
    PartitionHandoff,
)
from wordwinnow.infrastructure.messaging.messages import (
    AnalysisCompletedV1,
)
from wordwinnow.infrastructure.messaging.publisher import (
    KafkaAnalysisEventPublisher,
    build_producer,
)
from wordwinnow.infrastructure.messaging.topics import (
    ALL_TOPICS,
    ANALYSIS_COMPLETED_V1,
)

# NOTE:
# The broker `make up` starts, or another one named in the environment; the test skips when none answers.
_BOOTSTRAP_SERVERS: Final = environ.get(
    key="WORDWINNOW_TEST_KAFKA_BOOTSTRAP_SERVERS",
    default="127.0.0.1:9092",
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

# NOTE:
# A topic of the tests' own, with a partition for each of the two consumers, so the records they read are theirs.
_REBALANCE_TOPIC: Final = "wordwinnow.test.rebalance"

_REBALANCE_PARTITIONS: Final = 2

# NOTE:
# A member notices a rebalance at its next heartbeat, three seconds apart by default, and a join waits for every
# member, so each step is given ten heartbeats.
_STEP_SECONDS: Final = 30.0


@fixture(
    scope="module",
)
def kafka() -> None:
    try:
        run_coroutine(
            main=ensure_topics(
                topics=ALL_TOPICS,
                partitions=1,
                bootstrap_servers=_BOOTSTRAP_SERVERS,
            ),
        )

    except MessagingUnavailableError:
        skip(
            reason=f"no Kafka broker answers on {_BOOTSTRAP_SERVERS}; run `make up`",
        )


def _refuse(
    record: Record,
    exception: Exception,
    /,
) -> bool:
    return False


async def _logged(
    caplog: LogCaptureFixture,
    message: str,
    /,
) -> None:
    while not any(record.getMessage() == message for record in caplog.records):
        await sleep(
            delay=0.1,
        )


async def _split_and_caught_up(
    first: AIOKafkaConsumer,
    second: AIOKafkaConsumer,
    /,
) -> None:
    while True:
        first_assigned = first.assignment()

        second_assigned = second.assignment()

        split = (
            bool(
                first_assigned,
            )
            and bool(
                second_assigned,
            )
            and not first_assigned & second_assigned
            and len(
                first_assigned | second_assigned,
            )
            == _REBALANCE_PARTITIONS
        )

        if split:
            lag = MappingProxyType(
                mapping={
                    **await topic_lag(
                        consumer=first,
                    ),
                    **await topic_lag(
                        consumer=second,
                    ),
                },
            )

            if all(value == 0 for value in lag.values()):
                return

        await sleep(
            delay=0.1,
        )


@final
class TestKafka:
    @mark.integration
    async def test_a_published_completion_is_consumed_with_its_context_and_committed(
        self,
        *,
        kafka: None,
    ) -> None:
        await ensure_topics(
            topics=ALL_TOPICS,
            partitions=1,
            bootstrap_servers=_BOOTSTRAP_SERVERS,
        )

        group_id = f"foo-{uuid4().hex}"

        handoff = PartitionHandoff()

        consumer = build_consumer(
            topics=(ANALYSIS_COMPLETED_V1,),
            group_id=group_id,
            bootstrap_servers=_BOOTSTRAP_SERVERS,
            handoff=handoff,
        )

        await consumer.start()

        producer = build_producer(
            bootstrap_servers=_BOOTSTRAP_SERVERS,
        )

        await producer.start()

        try:
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
                await KafkaAnalysisEventPublisher(
                    producer=producer,
                ).publish(
                    analysis=analysis,
                    event=event,
                )

            received: list[tuple[AnalysisCompletedV1, str | None, int]] = []

            async def handler(
                value: bytes,
                context: Context,
                /,
            ) -> None:
                received.append(
                    (
                        decode(
                            data=value,
                            model=AnalysisCompletedV1,
                        ),
                        current_correlation_id(),
                        get_current_span(
                            context=context,
                        )
                        .get_span_context()
                        .trace_id,
                    ),
                )

            # NOTE:
            # A shared broker may hold records from earlier runs ahead of this one, so the loop reads until it meets
            # the record it published.
            await consume(
                consumer=consumer,
                handoff=handoff,
                handler=handler,
                should_continue=lambda: (
                    not any(
                        message.analysis_id == analysis.id.value
                        for (
                            message,
                            _,
                            _,
                        ) in received
                    )
                ),
                on_failure=_refuse,
            )

            (
                message,
                correlation_id,
                trace_id,
            ) = next(entry for entry in received if entry[0].analysis_id == analysis.id.value)

            assert message.analysis_id == analysis.id.value

            assert facts_from(
                message=message,
            ) == facts_of(
                analysis=analysis,
            )

            assert correlation_id == str(
                object=analysis.id,
            )

            assert trace_id == _TRACE_ID

            # NOTE:
            # The record lands on whichever of the topic's partitions its key hashes to, so the assertions cover every
            # partition the consumer read from rather than naming one.
            committed_partitions = 0

            for partition in consumer.assignment():
                committed = await consumer.committed(
                    partition=partition,
                )

                if committed is None:
                    continue

                committed_partitions += 1

                assert committed == await consumer.position(
                    partition=partition,
                )

            assert committed_partitions >= 1

            lag = await topic_lag(
                consumer=consumer,
            )

            assert all(
                topic == ANALYSIS_COMPLETED_V1
                for (
                    topic,
                    _,
                ) in lag
            )

            assert all(value >= 0 for value in lag.values())

        finally:
            await producer.stop()

            await consumer.stop()

    @mark.integration
    async def test_a_record_in_hand_is_committed_before_its_partition_moves_and_is_handled_once(
        self,
        *,
        kafka: None,
        caplog: LogCaptureFixture,
    ) -> None:
        await ensure_topics(
            topics=(_REBALANCE_TOPIC,),
            partitions=_REBALANCE_PARTITIONS,
            bootstrap_servers=_BOOTSTRAP_SERVERS,
        )

        marker = uuid4().bytes

        producer = build_producer(
            bootstrap_servers=_BOOTSTRAP_SERVERS,
        )

        await producer.start()

        try:
            sent = await producer.send_and_wait(
                topic=_REBALANCE_TOPIC,
                value=marker,
            )

        finally:
            await producer.stop()

        group_id = f"foo-{uuid4().hex}"

        first_handoff = PartitionHandoff()

        first = build_consumer(
            topics=(_REBALANCE_TOPIC,),
            group_id=group_id,
            bootstrap_servers=_BOOTSTRAP_SERVERS,
            handoff=first_handoff,
        )

        second_handoff = PartitionHandoff()

        second = build_consumer(
            topics=(_REBALANCE_TOPIC,),
            group_id=group_id,
            bootstrap_servers=_BOOTSTRAP_SERVERS,
            handoff=second_handoff,
        )

        holding = Event()

        release = Event()

        first_seen: list[bytes] = []

        second_seen: list[bytes] = []

        async def first_handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            first_seen.append(
                value,
            )

            if value == marker:
                holding.set()

                await release.wait()

        async def second_handler(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            second_seen.append(
                value,
            )

        tasks: list[Task[None]] = []

        async with AsyncExitStack() as stack:
            await first.start()

            stack.push_async_callback(
                first.stop,
            )

            try:
                first_loop = create_task(
                    coro=consume(
                        consumer=first,
                        handoff=first_handoff,
                        handler=first_handler,
                        should_continue=lambda: True,
                        on_failure=_refuse,
                    ),
                )

                tasks.append(
                    first_loop,
                )

                await wait_for(
                    fut=holding.wait(),
                    timeout=_STEP_SECONDS,
                )

                # NOTE:
                # A consumer subscribed before it starts returns from `start` only once the group has assigned it
                # partitions, which it does after the first consumer rejoins, so the second joins in the background.
                joining = create_task(
                    coro=second.start(),
                )

                tasks.append(
                    joining,
                )

                stack.push_async_callback(
                    second.stop,
                )

                with caplog.at_level(
                    level=INFO,
                    logger="wordwinnow.messaging.handoff",
                ):
                    await wait_for(
                        fut=_logged(
                            caplog,
                            "rebalance.waiting_for_record",
                        ),
                        timeout=_STEP_SECONDS,
                    )

                release.set()

                await wait_for(
                    fut=joining,
                    timeout=_STEP_SECONDS,
                )

                second_loop = create_task(
                    coro=consume(
                        consumer=second,
                        handoff=second_handoff,
                        handler=second_handler,
                        should_continue=lambda: True,
                        on_failure=_refuse,
                    ),
                )

                tasks.append(
                    second_loop,
                )

                await wait_for(
                    fut=_split_and_caught_up(
                        first,
                        second,
                    ),
                    timeout=_STEP_SECONDS,
                )

                assert not first_loop.done()

                assert not second_loop.done()

                assert (
                    first_seen.count(
                        marker,
                    )
                    == 1
                )

                assert marker not in second_seen

                assert (
                    await first.committed(
                        partition=TopicPartition(
                            topic=_REBALANCE_TOPIC,
                            partition=sent.partition,
                        ),
                    )
                    == sent.offset + 1
                )

            finally:
                for task in tasks:
                    task.cancel()

                    with suppress(
                        CancelledError,
                    ):
                        await task
