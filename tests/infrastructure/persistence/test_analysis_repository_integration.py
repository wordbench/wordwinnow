"""
The analysis repository against PostgreSQL: the server `make up` starts, or
the one `WORDWINNOW_TEST_POSTGRES_URL` names, holding a throwaway database.

Two saves of one analysis at the same time end with the last one's items,
never with both sets, and a worker's report that arrives while the analysis is
being saved as completed never writes over the completion.
"""

from asyncio import (
    create_task,
    sleep,
)
from datetime import (
    timedelta,
)
from os import (
    environ,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
    skip,
)
from sqlalchemy import (
    select,
    text,
)
from sqlalchemy.engine import (
    make_url,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
)

from tests.infrastructure.persistence.conftest import (
    EPOCH,
    GROOM,
    RESOLUTE,
    TIMINGS,
    assert_same_state,
    completed_analysis,
    create_schema,
    load_analysis,
    processing_analysis,
    store_analysis,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.infrastructure.persistence.engine import (
    build_engine,
)
from wordwinnow.infrastructure.persistence.models import (
    AnalysisRow,
    Base,
)
from wordwinnow.infrastructure.persistence.uow import (
    new_unit_of_work_factory,
)

# NOTE:
# The server `make up` starts, or another one named in the environment; the test creates its throwaway database on the
# maintenance connection and skips when no server answers.
_POSTGRES_URL: Final = environ.get(
    key="WORDWINNOW_TEST_POSTGRES_URL",
    default="postgresql+asyncpg://wordwinnow:wordwinnow@127.0.0.1:5432/wordwinnow",
)


_TEST_DATABASE: Final = "wordwinnow_test"


async def _throwaway_database() -> str:
    url = make_url(
        name_or_url=_POSTGRES_URL,
    )

    maintenance = create_async_engine(
        url=url,
        isolation_level="AUTOCOMMIT",
    )

    try:
        async with maintenance.connect() as connection:
            exists = await connection.scalar(
                statement=text(
                    text="SELECT 1 FROM pg_database WHERE datname = :name",
                ),
                parameters={
                    "name": _TEST_DATABASE,
                },
            )

            if not exists:
                await connection.execute(
                    statement=text(
                        text=f"CREATE DATABASE {_TEST_DATABASE}",
                    ),
                )

    except OSError as exception:
        skip(
            reason=f"no PostgreSQL server answers on {url.host}:{url.port}; run `make up` ({exception})",
        )

    finally:
        await maintenance.dispose()

    # WARN:
    # `str()` on a URL hides the password, so the engine would authenticate with three asterisks.
    return url.set(
        database=_TEST_DATABASE,
    ).render_as_string(
        hide_password=False,
    )


@final
class TestPostgreSql:
    @mark.integration
    async def test_a_completed_analysis_comes_back_as_it_went_in(
        self,
    ) -> None:
        engine = build_engine(
            database_url=await _throwaway_database(),
        )

        try:
            await create_schema(
                engine=engine,
            )

            try:
                new_unit_of_work = new_unit_of_work_factory(
                    engine=engine,
                )

                analysis = completed_analysis()

                await store_analysis(
                    analysis=analysis,
                    new_unit_of_work=new_unit_of_work,
                )

                assert_same_state(
                    original=analysis,
                    loaded=await load_analysis(
                        analysis_id=analysis.id,
                        new_unit_of_work=new_unit_of_work,
                    ),
                )

            finally:
                async with engine.begin() as connection:
                    await connection.run_sync(
                        fn=Base.metadata.drop_all,
                    )

        finally:
            await engine.dispose()

    @mark.integration
    async def test_two_saves_of_one_analysis_at_once_keep_only_the_last_ones_items(
        self,
    ) -> None:
        engine = build_engine(
            database_url=await _throwaway_database(),
        )

        try:
            await create_schema(
                engine=engine,
            )

            try:
                new_unit_of_work = new_unit_of_work_factory(
                    engine=engine,
                )

                stored = processing_analysis()

                await store_analysis(
                    analysis=stored,
                    new_unit_of_work=new_unit_of_work,
                )

                # NOTE:
                # Two workers that both took the same request each load the analysis and finish it their own way.
                first = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                second = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert first is not None

                assert second is not None

                first.complete(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                    items=(GROOM,),
                    stage_timings=TIMINGS,
                )

                second.complete(
                    at=EPOCH
                    + timedelta(
                        seconds=3,
                    ),
                    items=(RESOLUTE,),
                    stage_timings=TIMINGS,
                )

                async with new_unit_of_work() as first_uow:
                    await first_uow.analyses.save(
                        analysis=first,
                    )

                    async def save_second() -> None:
                        async with new_unit_of_work() as second_uow:
                            await second_uow.analyses.save(
                                analysis=second,
                            )

                            await second_uow.commit()

                    saving = create_task(
                        coro=save_second(),
                    )

                    # NOTE:
                    # Long enough for the second save to reach the database and wait on the first one's transaction.
                    await sleep(
                        delay=0.5,
                    )

                    assert not saving.done()

                    await first_uow.commit()

                await saving

                loaded = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert loaded is not None

                assert tuple(item.item.lemma for item in loaded.items) == ("resolute",)

            finally:
                async with engine.begin() as connection:
                    await connection.run_sync(
                        fn=Base.metadata.drop_all,
                    )

        finally:
            await engine.dispose()

    @mark.integration
    async def test_a_report_that_arrives_during_the_completion_does_not_write_over_it(
        self,
    ) -> None:
        engine = build_engine(
            database_url=await _throwaway_database(),
        )

        try:
            await create_schema(
                engine=engine,
            )

            try:
                new_unit_of_work = new_unit_of_work_factory(
                    engine=engine,
                )

                stored = processing_analysis()

                await store_analysis(
                    analysis=stored,
                    new_unit_of_work=new_unit_of_work,
                )

                finished = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert finished is not None

                finished.complete(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                    items=(GROOM,),
                    stage_timings=TIMINGS,
                )

                async with new_unit_of_work() as completing:
                    await completing.analyses.save(
                        analysis=finished,
                    )

                    async def report() -> None:
                        async with new_unit_of_work() as reporting:
                            await reporting.analyses.record_progress(
                                analysis_id=stored.id,
                                progress=ProcessingProgress(
                                    stage=Stage.DICTIONARY_ENRICHMENT,
                                    steps=1,
                                    done=1,
                                    unavailable=0,
                                    dictionary_paused=False,
                                    reported_at=EPOCH,
                                ),
                            )

                            await reporting.commit()

                    reporting = create_task(
                        coro=report(),
                    )

                    # NOTE:
                    # Long enough for the report to reach the database and wait on the completion's transaction.
                    await sleep(
                        delay=0.5,
                    )

                    assert not reporting.done()

                    await completing.commit()

                await reporting

                async with engine.connect() as connection:
                    cleared = await connection.scalar(
                        statement=select(
                            AnalysisRow.progress.is_(
                                None,
                            ),
                        ).where(
                            AnalysisRow.id
                            == str(
                                object=stored.id,
                            ),
                        ),
                    )

                loaded = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert loaded is not None

                assert (
                    loaded.status,
                    cleared,
                ) == (
                    "completed",
                    True,
                )

            finally:
                async with engine.begin() as connection:
                    await connection.run_sync(
                        fn=Base.metadata.drop_all,
                    )

        finally:
            await engine.dispose()
