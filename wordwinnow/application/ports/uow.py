"""
The transactional boundary around a use case: the repository it exposes
commits together, or not at all.
"""

from collections.abc import (
    Callable,
)
from types import (
    TracebackType,
)
from typing import (
    Protocol,
    Self,
)

from wordwinnow.application.ports.analysis_repository import (
    AnalysisRepository,
)


class UnitOfWork(
    Protocol,
):
    """
    One transaction over the repository.

    Leaving the block without `commit()` rolls the transaction back.
    """

    @property
    def analyses(
        self,
    ) -> AnalysisRepository:
        """
        The repository inside this transaction.
        """

        ...

    async def __aenter__(
        self,
    ) -> Self: ...

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None: ...

    async def commit(
        self,
    ) -> None:
        """
        Make every change in this transaction durable.
        """

        ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
