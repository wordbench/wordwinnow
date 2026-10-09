"""
Expired analyses are deleted, and only those.
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
    RecordingPublisher,
)
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
)
from wordwinnow.application.use_cases.purge_expired_analyses import (
    purge_expired_analyses,
)
from wordwinnow.application.use_cases.request_an_analysis import (
    request_an_analysis,
)
from wordwinnow.application.use_cases.show_an_analysis import (
    show_an_analysis,
)
from wordwinnow.domain.analysis import (
    AnalysisOptions,
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

_PROFILE: Final = LearnerProfile(
    level=CefrLevel.B1,
    target_level=CefrLevel.B2,
)

_OWN: Final = Document(
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


@final
class TestPurgeExpiredAnalyses:
    async def test_the_expired_borrowed_text_goes_and_the_learner_s_own_stays(
        self,
    ) -> None:
        storage = InMemoryStorage()

        publisher = RecordingPublisher()

        own = await request_an_analysis(
            document=_OWN,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        borrowed = await request_an_analysis(
            document=_BORROWED,
            profile=_PROFILE,
            options=AnalysisOptions(),
            new_unit_of_work=storage.new_unit_of_work,
            publisher=publisher,
            clock=FakeClock(
                at=EPOCH,
            ),
        )

        untouched = await purge_expired_analyses(
            new_unit_of_work=storage.new_unit_of_work,
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    hours=23,
                ),
            ),
        )

        assert untouched == 0

        deleted = await purge_expired_analyses(
            new_unit_of_work=storage.new_unit_of_work,
            clock=FakeClock(
                at=EPOCH
                + timedelta(
                    hours=24,
                ),
            ),
        )

        assert deleted == 1

        with raises(
            expected_exception=AnalysisNotFoundError,
        ):
            await show_an_analysis(
                analysis_id=borrowed.id,
                new_unit_of_work=storage.new_unit_of_work,
            )

        assert (
            await show_an_analysis(
                analysis_id=own.id,
                new_unit_of_work=storage.new_unit_of_work,
            )
        ).id == own.id
