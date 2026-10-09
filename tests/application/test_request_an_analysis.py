"""
Requesting an analysis: it is stored, then its request is published.
"""

from datetime import (
    timedelta,
)
from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    InMemoryStorage,
    RaisingPublisher,
    RecordingPublisher,
)
from wordwinnow.application.errors import (
    AnalysisNotPublishedError,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.application.use_cases.requeue_stale_analyses import (
    requeue_stale_analyses,
)
from wordwinnow.application.use_cases.show_an_analysis import (
    show_an_analysis,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisEvent,
    AnalysisOptions,
    AnalysisRequested,
    AnalysisStatus,
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

_DOCUMENT: Final = Document(
    title="a-scandal-in-bohemia",
    text="Irene Adler kept the photograph.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)

_PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)


@final
class DefectivePublisher:
    """
    Fails every publish with a defect of its own rather than an unreachable
    broker.
    """

    async def publish(
        self,
        *,
        analysis: Analysis,
        event: AnalysisEvent,
    ) -> None:
        raise ValueError(
            "the event could not be encoded",
        )


@final
class TestRequestAnAnalysis:
    async def test_the_analysis_is_stored_and_its_request_is_published(
        self,
    ) -> None:
        storage = InMemoryStorage()

        publisher = RecordingPublisher()

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        stored = await show_an_analysis(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
        )

        assert stored.status is AnalysisStatus.REQUESTED

        assert publisher.published == [
            (
                analysis.id,
                AnalysisRequested(
                    analysis_id=analysis.id,
                    occurred_at=EPOCH,
                ),
            ),
        ]

    async def test_an_unreachable_broker_leaves_the_analysis_requested_for_the_requeue(
        self,
    ) -> None:
        storage = InMemoryStorage()

        with raises(
            expected_exception=AnalysisNotPublishedError,
            match="MessagingUnavailableError",
        ):
            await request_an_analysis(
                document=_DOCUMENT,
                profile=_PROFILE,
                options=AnalysisOptions(),
                new_unit_of_work=storage.new_unit_of_work,
                publisher=RaisingPublisher(),
                clock=FakeClock(
                    at=EPOCH,
                ),
            )

        (stored,) = storage.analyses.values()

        assert stored.status is AnalysisStatus.REQUESTED

        requeued = await requeue_stale_analyses(
            older_than_seconds=0,
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    hours=1,
                ),
            ),
        )

        assert requeued == (stored.id,)

    async def test_a_defect_in_publishing_is_not_reported_as_an_unreachable_broker(
        self,
    ) -> None:
        with raises(
            expected_exception=ValueError,
            match="could not be encoded",
        ):
            await request_an_analysis(
                document=_DOCUMENT,
                profile=_PROFILE,
                options=AnalysisOptions(),
                new_unit_of_work=InMemoryStorage().new_unit_of_work,
                publisher=DefectivePublisher(),
                clock=FakeClock(
                    at=EPOCH,
                ),
            )
