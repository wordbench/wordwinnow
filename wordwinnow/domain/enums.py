"""
The one enumeration base the domain shares.
"""

from enum import (
    StrEnum,
)
from typing import (
    override,
)


class UnorderedStrEnum(
    StrEnum,
):
    """
    A `StrEnum` whose members stay string-compatible for a column, a message,
    or a report, but refuse `<`, `<=`, `>`, and `>=`: a closed set of labels,
    not a ranked scale.
    """

    @override
    def __lt__(
        self,
        other: object,
    ) -> bool:
        return NotImplemented

    @override
    def __le__(
        self,
        other: object,
    ) -> bool:
        return NotImplemented

    @override
    def __gt__(
        self,
        other: object,
    ) -> bool:
        return NotImplemented

    @override
    def __ge__(
        self,
        other: object,
    ) -> bool:
        return NotImplemented
