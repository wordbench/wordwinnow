"""
A unit of work over one SQLAlchemy session: the session is the transaction.
"""

from collections.abc import (
    Callable,
)
from types import (
    TracebackType,
)
from typing import (
    Final,
    Self,
    final,
)

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from wordwinnow.infrastructure.persistence.analysis_repository import (
    SqlAlchemyAnalysisRepository,
)


@final
class SqlAlchemyUnitOfWork:
    """
    One session, entered as one transaction.

    Satisfies `UnitOfWork` structurally: leaving the block without `commit()`
    rolls the transaction back, and the session is closed either way.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory: Final = session_factory

        self._session: AsyncSession | None = None

        self._analyses: SqlAlchemyAnalysisRepository | None = None

    @property
    def analyses(
        self,
    ) -> SqlAlchemyAnalysisRepository:
        if self._analyses is None:
            raise RuntimeError(
                "the unit of work is not open; use it with `async with`",
            )

        return self._analyses

    async def __aenter__(
        self,
    ) -> Self:
        session = self._session_factory()

        self._session = session

        self._analyses = SqlAlchemyAnalysisRepository(
            session=session,
        )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None:
        session = self._active_session()

        # NOTE:
        # Rolling back after a commit discards nothing, so the block needs no record of whether it committed: whatever
        # was not made durable is dropped, and the session is closed whether or not the rollback itself succeeds.
        try:
            await session.rollback()

        finally:
            self._session = None

            self._analyses = None

            await session.close()

    async def commit(
        self,
    ) -> None:
        await self._active_session().commit()

    async def rollback(
        self,
    ) -> None:
        await self._active_session().rollback()

    def _active_session(
        self,
    ) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "the unit of work is not open; use it with `async with`",
            )

        return self._session


def new_unit_of_work_factory(
    *,
    engine: AsyncEngine,
) -> Callable[[], SqlAlchemyUnitOfWork]:
    """
    A factory of units of work over `engine`, one session each.
    """

    # NOTE:
    # Attributes stay loaded after a commit because an expired attribute would lazy-load on next access, and a lazy
    # load has no event loop to run on from synchronous attribute access.
    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    def new_unit_of_work() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            session_factory=session_factory,
        )

    return new_unit_of_work
