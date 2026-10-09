"""
The engine: a SQLite file gets its directory and its foreign keys, and an
error names its statement without the values it carried.
"""

from pathlib import (
    Path,
)
from typing import (
    final,
)

from pytest import (
    raises,
)
from sqlalchemy import (
    delete,
    func,
    select,
)
from sqlalchemy.exc import (
    IntegrityError,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
)

from tests.infrastructure.persistence.conftest import (
    NewUnitOfWork,
    completed_analysis,
    requested_analysis,
    store_analysis,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
)
from wordwinnow.infrastructure.persistence.models import (
    AnalysisRow,
    VocabularyItemRow,
)


@final
class TestEngine:
    async def test_a_sqlite_file_gets_its_parent_directory(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "nested" / "deeper" / "wordwinnow.db"

        engine = build_engine(
            database_url=f"sqlite+aiosqlite:///{path}",
        )

        try:
            assert path.parent.is_dir()

        finally:
            await engine.dispose()

    async def test_deleting_an_analysis_row_cascades_to_its_items_on_sqlite(
        self,
        *,
        engine: AsyncEngine,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = completed_analysis()

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        async with engine.begin() as connection:
            await connection.execute(
                statement=delete(
                    table=AnalysisRow,
                ).where(
                    AnalysisRow.id
                    == str(
                        object=analysis.id,
                    ),
                ),
            )

        async with engine.connect() as connection:
            remaining = await connection.scalar(
                statement=select(
                    func.count(
                        VocabularyItemRow.id,
                    ),
                ),
            )

        assert remaining == 0

    async def test_a_failed_write_names_its_statement_without_the_text_it_carried(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = requested_analysis()

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        with raises(
            expected_exception=IntegrityError,
        ) as raised:
            await store_analysis(
                analysis=analysis,
                new_unit_of_work=new_unit_of_work,
            )

        message = str(
            object=raised.value,
        )

        assert "INSERT INTO analyses" in message

        assert "Irene Adler" not in message
