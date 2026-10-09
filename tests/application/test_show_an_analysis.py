"""
Reading an analysis back, and how far a worker has come with it.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from tests.fakes.ports import (
    EPOCH,
    InMemoryStorage,
)
from tests.infrastructure.messaging.analyses import (
    requested_analysis,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
)
from wordwinnow.application.use_cases.show_an_analysis import (
    show_an_analysis,
    show_progress,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)


@final
class TestShowAnAnalysis:
    async def test_an_unknown_id_is_a_not_found_error(
        self,
    ) -> None:
        with raises(
            expected_exception=AnalysisNotFoundError,
        ):
            await show_an_analysis(
                analysis_id=AnalysisId.new(),
                new_unit_of_work=InMemoryStorage().new_unit_of_work,
            )

    async def test_progress_is_what_the_worker_last_reported_and_none_before_it_reports(
        self,
    ) -> None:
        storage = InMemoryStorage()

        analysis = requested_analysis()

        analysis.pull_events()

        analysis.start(
            at=EPOCH,
        )

        report = ProcessingProgress(
            stage=Stage.LEVEL_ASSESSMENT,
            steps=None,
            done=0,
            unavailable=0,
            dictionary_paused=False,
            reported_at=EPOCH,
        )

        async with storage.new_unit_of_work() as uow:
            await uow.analyses.add(
                analysis=analysis,
            )

            await uow.commit()

        before = await show_progress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
        )

        async with storage.new_unit_of_work() as uow:
            await uow.analyses.record_progress(
                analysis_id=analysis.id,
                progress=report,
            )

            await uow.commit()

        after = await show_progress(
            analysis_id=analysis.id,
            new_unit_of_work=storage.new_unit_of_work,
        )

        assert (
            before,
            after,
        ) == (
            None,
            report,
        )
