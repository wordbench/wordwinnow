"""
The two Kafka workers: one runs the pipeline, one keeps the facts.

Both share one loop shape: consume, handle, commit; a transient failure stops
the process so Compose restarts it from the last committed offset, and a
poison record is logged, counted, and committed past.
"""

from asyncio import (
    CancelledError,
    Event,
    Task,
    create_task,
    get_running_loop,
)
from collections.abc import (
    Callable,
)
from contextlib import (
    AsyncExitStack,
)
from dataclasses import (
    replace,
)
from logging import (
    getLogger,
)
from signal import (
    SIGINT,
    SIGTERM,
)
from time import (
    monotonic,
)
from typing import (
    Final,
    final,
)

from aiokafka.errors import (
    KafkaError,
)
from opentelemetry.context import (
    Context,
    attach,
    detach,
)
from sqlalchemy.exc import (
    InterfaceError,
    OperationalError,
)

from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    FactStoreUnavailableError,
    MessagingUnavailableError,
)
from wordwinnow.application.use_cases.process_an_analysis import (
    process_an_analysis,
)
from wordwinnow.domain.analysis import (
    AnalysisStatus,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.composition import (
    build_facts,
    configure_process,
    worker_profile,
)
from wordwinnow.infrastructure.messaging.codec import (
    decode,
    facts_from,
)
from wordwinnow.infrastructure.messaging.consumer import (
    FailureHandler,
    MessageConsumer,
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
    AnalysisRequestedV1,
)
from wordwinnow.infrastructure.messaging.topics import (
    ALL_TOPICS,
    ANALYSIS_COMPLETED_V1,
    ANALYSIS_REQUESTED_V1,
)
from wordwinnow.infrastructure.observability.metrics import (
    Metrics,
    build_metrics,
)
from wordwinnow.infrastructure.observability.tracing import (
    tracer,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.progress import (
    StoredProgress,
)

_logger: Final = getLogger(
    name="wordwinnow.workers",
)

# NOTE:
# The requested-analyses topic has this many partitions, which is the most workers that can share it.
PARTITIONS: Final = 4

# NOTE:
# How often a worker asks the broker how far behind it is; every message would be a round trip per message.
LAG_INTERVAL_SECONDS: Final = 5.0

# NOTE:
# Failures that a restart may cure: a store or a broker that is not there right now.
_TRANSIENT: Final = (
    OperationalError,
    InterfaceError,
    OSError,
    MessagingUnavailableError,
    FactStoreUnavailableError,
)


def _is_transient(
    exception: Exception,
    /,
) -> bool:
    return isinstance(
        exception,
        _TRANSIENT,
    )


def _stop_on_signal(
    callback: Callable[[], None],
    /,
) -> None:
    loop = get_running_loop()

    for signal_number in (
        SIGINT,
        SIGTERM,
    ):
        loop.add_signal_handler(
            sig=signal_number,
            callback=callback,
        )


def request_stop(
    *,
    stop: Event,
    handoff: PartitionHandoff,
    consuming: Task[None],
) -> None:
    """
    Stop a worker's loop without losing the record it holds.

    A worker holding a record finishes it and stops at the loop's next check;
    one between records is waiting for a record that may never come, so its
    wait is canceled.
    """

    stop.set()

    if handoff.between_records:
        consuming.cancel()


@final
class LagObserver:
    """
    Refreshes the consumer-lag gauge at most once per interval.

    A reading the broker cannot give, such as one asked for while the group
    rebalances, is skipped: the gauge is telemetry, and it never changes what
    happens to the record just handled.
    """

    def __init__(
        self,
        *,
        consumer: MessageConsumer,
        metrics: Metrics,
    ) -> None:
        self._consumer: Final = consumer

        self._metrics: Final = metrics

        self._observed_at = -LAG_INTERVAL_SECONDS

    async def observe(
        self,
    ) -> None:
        now = monotonic()

        if now - self._observed_at < LAG_INTERVAL_SECONDS:
            return

        self._observed_at = now

        try:
            lags = await topic_lag(
                consumer=self._consumer,
            )

        except KafkaError as exception:
            _logger.debug(
                msg="consumer.lag_unavailable",
                extra={
                    "reason": type(
                        exception,
                    ).__name__,
                },
            )

            return

        for (
            (
                topic,
                partition,
            ),
            lag,
        ) in lags.items():
            self._metrics.consumer_lag.labels(
                topic=topic,
                partition=str(
                    object=partition,
                ),
            ).set(
                value=lag,
            )


def _failure_policy(
    metrics: Metrics,
    skipped_event: str,
    /,
) -> FailureHandler:
    def on_failure(
        record: Record,
        exception: Exception,
        /,
    ) -> bool:
        if _is_transient(
            exception,
        ):
            _logger.error(
                msg="worker.stopping",
                extra={
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                    "reason": str(
                        object=exception,
                    ),
                },
            )

            return False

        metrics.messages_skipped.labels(
            topic=record.topic,
        ).inc()

        _logger.warning(
            msg=skipped_event,
            extra={
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
                "reason": str(
                    object=exception,
                ),
            },
        )

        return True

    return on_failure


async def run_analysis_worker(
    *,
    settings: Settings,
) -> None:
    """
    Process requested analyses until told to stop.
    """

    tracing = configure_process(
        settings=settings,
        service_name="wordwinnow-analysis-worker",
    )

    stop = Event()

    _stop_on_signal(
        stop.set,
    )

    async with AsyncExitStack() as stack:
        profile = await stack.enter_async_context(
            cm=worker_profile(
                settings=settings,
            ),
        )

        profile.metrics.serve(
            port=settings.metrics_port,
        )

        await ensure_topics(
            topics=ALL_TOPICS,
            partitions=PARTITIONS,
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )

        handoff = PartitionHandoff()

        consumer = build_consumer(
            topics=(ANALYSIS_REQUESTED_V1,),
            group_id=settings.kafka_consumer_group,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            handoff=handoff,
        )

        await consumer.start()

        stack.push_async_callback(
            consumer.stop,
        )

        lag = LagObserver(
            consumer=consumer,
            metrics=profile.metrics,
        )

        span_tracer = tracer(
            name="wordwinnow.workers",
        )

        async def handle(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            message = decode(
                data=value,
                model=AnalysisRequestedV1,
            )

            token = attach(
                context=context,
            )

            analysis_id = AnalysisId(
                value=message.analysis_id,
            )

            try:
                with span_tracer.start_as_current_span(
                    name="analysis.process",
                ):
                    try:
                        # NOTE:
                        # The pipeline reports into the analysis store as it goes, so the command waiting for this
                        # analysis shows the stage and the count rather than only that a worker has it.
                        async with StoredProgress(
                            analysis_id=analysis_id,
                            new_unit_of_work=profile.new_unit_of_work,
                            clock=profile.clock,
                        ) as progress:
                            processed = await process_an_analysis(
                                analysis_id=analysis_id,
                                pipeline=replace(
                                    profile.pipeline,
                                    progress=progress,
                                ),
                                new_unit_of_work=profile.new_unit_of_work,
                                publisher=profile.publisher,
                                clock=profile.clock,
                            )

                    except AnalysisNotFoundError:
                        raise

                    except Exception:
                        profile.metrics.analyses_finished.labels(
                            status=AnalysisStatus.FAILED,
                        ).inc()

                        raise

                if not processed.redelivered:
                    profile.metrics.observe_analysis(
                        analysis=processed.analysis,
                    )

                _logger.info(
                    msg="analysis.processed",
                    extra={
                        "analysis_id": str(
                            object=processed.analysis.id,
                        ),
                        "status": processed.analysis.status,
                        "items": len(
                            processed.analysis.items,
                        ),
                        "queue_wait_seconds": processed.analysis.queue_wait_seconds,
                        "redelivered": processed.redelivered,
                    },
                )

            finally:
                detach(
                    token=token,
                )

                await lag.observe()

        _logger.info(
            msg="worker.started",
            extra={
                "topic": ANALYSIS_REQUESTED_V1,
                "group": settings.kafka_consumer_group,
            },
        )

        consuming = create_task(
            coro=consume(
                consumer=consumer,
                handoff=handoff,
                handler=handle,
                should_continue=lambda: not stop.is_set(),
                on_failure=_failure_policy(
                    profile.metrics,
                    "analysis.skipped",
                ),
            ),
        )

        _stop_on_signal(
            lambda: request_stop(
                stop=stop,
                handoff=handoff,
                consuming=consuming,
            ),
        )

        try:
            await consuming

        except CancelledError:
            pass

        finally:
            if tracing is not None:
                tracing.shutdown()


async def run_aggregation_worker(
    *,
    settings: Settings,
) -> None:
    """
    Turn completed analyses into facts until told to stop.
    """

    tracing = configure_process(
        settings=settings,
        service_name="wordwinnow-aggregation-worker",
    )

    stop = Event()

    _stop_on_signal(
        stop.set,
    )

    metrics = build_metrics()

    metrics.serve(
        port=settings.metrics_port,
    )

    facts = build_facts(
        settings=settings,
    )

    await facts.ensure_schema()

    async with AsyncExitStack() as stack:
        await ensure_topics(
            topics=ALL_TOPICS,
            partitions=PARTITIONS,
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )

        handoff = PartitionHandoff()

        consumer = build_consumer(
            topics=(ANALYSIS_COMPLETED_V1,),
            group_id=f"{settings.kafka_consumer_group}-aggregation",
            bootstrap_servers=settings.kafka_bootstrap_servers,
            handoff=handoff,
        )

        await consumer.start()

        stack.push_async_callback(
            consumer.stop,
        )

        lag = LagObserver(
            consumer=consumer,
            metrics=metrics,
        )

        span_tracer = tracer(
            name="wordwinnow.workers",
        )

        async def handle(
            value: bytes,
            context: Context,
            /,
        ) -> None:
            message = decode(
                data=value,
                model=AnalysisCompletedV1,
            )

            token = attach(
                context=context,
            )

            try:
                with span_tracer.start_as_current_span(
                    name="facts.record",
                ):
                    recorded = facts_from(
                        message=message,
                    )

                    await facts.record(
                        facts=recorded,
                    )

                metrics.facts_recorded.inc(
                    amount=len(
                        recorded,
                    ),
                )

                _logger.info(
                    msg="facts.recorded",
                    extra={
                        "analysis_id": str(
                            object=message.analysis_id,
                        ),
                        "facts": len(
                            recorded,
                        ),
                    },
                )

            finally:
                detach(
                    token=token,
                )

                await lag.observe()

        _logger.info(
            msg="worker.started",
            extra={
                "topic": ANALYSIS_COMPLETED_V1,
            },
        )

        consuming = create_task(
            coro=consume(
                consumer=consumer,
                handoff=handoff,
                handler=handle,
                should_continue=lambda: not stop.is_set(),
                on_failure=_failure_policy(
                    metrics,
                    "facts.skipped",
                ),
            ),
        )

        _stop_on_signal(
            lambda: request_stop(
                stop=stop,
                handoff=handoff,
                consuming=consuming,
            ),
        )

        try:
            await consuming

        except CancelledError:
            pass

        finally:
            if tracing is not None:
                tracing.shutdown()
