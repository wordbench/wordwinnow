"""
Where analyses are kept between the request and the report.
"""

from datetime import (
    datetime,
)
from typing import (
    Protocol,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    Analysis,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)


class AnalysisRepository(
    Protocol,
):
    """
    Stores analyses and finds them again.
    """

    async def add(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        """
        Store a new analysis.
        """

        ...

    async def get(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> Analysis | None:
        """
        The analysis with this id, or `None` when there is none.
        """

        ...

    async def save(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        """
        Store the current state of an analysis that was already added.
        """

        ...

    async def record_progress(
        self,
        *,
        analysis_id: AnalysisId,
        progress: ProcessingProgress,
    ) -> None:
        """
        Keep how far processing has come, for an analysis still processing;
        any other analysis is left as it is.

        Saving an analysis forgets its progress, so a finished analysis never
        carries any.
        """

        ...

    async def progress_of(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> ProcessingProgress | None:
        """
        How far processing has come, for an analysis still processing that a
        worker has reported on, or `None`.
        """

        ...

    async def list_recent(
        self,
        *,
        limit: int,
    ) -> tuple[AnalysisSummary, ...]:
        """
        The most recently requested analyses, newest first.
        """

        ...

    async def list_stale(
        self,
        *,
        stale_before: datetime,
    ) -> tuple[Analysis, ...]:
        """
        Analyses still requested that were requested before the instant given,
        and analyses still processing that started before it.
        """

        ...

    async def delete_expired(
        self,
        *,
        now: datetime,
    ) -> int:
        """
        Delete every analysis whose text may no longer be kept, with its
        items, and say how many there were.
        """

        ...
