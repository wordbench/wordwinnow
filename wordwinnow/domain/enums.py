"""
The one enumeration base the domain shares.
"""

from enum import (
    StrEnum,
)
from typing import (
    final,
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
    @final
    def __lt__(
        self,
        other: object,
        /,
    ) -> bool:
        return NotImplemented

    @override
    @final
    def __le__(
        self,
        other: object,
        /,
    ) -> bool:
        return NotImplemented

    @override
    @final
    def __gt__(
        self,
        other: object,
        /,
    ) -> bool:
        return NotImplemented

    @override
    @final
    def __ge__(
        self,
        other: object,
        /,
    ) -> bool:
        return NotImplemented
