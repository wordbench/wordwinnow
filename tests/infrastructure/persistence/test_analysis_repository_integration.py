"""
The analysis repository against PostgreSQL: the server `make up` starts, or
the one `WORDWINNOW_TEST_POSTGRES_URL` names, holding a throwaway database.

Two saves of one analysis at the same time end with the last one's items,
never with both sets, and a worker's report that arrives while the analysis is
being saved as completed never writes over the completion.

A conditional save that meets another run's uncommitted change waits for it
and then tests its condition against the committed row, so a run's start never
lands on a completion and a run's failure never lands on a newer run's start.
"""

from asyncio import (
    create_task,
    sleep,
    wait_for,
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
    AsyncEngine,
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
    AnalysisStatus,
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


async def _until_a_statement_waits_for_a_lock(
    engine: AsyncEngine,
    /,
) -> None:
    # TEST:
    # The test goes on once PostgreSQL reports a statement in the test database waiting for another transaction's
    # lock, so the meeting is forced rather than timed; each poll is a transaction of its own, because the server
    # shows a transaction one snapshot of its activity.
    async with engine.connect() as connection:
        polling = await connection.execution_options(
            isolation_level="AUTOCOMMIT",
        )

        while not await polling.scalar(
            statement=text(
                text=(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                ),
            ),
        ):
            await sleep(
                delay=0.01,
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
    async def test_of_two_runs_finishing_at_once_only_the_first_to_commit_is_stored(
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
                # Two workers that both took the same request each finish it their own way, as two runs do when a
                # second worker takes an analysis the first is still on.
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
                    assert await first_uow.analyses.save_unless_completed(
                        analysis=first,
                    )

                    async def save_second() -> bool:
                        async with new_unit_of_work() as second_uow:
                            saved = await second_uow.analyses.save_unless_completed(
                                analysis=second,
                            )

                            await second_uow.commit()

                            return saved

                    saving = create_task(
                        coro=save_second(),
                    )

                    # NOTE:
                    # Long enough for the second update to reach the database and wait on the first one's row lock.
                    await sleep(
                        delay=0.5,
                    )

                    assert not saving.done()

                    await first_uow.commit()

                assert not await saving

                loaded = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert loaded is not None

                assert loaded.finished_at == first.finished_at

                assert tuple(item.item.lemma for item in loaded.items) == ("groom",)

            finally:
                async with engine.begin() as connection:
                    await connection.run_sync(
                        fn=Base.metadata.drop_all,
                    )

        finally:
            await engine.dispose()

    @mark.integration
    async def test_a_start_stored_while_another_run_completes_the_analysis_waits_and_then_stores_nothing(
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

                completing = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                starting = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert completing is not None

                assert starting is not None

                completing.complete(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                    items=(GROOM,),
                    stage_timings=TIMINGS,
                )

                # NOTE:
                # The second run read the analysis processing and takes it over, as a run does that finds one a worker
                # left.
                taken_over_at = EPOCH + timedelta(
                    seconds=3,
                )

                starting.fail(
                    at=taken_over_at,
                    reason="processing was interrupted",
                )

                starting.reopen(
                    at=taken_over_at,
                )

                starting.start(
                    at=taken_over_at,
                )

                async with new_unit_of_work() as completing_uow:
                    assert await completing_uow.analyses.save_unless_completed(
                        analysis=completing,
                    )

                    async def store_the_start() -> bool:
                        async with new_unit_of_work() as starting_uow:
                            started = await starting_uow.analyses.save_unless_completed(
                                analysis=starting,
                            )

                            await starting_uow.commit()

                            return started

                    storing = create_task(
                        coro=store_the_start(),
                    )

                    await wait_for(
                        fut=_until_a_statement_waits_for_a_lock(
                            engine,
                        ),
                        timeout=10,
                    )

                    await completing_uow.commit()

                assert not await storing

                loaded = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert loaded is not None

                assert loaded.status is AnalysisStatus.COMPLETED

                assert loaded.finished_at == completing.finished_at

                assert tuple(item.item.lemma for item in loaded.items) == ("groom",)

            finally:
                async with engine.begin() as connection:
                    await connection.run_sync(
                        fn=Base.metadata.drop_all,
                    )

        finally:
            await engine.dispose()

    @mark.integration
    async def test_a_failure_stored_while_another_run_starts_the_analysis_again_waits_and_then_stores_nothing(
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

                failing = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                taking_over = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert failing is not None

                assert taking_over is not None

                failing.fail(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                    reason="tagger crashed",
                )

                # TEST:
                # The second run starts a microsecond after the first, the least two starts can differ by, so the
                # first run's failure is refused only if the comparison sees that microsecond.
                taken_over_at = EPOCH + timedelta(
                    seconds=1,
                    microseconds=1,
                )

                taking_over.fail(
                    at=taken_over_at,
                    reason="processing was interrupted",
                )

                taking_over.reopen(
                    at=taken_over_at,
                )

                taking_over.start(
                    at=taken_over_at,
                )

                async with new_unit_of_work() as taking_over_uow:
                    assert await taking_over_uow.analyses.save_unless_completed(
                        analysis=taking_over,
                    )

                    async def store_the_failure() -> bool:
                        async with new_unit_of_work() as failing_uow:
                            failed = await failing_uow.analyses.save_while_processing(
                                analysis=failing,
                                started_at=EPOCH
                                + timedelta(
                                    seconds=1,
                                ),
                            )

                            await failing_uow.commit()

                            return failed

                    storing = create_task(
                        coro=store_the_failure(),
                    )

                    await wait_for(
                        fut=_until_a_statement_waits_for_a_lock(
                            engine,
                        ),
                        timeout=10,
                    )

                    await taking_over_uow.commit()

                assert not await storing

                loaded = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert loaded is not None

                assert loaded.status is AnalysisStatus.PROCESSING

                assert loaded.started_at == taken_over_at

            finally:
                async with engine.begin() as connection:
                    await connection.run_sync(
                        fn=Base.metadata.drop_all,
                    )

        finally:
            await engine.dispose()

    @mark.integration
    async def test_a_run_is_told_apart_from_one_that_started_a_microsecond_later(
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

                # TEST:
                # The start carries a microsecond, so the run is matched only if PostgreSQL gives it back exactly.
                stored = processing_analysis(
                    at=EPOCH
                    + timedelta(
                        microseconds=1,
                    ),
                )

                await store_analysis(
                    analysis=stored,
                    new_unit_of_work=new_unit_of_work,
                )

                failed = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert failed is not None

                failed.fail(
                    at=EPOCH
                    + timedelta(
                        seconds=2,
                    ),
                    reason="tagger crashed",
                )

                async with new_unit_of_work() as uow:
                    assert not await uow.analyses.save_while_processing(
                        analysis=failed,
                        started_at=EPOCH
                        + timedelta(
                            seconds=1,
                            microseconds=2,
                        ),
                    )

                    assert await uow.analyses.save_while_processing(
                        analysis=failed,
                        started_at=EPOCH
                        + timedelta(
                            seconds=1,
                            microseconds=1,
                        ),
                    )

                    await uow.commit()

                loaded = await load_analysis(
                    analysis_id=stored.id,
                    new_unit_of_work=new_unit_of_work,
                )

                assert loaded is not None

                assert loaded.status is AnalysisStatus.FAILED

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
