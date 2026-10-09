"""
Recovering an analysis stranded between its request and its result.
"""

from datetime import (
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

from tests.fakes.ports import (
    EPOCH,
    FakeClock,
    InMemoryStorage,
    RaisingPublisher,
    RecordingPublisher,
)
from wordwinnow.application.errors import (
    MessagingUnavailableError,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.application.use_cases.requeue_stale_analyses import (
    requeue_stale_analyses,
)
from wordwinnow.domain.analysis import (
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
from wordwinnow.domain.identifiers import (
    AnalysisId,
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


async def _started(
    storage: InMemoryStorage,
    /,
    *,
    requested_at: datetime,
    started_at: datetime,
) -> AnalysisId:
    analysis = await request_an_analysis(
        document=_DOCUMENT,
        profile=_PROFILE,
        options=AnalysisOptions(),
        new_unit_of_work=storage.new_unit_of_work,
        publisher=RecordingPublisher(),
        clock=FakeClock(
            at=requested_at,
        ),
    )

    async with storage.new_unit_of_work() as uow:
        stored = await uow.analyses.get(
            analysis_id=analysis.id,
        )

        assert stored is not None

        stored.start(
            at=started_at,
        )

        await uow.analyses.save(
            analysis=stored,
        )

        await uow.commit()

    return analysis.id


@final
class TestRequeueStaleAnalyses:
    async def test_a_stale_request_is_republished(
        self,
    ) -> None:
        storage = InMemoryStorage()

        stale = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        publisher = RecordingPublisher()

        requeued = await requeue_stale_analyses(
            older_than_seconds=60,
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    hours=1,
                ),
            ),
        )

        assert requeued == (stale.id,)

        assert tuple(
            analysis_id
            for (
                analysis_id,
                _,
            ) in publisher.published
        ) == (stale.id,)

    async def test_a_stale_processing_analysis_is_failed_reopened_and_republished(
        self,
    ) -> None:
        storage = InMemoryStorage()

        analysis = await request_an_analysis(
            document=_DOCUMENT,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=RecordingPublisher(),
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        async with storage.new_unit_of_work() as uow:
            stored = await uow.analyses.get(
                analysis_id=analysis.id,
            )

            assert stored is not None

            stored.start(
                at=EPOCH,
            )

            await uow.analyses.save(
                analysis=stored,
            )

            await uow.commit()

        publisher = RecordingPublisher()

        requeued = await requeue_stale_analyses(
            older_than_seconds=60,
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    hours=1,
                ),
            ),
        )

        assert requeued == (analysis.id,)

        assert storage.analyses[analysis.id].status is AnalysisStatus.REQUESTED

        assert tuple(
            type(
                event,
            )
            for (
                _,
                event,
            ) in publisher.published
        ) == (AnalysisRequested,)

    async def test_a_processing_analysis_is_stale_by_how_long_it_has_been_processing(
        self,
    ) -> None:
        storage = InMemoryStorage()

        analysis_id = await _started(
            storage,
            requested_at=EPOCH,
            started_at=EPOCH
            + timedelta(
                minutes=59,
            ),
        )

        publisher = RecordingPublisher()

        requeued = await requeue_stale_analyses(
            older_than_seconds=600,
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    hours=1,
                ),
            ),
        )

        assert requeued == ()

        assert publisher.published == []

        assert storage.analyses[analysis_id].status is AnalysisStatus.PROCESSING

    async def test_the_store_is_committed_before_anything_is_published(
        self,
    ) -> None:
        storage = InMemoryStorage()

        analysis_id = await _started(
            storage,
            requested_at=EPOCH,
            started_at=EPOCH,
        )

        with raises(
            expected_exception=MessagingUnavailableError,
        ):
            await requeue_stale_analyses(
                older_than_seconds=60,
                new_unit_of_work=storage.new_unit_of_work,
                publisher=RaisingPublisher(),
                clock=FakeClock(
                    at=EPOCH
                    + timedelta(
                        hours=1,
                    ),
                ),
            )

        assert storage.analyses[analysis_id].status is AnalysisStatus.REQUESTED
