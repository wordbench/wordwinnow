"""
The unit of work: a transaction that commits together or not at all.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from tests.infrastructure.persistence.conftest import (
    NewUnitOfWork,
    completed_analysis,
    load_analysis,
)


@final
class TestUnitOfWork:
    async def test_leaving_without_a_commit_rolls_back(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = completed_analysis()

        async with new_unit_of_work() as uow:
            await uow.analyses.add(
                analysis=analysis,
            )

        assert (
            await load_analysis(
                analysis_id=analysis.id,
                new_unit_of_work=new_unit_of_work,
            )
            is None
        )

    async def test_an_explicit_rollback_discards_the_changes_before_a_commit(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        analysis = completed_analysis()

        async with new_unit_of_work() as uow:
            await uow.analyses.add(
                analysis=analysis,
            )

            await uow.rollback()

            await uow.commit()

        assert (
            await load_analysis(
                analysis_id=analysis.id,
                new_unit_of_work=new_unit_of_work,
            )
            is None
        )

    async def test_the_repository_is_reachable_only_inside_the_block(
        self,
        *,
        new_unit_of_work: NewUnitOfWork,
    ) -> None:
        uow = new_unit_of_work()

        with raises(
            expected_exception=RuntimeError,
        ):
            _ = uow.analyses
