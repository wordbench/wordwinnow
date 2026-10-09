"""
The clock, so a use case never reads the wall clock itself.
"""

from datetime import (
    datetime,
)
from typing import (
    Protocol,
)


class Clock(
    Protocol,
):
    """
    Tells the current time, timezone-aware.
    """

    def now(
        self,
    ) -> datetime: ...
