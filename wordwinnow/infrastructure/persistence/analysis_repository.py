"""
The analysis repository over one SQLAlchemy session.
"""

from datetime import (
    datetime,
)
from typing import (
    Final,
    final,
)

from sqlalchemy import (
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.persistence.mapping import (
    analysis_from_rows,
    analysis_to_row,
    item_rows_of,
    progress_from_json,
    progress_to_json,
    summary_from_row,
    utc_of,
)
from wordwinnow.infrastructure.persistence.models import (
    AnalysisRow,
    VocabularyItemRow,
)


@final
class SqlAlchemyAnalysisRepository:
    """
    Analyses stored in the two tables of the analysis store.

    Satisfies `AnalysisRepository` structurally.

    Nothing is written until the session flushes, so a unit of work that ends
    without a commit leaves the tables as it found them.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
    ) -> None:
        self._session: Final = session

    async def add(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        self._session.add(
            instance=analysis_to_row(
                analysis=analysis,
            ),
        )

        self._session.add_all(
            instances=item_rows_of(
                analysis=analysis,
            ),
        )

    async def get(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> Analysis | None:
        row = await self._session.get(
            entity=AnalysisRow,
            ident=str(
                object=analysis_id,
            ),
        )

        if row is None:
            return None

        return analysis_from_rows(
            row=row,
            item_rows=await self._item_rows_of(
                row.id,
            ),
        )

    async def save(
        self,
        *,
        analysis: Analysis,
    ) -> None:
        """
        Store the current state of an analysis, replacing its item rows.
        """

        # WARN:
        # The order keeps two saves of one analysis apart on PostgreSQL: the delete flushes the merged row first,
        # which locks it, so a second save waits for the first to commit and then deletes that one's items rather than
        # adding its own beside them; the PostgreSQL integration tests prove it.
        await self._session.merge(
            instance=analysis_to_row(
                analysis=analysis,
            ),
        )

        await self._session.execute(
            statement=(
                delete(
                    table=VocabularyItemRow,
                ).where(
                    VocabularyItemRow.analysis_id
                    == str(
                        object=analysis.id,
                    ),
                )
            ),
        )

        self._session.add_all(
            instances=item_rows_of(
                analysis=analysis,
            ),
        )

    async def record_progress(
        self,
        *,
        analysis_id: AnalysisId,
        progress: ProcessingProgress,
    ) -> None:
        # WARN:
        # The status is part of the condition rather than read first: a worker's last report can arrive after the
        # analysis was saved as completed, and the condition is what keeps it from writing over that save.
        await self._session.execute(
            statement=(
                update(
                    table=AnalysisRow,
                )
                .where(
                    AnalysisRow.id
                    == str(
                        object=analysis_id,
                    ),
                    AnalysisRow.status == AnalysisStatus.PROCESSING,
                )
                .values(
                    progress=progress_to_json(
                        progress=progress,
                    ),
                )
            ),
        )

    async def progress_of(
        self,
        *,
        analysis_id: AnalysisId,
    ) -> ProcessingProgress | None:
        payload = (
            await self._session.execute(
                statement=select(
                    AnalysisRow.progress,
                ).where(
                    AnalysisRow.id
                    == str(
                        object=analysis_id,
                    ),
                    AnalysisRow.status == AnalysisStatus.PROCESSING,
                ),
            )
        ).scalar_one_or_none()

        if payload is None:
            return None

        return progress_from_json(
            payload=payload,
        )

    async def list_recent(
        self,
        *,
        limit: int,
    ) -> tuple[AnalysisSummary, ...]:
        item_count = (
            select(
                func.count(
                    VocabularyItemRow.id,
                ),
            )
            .where(
                VocabularyItemRow.analysis_id == AnalysisRow.id,
            )
            .correlate(
                AnalysisRow,
            )
            .scalar_subquery()
        )

        result = await self._session.execute(
            statement=(
                select(
                    AnalysisRow,
                    item_count,
                )
                .order_by(
                    AnalysisRow.requested_at.desc(),
                )
                .limit(
                    limit=limit,
                )
            ),
        )

        return tuple(
            summary_from_row(
                row=row,
                item_count=count,
            )
            for (
                row,
                count,
            ) in result
        )

    async def list_stale(
        self,
        *,
        stale_before: datetime,
    ) -> tuple[Analysis, ...]:
        threshold = utc_of(
            value=stale_before,
        )

        result = await self._session.execute(
            statement=(
                select(
                    AnalysisRow,
                )
                .where(
                    or_(
                        and_(
                            AnalysisRow.status == AnalysisStatus.REQUESTED,
                            AnalysisRow.requested_at < threshold,
                        ),
                        and_(
                            AnalysisRow.status == AnalysisStatus.PROCESSING,
                            AnalysisRow.started_at < threshold,
                        ),
                    ),
                )
                .order_by(
                    AnalysisRow.requested_at,
                )
            ),
        )

        # NOTE:
        # A requested or processing analysis has no items: only `complete()` sets them, and `reconstitute()` drops
        # them for any other status, so the item tables are not read for these rows.
        return tuple(
            analysis_from_rows(
                row=row,
                item_rows=(),
            )
            for row in result.scalars()
        )

    async def delete_expired(
        self,
        *,
        now: datetime,
    ) -> int:
        expired = tuple(
            (
                await self._session.execute(
                    statement=select(
                        AnalysisRow.id,
                    ).where(
                        AnalysisRow.expires_at.is_not(
                            other=None,
                        ),
                        AnalysisRow.expires_at
                        <= utc_of(
                            value=now,
                        ),
                    ),
                )
            ).scalars(),
        )

        if not expired:
            return 0

        # NOTE:
        # The item rows go with their analysis through the foreign key's cascade, which the SQLite connection enables
        # on connect.
        await self._session.execute(
            statement=delete(
                table=AnalysisRow,
            ).where(
                AnalysisRow.id.in_(
                    other=expired,
                ),
            ),
        )

        return len(
            expired,
        )

    async def _item_rows_of(
        self,
        analysis_id: str,
        /,
    ) -> tuple[VocabularyItemRow, ...]:
        result = await self._session.execute(
            statement=(
                select(
                    VocabularyItemRow,
                )
                .where(
                    VocabularyItemRow.analysis_id == analysis_id,
                )
                .order_by(
                    VocabularyItemRow.position,
                )
            ),
        )

        return tuple(
            result.scalars(),
        )
