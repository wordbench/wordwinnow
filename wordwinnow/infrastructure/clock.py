"""
The system clock.
"""

from datetime import (
    UTC,
    datetime,
)
from typing import (
    final,
)


@final
class SystemClock:
    """
    Reads the wall clock in UTC.
    """

    def now(
        self,
    ) -> datetime:
        return datetime.now(
            tz=UTC,
        )
