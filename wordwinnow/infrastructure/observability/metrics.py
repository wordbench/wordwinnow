"""
The Prometheus metrics the processes expose.

Each metric answers a question an operator or the experiments ask: how many
analyses arrive and finish, how long each stage takes, how long an analysis
waited in the queue, how the dictionary and its provider behave, how far a
worker lags behind its topic, and how many records it had to skip.

A counter whose labels take a fixed set of values starts at zero for each, so
a dashboard tells a process that has counted nothing from one that is not
scraped.
"""

from dataclasses import (
    dataclass,
)
from typing import (
    Final,
    final,
)

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.exposition import (
    start_http_server,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    RETRYABLE_REASONS,
    WaitCause,
)
from wordwinnow.infrastructure.messaging.topics import (
    ALL_TOPICS,
)

# NOTE:
# Buckets from a fast local stage to a slow provider call, in seconds.
_STAGE_BUCKETS: Final = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
)

_WAIT_BUCKETS: Final = (
    0.1,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Metrics:
    """
    The metric objects of one process, all registered on `registry`.
    """

    registry: CollectorRegistry

    analyses_requested: Counter

    analyses_finished: Counter

    analysis_stage_seconds: Histogram

    analysis_queue_wait_seconds: Histogram

    dictionary_lookups: Counter

    dictionary_provider_request_seconds: Histogram

    dictionary_wait_seconds: Counter

    dictionary_retries: Counter

    dictionary_circuit_state: Gauge

    consumer_lag: Gauge

    messages_skipped: Counter

    facts_recorded: Counter

    def observe_analysis(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        """
        Record what a finished analysis measured about itself.
        """

        self.analyses_finished.labels(
            status=analysis.status,
        ).inc()

        wait = analysis.queue_wait_seconds

        if wait is not None:
            self.analysis_queue_wait_seconds.observe(
                amount=wait,
            )

        if analysis.status is not AnalysisStatus.COMPLETED:
            return

        for timing in analysis.stage_timings:
            self.analysis_stage_seconds.labels(
                stage=timing.stage,
            ).observe(
                amount=timing.seconds,
            )

    def render(
        self,
    ) -> bytes:
        """
        The registry in the Prometheus exposition format.
        """

        return generate_latest(
            registry=self.registry,
        )

    def serve(
        self,
        *,
        port: int,
    ) -> None:
        """
        Expose the registry over HTTP from a background thread, for a worker
        that has no HTTP server of its own.
        """

        start_http_server(
            port=port,
            registry=self.registry,
        )


def build_metrics(
    *,
    registry: CollectorRegistry | None = None,
) -> Metrics:
    """
    Register every metric on a registry, a fresh one unless given.
    """

    target = registry if registry is not None else CollectorRegistry()

    metrics = Metrics(
        registry=target,
        analyses_requested=Counter(
            name="wordwinnow_analyses_requested_total",
            documentation="Analyses requested, by document origin.",
            labelnames=("origin",),
            registry=target,
        ),
        analyses_finished=Counter(
            name="wordwinnow_analyses_finished_total",
            documentation="Analyses that reached a terminal status, counted once each.",
            labelnames=("status",),
            registry=target,
        ),
        analysis_stage_seconds=Histogram(
            name="wordwinnow_analysis_stage_seconds",
            documentation="Time spent in each pipeline stage.",
            labelnames=("stage",),
            registry=target,
            buckets=_STAGE_BUCKETS,
        ),
        analysis_queue_wait_seconds=Histogram(
            name="wordwinnow_analysis_queue_wait_seconds",
            documentation="Time between an analysis being requested and a worker starting it.",
            registry=target,
            buckets=_WAIT_BUCKETS,
        ),
        dictionary_lookups=Counter(
            name="wordwinnow_dictionary_lookups_total",
            documentation="Dictionary lookups, by outcome, failure reason, and whether the cache answered.",
            labelnames=(
                "outcome",
                "reason",
                "served_from_cache",
            ),
            registry=target,
        ),
        dictionary_provider_request_seconds=Histogram(
            name="wordwinnow_dictionary_provider_request_seconds",
            documentation="Round trips to the dictionary provider, by HTTP status class.",
            labelnames=("status_class",),
            registry=target,
            buckets=_STAGE_BUCKETS,
        ),
        dictionary_wait_seconds=Counter(
            name="wordwinnow_dictionary_wait_seconds_total",
            documentation=(
                "Seconds dictionary lookups spent waiting, by what for: the request budget, the provider's answer, "
                "or the pause before a retry."
            ),
            labelnames=("cause",),
            registry=target,
        ),
        dictionary_retries=Counter(
            name="wordwinnow_dictionary_retries_total",
            documentation="Requests the dictionary client sent again, by why the one before failed.",
            labelnames=("reason",),
            registry=target,
        ),
        dictionary_circuit_state=Gauge(
            name="wordwinnow_dictionary_circuit_state",
            documentation="The dictionary circuit's state: 1 for the one it is in, 0 for the others.",
            labelnames=("state",),
            registry=target,
        ),
        consumer_lag=Gauge(
            name="wordwinnow_consumer_lag_messages",
            documentation="Messages between this consumer's position and the end of the partition.",
            labelnames=(
                "topic",
                "partition",
            ),
            registry=target,
        ),
        messages_skipped=Counter(
            name="wordwinnow_messages_skipped_total",
            documentation="Records a worker committed past because they could not be handled.",
            labelnames=("topic",),
            registry=target,
        ),
        facts_recorded=Counter(
            name="wordwinnow_facts_recorded_total",
            documentation="Vocabulary facts written to the fact store.",
            registry=target,
        ),
    )

    # NOTE:
    # A labeled counter has no series until a first increment creates one, and a panel over a counter without a series
    # says "No data", which reads the same as a counter nobody scrapes; creating a series is what starts it at zero.
    for origin in DocumentOrigin:
        metrics.analyses_requested.labels(
            origin=origin,
        )

    for status in (
        AnalysisStatus.COMPLETED,
        AnalysisStatus.FAILED,
    ):
        metrics.analyses_finished.labels(
            status=status,
        )

    for cause in WaitCause:
        metrics.dictionary_wait_seconds.labels(
            cause=cause,
        )

    for reason in RETRYABLE_REASONS:
        metrics.dictionary_retries.labels(
            reason=reason,
        )

    for topic in ALL_TOPICS:
        metrics.messages_skipped.labels(
            topic=topic,
        )

    return metrics


def status_class_of(
    *,
    status_code: int,
) -> str:
    """
    The label for an HTTP status: `2xx`, `4xx`, `5xx`.
    """

    return f"{status_code // 100}xx"
