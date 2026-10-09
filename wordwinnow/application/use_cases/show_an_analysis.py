"""
Read one analysis back, with how far a worker has come with it while it
processes, or list the recent ones.
"""

from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
)
from wordwinnow.application.errors import (
    AnalysisNotFoundError,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)
from wordwinnow.domain.analysis import (
    Analysis,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)


async def show_an_analysis(
    *,
    analysis_id: AnalysisId,
    new_unit_of_work: UnitOfWorkFactory,
) -> Analysis:
    """
    The analysis with this id, whatever its status.
    """

    async with new_unit_of_work() as uow:
        analysis = await uow.analyses.get(
            analysis_id=analysis_id,
        )

    if analysis is None:
        raise AnalysisNotFoundError(
            f"no analysis {analysis_id}",
        )

    return analysis


async def show_progress(
    *,
    analysis_id: AnalysisId,
    new_unit_of_work: UnitOfWorkFactory,
) -> ProcessingProgress | None:
    """
    How far a worker has come with the analysis, while it processes and once
    the worker has said so, or `None`.
    """

    async with new_unit_of_work() as uow:
        return await uow.analyses.progress_of(
            analysis_id=analysis_id,
        )


async def list_analyses(
    *,
    limit: int,
    new_unit_of_work: UnitOfWorkFactory,
) -> tuple[AnalysisSummary, ...]:
    """
    The most recent analyses, newest first.
    """

    async with new_unit_of_work() as uow:
        return await uow.analyses.list_recent(
            limit=limit,
        )
