"""
The rebalance listener that lets a partition change owner only between
records.
"""

# WARN:
# `PartitionHandoff` implements aiokafka's `ConsumerRebalanceListener`, whose hooks aiokafka calls with the partitions
# as one positional argument, so the hooks keep that shape: a keyword-only parameter would raise inside aiokafka,
# which logs a failing listener and carries on, and the handoff would silently stop holding anything.
#
# The conventions checker cannot tell a framework-imposed signature from a project one, so `pyproject.toml` excludes
# this module from its scan and says why.

from asyncio import (
    Event,
)
from collections.abc import (
    Iterator,
)
from collections.abc import (
    Set as AbstractSet,
)
from contextlib import (
    contextmanager,
)
from logging import (
    getLogger,
)
from typing import (
    Final,
    final,
    override,
)

from aiokafka import (
    ConsumerRebalanceListener,
    TopicPartition,
)

_logger: Final = getLogger(
    name="wordwinnow.messaging.handoff",
)


@final
class PartitionHandoff(
    ConsumerRebalanceListener,
):
    """
    Lets a rebalance take a partition from this consumer only between records.

    The loop marks the record it is handling, and a revocation waits until
    that record is handled and its offset committed, so the partition's next
    owner starts after it rather than handling it a second time alongside this
    one.
    """

    def __init__(
        self,
    ) -> None:
        self._between_records: Final = Event()

        self._between_records.set()

    @property
    def between_records(
        self,
    ) -> bool:
        """
        Whether the loop holds no record, so stopping it loses nothing.
        """

        return self._between_records.is_set()

    @contextmanager
    def holding(
        self,
    ) -> Iterator[None]:
        """
        Hold any revocation until the block ends.
        """

        self._between_records.clear()

        try:
            yield

        finally:
            self._between_records.set()

    # NOTE:
    # aiokafka awaits this before the consumer rejoins the group, keeps the member's heartbeat alive meanwhile so an
    # offset can still be committed, and hands no record to the loop until the new assignment arrives.
    #
    # The broker waits for the member only as long as the consumer's rebalance timeout, after which it assigns the
    # partition elsewhere and the commit fails; `consume` treats that as a redelivery.
    @override
    async def on_partitions_revoked(
        self,
        revoked: AbstractSet[TopicPartition],
    ) -> None:
        if not self._between_records.is_set():
            _logger.info(
                msg="rebalance.waiting_for_record",
                extra={
                    "partitions": sorted(f"{partition.topic}/{partition.partition}" for partition in revoked),
                },
            )

        await self._between_records.wait()

    @override
    async def on_partitions_assigned(
        self,
        assigned: AbstractSet[TopicPartition],
    ) -> None:
        # NOTE:
        # A newly assigned partition is read from its last committed offset, which is all the loop needs.
        return
