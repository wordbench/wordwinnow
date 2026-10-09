"""
What the metrics record about a finished analysis.
"""

from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import (
    Final,
    final,
)

from wordwinnow.domain.analysis import (
    AnalysisOptions,
    Stage,
    StageTiming,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.infrastructure.observability.metrics import (
    build_metrics,
)

_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)


@final
class TestMetrics:
    def test_a_finished_analysis_feeds_the_status_stage_and_wait_metrics(
        self,
    ) -> None:
        metrics = build_metrics()

        analysis = request_an_analysis(
            document=Document(
                title="a-scandal-in-bohemia",
                text="Irene Adler kept the photograph.",
                origin=DocumentOrigin.CUSTOM_TEXT,
                reference="a-scandal-in-bohemia.txt",
            ),
            profile=LearnerProfile(
                level=CefrLevel.B1,
                target_level=CefrLevel.B2,
            ),
            options=AnalysisOptions(),
            at=_EPOCH,
        )

        analysis.start(
            at=_EPOCH
            + timedelta(
                seconds=3,
            ),
        )

        analysis.complete(
            at=_EPOCH
            + timedelta(
                seconds=4,
            ),
            items=(),
            stage_timings=(
                StageTiming(
                    stage=Stage.WINNOWING,
                    seconds=0.25,
                ),
            ),
        )

        metrics.observe_analysis(
            analysis=analysis,
        )

        rendered = metrics.render().decode()

        assert 'wordwinnow_analyses_finished_total{status="completed"} 1.0' in rendered

        assert 'wordwinnow_analysis_stage_seconds_count{stage="winnowing"} 1.0' in rendered

        assert "wordwinnow_analysis_queue_wait_seconds_sum 3.0" in rendered

    def test_every_value_a_counters_labels_can_take_starts_at_zero(
        self,
    ) -> None:
        rendered = build_metrics().render().decode()

        assert (
            tuple(
                series
                for series in (
                    'wordwinnow_analyses_requested_total{origin="custom_text"} 0.0',
                    'wordwinnow_analyses_requested_total{origin="new_york_times"} 0.0',
                    'wordwinnow_analyses_finished_total{status="completed"} 0.0',
                    'wordwinnow_analyses_finished_total{status="failed"} 0.0',
                    'wordwinnow_dictionary_wait_seconds_total{cause="budget"} 0.0',
                    'wordwinnow_dictionary_wait_seconds_total{cause="provider"} 0.0',
                    'wordwinnow_dictionary_wait_seconds_total{cause="retry"} 0.0',
                    'wordwinnow_dictionary_wait_seconds_total{cause="trial"} 0.0',
                    'wordwinnow_dictionary_retries_total{reason="timed_out"} 0.0',
                    'wordwinnow_dictionary_retries_total{reason="transport_error"} 0.0',
                    'wordwinnow_dictionary_retries_total{reason="rate_limited"} 0.0',
                    'wordwinnow_dictionary_retries_total{reason="provider_error"} 0.0',
                    'wordwinnow_messages_skipped_total{topic="wordwinnow.analysis.requested.v1"} 0.0',
                    'wordwinnow_messages_skipped_total{topic="wordwinnow.analysis.completed.v1"} 0.0',
                )
                if series not in rendered
            )
            == ()
        )
