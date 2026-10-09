"""
Timezone-aware domain timestamps.
"""

from datetime import (
    datetime,
)
from typing import (
    final,
)


@final
class InvalidTimestampError(
    ValueError,
):
    """
    Raised when a domain timestamp is not a timezone-aware datetime.
    """


def aware_datetime(
    *,
    value: datetime,
    field_name: str,
) -> datetime:
    """
    Return `value` after validating that it is timezone-aware.
    """

    if not isinstance(
        value,
        datetime,
    ):
        raise InvalidTimestampError(
            f"{field_name} requires a datetime, got {
                type(
                    value,
                ).__name__
            }",
        )

    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTimestampError(
            f"{field_name} requires a timezone-aware datetime, got the naive {value.isoformat()}",
        )

    return value
