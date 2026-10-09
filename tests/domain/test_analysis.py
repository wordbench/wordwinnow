"""
Tests for `Analysis`: its lifecycle from the request to the result, the events
it records on the way, and when an analysis of borrowed text must go.
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

from pytest import (
    raises,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisCompleted,
    AnalysisOptions,
    AnalysisRequested,
    AnalysisStatus,
    AnalysisTransitionError,
    InvalidAnalysisError,
    Stage,
    StageTiming,
    reconstitute,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.study import (
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)

_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)

_DOCUMENT: Final = Document(
    title="a-scandal-in-bohemia",
    text="Irene Adler kept the photograph.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)

_BORROWED: Final = Document(
    title="New York Times feed: Science",
    text="A cab waits at the door",
    origin=DocumentOrigin.NEW_YORK_TIMES,
    reference="rss/Science",
    attribution="Data provided by The New York Times",
    retention=timedelta(
        hours=24,
    ),
)

_PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)

_PHOTOGRAPH: Final = StudyItem(
    item=VocabularyItem(
        lemma="photograph",
        part_of_speech=PartOfSpeech.NOUN,
        occurrence_count=1,
        example_sentence="Irene Adler kept the photograph.",
    ),
    zipf_frequency=4.23,
    level=LevelAssessment(
        level=CefrLevel.A2,
        source=LevelSource.REFERENCE_LIST,
    ),
    tier=StudyTier.FOCUS,
    dictionary=None,
    senses=(),
)

_TIMING: Final = StageTiming(
    stage=Stage.WINNOWING,
    seconds=0.5,
)


def _requested() -> Analysis:
    return request_an_analysis(
        document=_DOCUMENT,
        profile=_PROFILE,
        options=AnalysisOptions(),
        at=_EPOCH,
    )


@final
class TestRequestAnAnalysis:
    def test_a_new_analysis_is_requested_and_records_the_request(
        self,
    ) -> None:
        analysis = _requested()

        assert analysis.status is AnalysisStatus.REQUESTED

        assert analysis.pull_events() == (
            AnalysisRequested(
                analysis_id=analysis.id,
                occurred_at=_EPOCH,
            ),
        )

        assert analysis.pull_events() == ()

    def test_a_naive_request_time_is_rejected(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="requested_at",
        ):
            request_an_analysis(
                document=_DOCUMENT,
                profile=_PROFILE,
                options=AnalysisOptions(),
                at=datetime(
                    year=1970,
                    month=1,
                    day=1,
                ),
            )

    def test_identity_is_the_id(
        self,
    ) -> None:
        first = _requested()

        second = _requested()

        assert first != second

        assert first == reconstitute(
            id=first.id,
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            requested_at=_EPOCH,
            status=AnalysisStatus.REQUESTED,
            started_at=None,
            finished_at=None,
            failure_reason=None,
            stage_timings=(),
            items=(),
        )


@final
class TestLifecycle:
    def test_start_then_complete_records_the_result_and_the_completion(
        self,
    ) -> None:
        analysis = _requested()

        analysis.pull_events()

        started = _EPOCH + timedelta(
            seconds=2,
        )

        finished = _EPOCH + timedelta(
            seconds=5,
        )

        analysis.start(
            at=started,
        )

        assert analysis.status is AnalysisStatus.PROCESSING

        assert analysis.queue_wait_seconds == 2.0

        analysis.complete(
            at=finished,
            items=(_PHOTOGRAPH,),
            stage_timings=(_TIMING,),
        )

        assert analysis.status is AnalysisStatus.COMPLETED

        assert analysis.items == (_PHOTOGRAPH,)

        assert analysis.stage_timings == (_TIMING,)

        assert analysis.finished_at == finished

        assert analysis.pull_events() == (
            AnalysisCompleted(
                analysis_id=analysis.id,
                occurred_at=finished,
                item_count=1,
            ),
        )

    def test_a_failure_from_either_live_state_is_recorded_as_state_not_as_an_event(
        self,
    ) -> None:
        requested = _requested()

        requested.fail(
            at=_EPOCH,
            reason="no worker",
        )

        assert requested.status is AnalysisStatus.FAILED

        assert requested.failure_reason == "no worker"

        processing = _requested()

        processing.start(
            at=_EPOCH,
        )

        processing.pull_events()

        processing.fail(
            at=_EPOCH,
            reason="tagger crashed",
        )

        assert processing.status is AnalysisStatus.FAILED

        assert processing.finished_at == _EPOCH

        assert processing.pull_events() == ()

    def test_a_failure_needs_a_reason(
        self,
    ) -> None:
        analysis = _requested()

        with raises(
            expected_exception=InvalidAnalysisError,
        ):
            analysis.fail(
                at=_EPOCH,
                reason="  ",
            )

    def test_a_failed_analysis_can_be_reopened_and_nothing_else_can(
        self,
    ) -> None:
        analysis = _requested()

        analysis.fail(
            at=_EPOCH,
            reason="no worker",
        )

        analysis.pull_events()

        analysis.reopen(
            at=_EPOCH,
        )

        assert analysis.status is AnalysisStatus.REQUESTED

        assert analysis.failure_reason is None

        assert analysis.pull_events() == (
            AnalysisRequested(
                analysis_id=analysis.id,
                occurred_at=_EPOCH,
            ),
        )

        with raises(
            expected_exception=AnalysisTransitionError,
        ):
            analysis.reopen(
                at=_EPOCH,
            )

    def test_the_status_forbids_out_of_order_transitions(
        self,
    ) -> None:
        analysis = _requested()

        with raises(
            expected_exception=AnalysisTransitionError,
        ):
            analysis.complete(
                at=_EPOCH,
                items=(),
                stage_timings=(),
            )

        analysis.start(
            at=_EPOCH,
        )

        with raises(
            expected_exception=AnalysisTransitionError,
        ):
            analysis.start(
                at=_EPOCH,
            )

        analysis.complete(
            at=_EPOCH,
            items=(),
            stage_timings=(),
        )

        with raises(
            expected_exception=AnalysisTransitionError,
        ):
            analysis.fail(
                at=_EPOCH,
                reason="too late",
            )


@final
class TestCompletion:
    def test_a_completion_is_the_event_that_completing_recorded(
        self,
    ) -> None:
        analysis = _requested()

        analysis.pull_events()

        analysis.start(
            at=_EPOCH,
        )

        analysis.complete(
            at=_EPOCH
            + timedelta(
                seconds=5,
            ),
            items=(_PHOTOGRAPH,),
            stage_timings=(_TIMING,),
        )

        assert analysis.pull_events() == (analysis.completion(),)

    def test_a_stored_completed_analysis_rebuilds_the_same_completion(
        self,
    ) -> None:
        analysis = reconstitute(
            id=AnalysisId.new(),
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            requested_at=_EPOCH,
            status=AnalysisStatus.COMPLETED,
            started_at=_EPOCH,
            finished_at=_EPOCH,
            failure_reason=None,
            stage_timings=(_TIMING,),
            items=(_PHOTOGRAPH,),
        )

        assert analysis.completion() == AnalysisCompleted(
            analysis_id=analysis.id,
            occurred_at=_EPOCH,
            item_count=1,
        )

    def test_only_a_completed_analysis_has_a_completion(
        self,
    ) -> None:
        with raises(
            expected_exception=AnalysisTransitionError,
            match="only a completed analysis has a completion",
        ):
            _requested().completion()


@final
class TestExpiry:
    def test_an_analysis_of_a_borrowed_text_expires_after_its_retention(
        self,
    ) -> None:
        analysis = request_an_analysis(
            document=_BORROWED,
            profile=_PROFILE,
            options=AnalysisOptions(),
            at=_EPOCH,
        )

        assert analysis.expires_at == _EPOCH + timedelta(
            hours=24,
        )

        assert not analysis.is_expired(
            at=_EPOCH
            + timedelta(
                hours=23,
            ),
        )

        assert analysis.is_expired(
            at=_EPOCH
            + timedelta(
                hours=24,
            ),
        )

    def test_an_analysis_of_the_learner_s_own_text_never_expires(
        self,
    ) -> None:
        analysis = _requested()

        assert analysis.expires_at is None

        assert not analysis.is_expired(
            at=datetime.max.replace(
                tzinfo=UTC,
            ),
        )


@final
class TestReconstitute:
    def test_a_completed_analysis_comes_back_with_its_result_and_no_events(
        self,
    ) -> None:
        analysis = reconstitute(
            id=AnalysisId.new(),
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            requested_at=_EPOCH,
            status=AnalysisStatus.COMPLETED,
            started_at=_EPOCH,
            finished_at=_EPOCH,
            failure_reason=None,
            stage_timings=(_TIMING,),
            items=(_PHOTOGRAPH,),
        )

        assert analysis.status is AnalysisStatus.COMPLETED

        assert analysis.items == (_PHOTOGRAPH,)

        assert analysis.pull_events() == ()

    def test_a_failed_analysis_needs_its_reason(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidAnalysisError,
        ):
            reconstitute(
                id=AnalysisId.new(),
                document=_DOCUMENT,
                profile=_PROFILE,
                options=AnalysisOptions(),
                requested_at=_EPOCH,
                status=AnalysisStatus.FAILED,
                started_at=None,
                finished_at=_EPOCH,
                failure_reason=None,
                stage_timings=(),
                items=(),
            )

    def test_a_processing_analysis_needs_a_start_time(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidAnalysisError,
        ):
            reconstitute(
                id=AnalysisId.new(),
                document=_DOCUMENT,
                profile=_PROFILE,
                options=AnalysisOptions(),
                requested_at=_EPOCH,
                status=AnalysisStatus.PROCESSING,
                started_at=None,
                finished_at=None,
                failure_reason=None,
                stage_timings=(),
                items=(),
            )


@final
class TestStageTiming:
    def test_a_stage_timing_cannot_be_negative(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidAnalysisError,
        ):
            StageTiming(
                stage=Stage.WINNOWING,
                seconds=-1.0,
            )
