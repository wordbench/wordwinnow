"""
The analysis repository against a SQLite file: what goes in comes back out,
field by field, and what may no longer be kept is deleted.
"""

from datetime import (
    UTC,
    timedelta,
    timezone,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
)

from tests.infrastructure.persistence.conftest import (
    DOCUMENT,
    EPOCH,
    GROOM,
    PROFILE,
    RESOLUTE,
    NewUnitOfWork,
    assert_same_state,
    completed_analysis,
    failed_analysis,
    load_analysis,
    processing_analysis,
    requested_analysis,
    store_analysis,
)
from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.domain.analysis import (
    AnalysisOptions,
    AnalysisStatus,
    Stage,
    reconstitute,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    Reference,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.infrastructure.persistence.models import (
    AnalysisRow,
    VocabularyItemRow,
)


@final
class TestRoundTrip:
    async def test_a_requested_analysis_comes_back_as_it_went_in(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = requested_analysis()

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

    async def test_a_completed_analysis_comes_back_with_its_items_timings_and_dictionary_information(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = completed_analysis()

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        loaded = await load_analysis(
            analysis_id=analysis.id,
            new_unit_of_work=new_unit_of_work,
        )

        assert_same_state(
            original=analysis,
            loaded=loaded,
        )

        assert loaded is not None

        assert loaded.items[0].dictionary == GROOM.dictionary

        assert loaded.items[1].dictionary == RESOLUTE.dictionary

        assert loaded.items[0].senses == GROOM.senses

        assert loaded.items[1].senses == RESOLUTE.senses

    async def test_a_failed_analysis_comes_back_with_its_reason(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = failed_analysis()

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

    async def test_an_unknown_id_is_none(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        assert (
            await load_analysis(
                analysis_id=AnalysisId.new(),
                new_unit_of_work=new_unit_of_work,
            )
            is None
        )

    async def test_timestamps_come_back_timezone_aware_in_utc(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        east = timezone(
            offset=timedelta(
                hours=2,
            ),
        )

        analysis = completed_analysis(
            at=EPOCH.astimezone(
                tz=east,
            ),
        )

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        loaded = await load_analysis(
            analysis_id=analysis.id,
            new_unit_of_work=new_unit_of_work,
        )

        assert loaded is not None

        assert loaded.finished_at is not None

        for instant in (
            loaded.requested_at,
            loaded.started_at,
            loaded.finished_at,
        ):
            assert instant is not None

            assert instant.tzinfo is UTC

        assert loaded.requested_at == EPOCH

        assert loaded.finished_at == EPOCH + timedelta(
            seconds=2,
        )


@final
class TestSave:
    async def test_save_replaces_the_items_and_the_lifecycle(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = completed_analysis()

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        replacement = reconstitute(
            id=analysis.id,
            document=DOCUMENT,
            profile=PROFILE,
            options=AnalysisOptions(
                include_dictionary=False,
            ),
            requested_at=EPOCH,
            status=AnalysisStatus.COMPLETED,
            started_at=EPOCH,
            finished_at=EPOCH,
            failure_reason=None,
            stage_timings=(),
            items=(RESOLUTE,),
        )

        async with new_unit_of_work() as uow:
            await uow.analyses.save(
                analysis=replacement,
            )

            await uow.commit()

        assert_same_state(
            original=replacement,
            loaded=await load_analysis(
                analysis_id=analysis.id,
                new_unit_of_work=new_unit_of_work,
            ),
        )

        async with new_unit_of_work() as uow:
            summaries = await uow.analyses.list_recent(
                limit=10,
            )

        assert tuple(summary.item_count for summary in summaries) == (1,)

    async def test_save_records_a_transition_of_a_stored_analysis(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = requested_analysis()

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        analysis.start(
            at=EPOCH,
        )

        analysis.fail(
            at=EPOCH,
            reason="no worker",
        )

        analysis.pull_events()

        async with new_unit_of_work() as uow:
            await uow.analyses.save(
                analysis=analysis,
            )

            await uow.commit()

        assert_same_state(
            original=analysis,
            loaded=await load_analysis(
                analysis_id=analysis.id,
                new_unit_of_work=new_unit_of_work,
            ),
        )


@final
class TestListing:
    async def test_recent_analyses_come_newest_first_up_to_the_limit_with_their_item_counts(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        oldest = requested_analysis(
            at=EPOCH,
        )

        middle = completed_analysis(
            at=EPOCH
            + timedelta(
                seconds=10,
            ),
        )

        newest = requested_analysis(
            at=EPOCH
            + timedelta(
                seconds=20,
            ),
        )

        for analysis in (
            middle,
            oldest,
            newest,
        ):
            await store_analysis(
                analysis=analysis,
                new_unit_of_work=new_unit_of_work,
            )

        async with new_unit_of_work() as uow:
            two = await uow.analyses.list_recent(
                limit=2,
            )

            everything = await uow.analyses.list_recent(
                limit=10,
            )

        assert tuple(summary.analysis_id for summary in two) == (
            newest.id,
            middle.id,
        )

        assert tuple(summary.item_count for summary in two) == (
            0,
            2,
        )

        assert tuple(summary.status for summary in two) == (
            AnalysisStatus.REQUESTED,
            AnalysisStatus.COMPLETED,
        )

        assert two[0].title == DOCUMENT.title

        assert two[0].origin is DocumentOrigin.CUSTOM_TEXT

        assert two[0].learner_level is CefrLevel.B1

        assert two[0].requested_at == newest.requested_at

        assert tuple(summary.analysis_id for summary in everything) == (
            newest.id,
            middle.id,
            oldest.id,
        )

    async def test_stale_analyses_have_waited_or_processed_since_before_the_threshold(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        waiting = requested_analysis()

        interrupted = processing_analysis()

        done = completed_analysis()

        failed = failed_analysis()

        fresh = requested_analysis(
            at=EPOCH
            + timedelta(
                seconds=10,
            ),
        )

        # NOTE:
        # Requested long ago, but only just started: it has not been processing long, whatever its wait in the queue.
        just_started = requested_analysis()

        just_started.start(
            at=EPOCH
            + timedelta(
                seconds=10,
            ),
        )

        for analysis in (
            waiting,
            interrupted,
            done,
            failed,
            fresh,
            just_started,
        ):
            await store_analysis(
                analysis=analysis,
                new_unit_of_work=new_unit_of_work,
            )

        async with new_unit_of_work() as uow:
            stale = await uow.analyses.list_stale(
                stale_before=EPOCH
                + timedelta(
                    seconds=5,
                ),
            )

        assert {analysis.id for analysis in stale} == {
            waiting.id,
            interrupted.id,
        }

        by_id = MappingProxyType(
            mapping={analysis.id: analysis for analysis in stale},
        )

        assert_same_state(
            original=waiting,
            loaded=by_id[waiting.id],
        )

        assert_same_state(
            original=interrupted,
            loaded=by_id[interrupted.id],
        )


@final
class TestExpiry:
    async def test_a_borrowed_text_round_trips_with_its_terms_and_is_deleted_once_expired(
        self,
        *,
        engine: AsyncEngine,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        borrowed = request_an_analysis(
            document=Document(
                title="New York Times feed: Science",
                text="A cab waits at the door",
                origin=DocumentOrigin.NEW_YORK_TIMES,
                reference="rss/Science",
                attribution="Data provided by The New York Times",
                references=(
                    Reference(
                        title="A cab waits at the door",
                        url="https://example.com/photograph",
                    ),
                ),
                retention=timedelta(
                    hours=24,
                ),
            ),
            profile=PROFILE,
            options=AnalysisOptions(),
            at=EPOCH,
        )

        borrowed.pull_events()

        own = requested_analysis()

        await store_analysis(
            analysis=borrowed,
            new_unit_of_work=new_unit_of_work,
        )

        await store_analysis(
            analysis=own,
            new_unit_of_work=new_unit_of_work,
        )

        loaded = await load_analysis(
            analysis_id=borrowed.id,
            new_unit_of_work=new_unit_of_work,
        )

        assert loaded is not None

        assert loaded.document == borrowed.document

        assert loaded.expires_at == EPOCH + timedelta(
            hours=24,
        )

        async with new_unit_of_work() as uow:
            kept = await uow.analyses.delete_expired(
                now=EPOCH
                + timedelta(
                    hours=23,
                ),
            )

            deleted = await uow.analyses.delete_expired(
                now=EPOCH
                + timedelta(
                    hours=24,
                ),
            )

            await uow.commit()

        assert (
            kept,
            deleted,
        ) == (
            0,
            1,
        )

        assert (
            await load_analysis(
                analysis_id=borrowed.id,
                new_unit_of_work=new_unit_of_work,
            )
            is None
        )

        assert (
            await load_analysis(
                analysis_id=own.id,
                new_unit_of_work=new_unit_of_work,
            )
            is not None
        )

        async with engine.connect() as connection:
            orphans = await connection.scalar(
                statement=select(
                    func.count(
                        VocabularyItemRow.id,
                    ),
                ).where(
                    VocabularyItemRow.analysis_id
                    == str(
                        object=borrowed.id,
                    ),
                ),
            )

        assert orphans == 0


_PROGRESS: Final = ProcessingProgress(
    stage=Stage.DICTIONARY_ENRICHMENT,
    steps=156,
    done=63,
    unavailable=20,
    dictionary_paused=True,
    reported_at=EPOCH.astimezone(
        tz=timezone(
            offset=timedelta(
                hours=3,
            ),
        ),
    ),
)


@final
class TestProgress:
    async def test_a_report_is_kept_while_processing_and_forgotten_by_the_next_save(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = processing_analysis()

        await store_analysis(
            analysis=analysis,
            new_unit_of_work=new_unit_of_work,
        )

        async with new_unit_of_work() as uow:
            await uow.analyses.record_progress(
                analysis_id=analysis.id,
                progress=_PROGRESS,
            )

            await uow.commit()

        async with new_unit_of_work() as uow:
            kept = await uow.analyses.progress_of(
                analysis_id=analysis.id,
            )

        analysis.fail(
            at=EPOCH,
            reason="processing was interrupted",
        )

        analysis.reopen(
            at=EPOCH,
        )

        analysis.start(
            at=EPOCH,
        )

        analysis.pull_events()

        async with new_unit_of_work() as uow:
            await uow.analyses.save(
                analysis=analysis,
            )

            await uow.commit()

        async with new_unit_of_work() as uow:
            forgotten = await uow.analyses.progress_of(
                analysis_id=analysis.id,
            )

        assert kept == _PROGRESS

        assert kept is not None

        assert kept.reported_at.tzinfo is UTC

        assert forgotten is None

    async def test_a_report_on_an_analysis_no_longer_processing_is_not_written(
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

        async with new_unit_of_work() as uow:
            await uow.analyses.record_progress(
                analysis_id=analysis.id,
                progress=_PROGRESS,
            )

            await uow.commit()

        async with engine.connect() as connection:
            empty = await connection.scalar(
                statement=select(
                    AnalysisRow.progress.is_(
                        None,
                    ),
                ).where(
                    AnalysisRow.id
                    == str(
                        object=analysis.id,
                    ),
                ),
            )

        assert empty is True
