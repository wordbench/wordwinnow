"""
Delete the analyses whose text may no longer be kept.
"""

from wordwinnow.application.ports.clock import (
    Clock,
)
from wordwinnow.application.ports.uow import (
    UnitOfWorkFactory,
)


async def purge_expired_analyses(
    *,
    new_unit_of_work: UnitOfWorkFactory,
    clock: Clock,
) -> int:
    """
    Delete every expired analysis and say how many there were.

    An analysis of a text the learner owns never expires; one of a text that
    came with a retention expires when that time has passed.
    """

    async with new_unit_of_work() as uow:
        deleted = await uow.analyses.delete_expired(
            now=clock.now(),
        )

        await uow.commit()

    return deleted
