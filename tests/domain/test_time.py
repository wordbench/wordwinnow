"""
Every recorded moment says where in the world it happened.
"""

from datetime import (
    UTC,
    datetime,
)
from typing import (
    final,
)

from pytest import (
    raises,
)

from wordwinnow.domain.time import (
    InvalidTimestampError,
    aware_datetime,
)


@final
class TestAwareDatetime:
    def test_an_aware_datetime_passes_through(
        self,
    ) -> None:
        value = datetime(
            year=1970,
            month=1,
            day=1,
            tzinfo=UTC,
        )

        assert (
            aware_datetime(
                value=value,
                field_name="requested_at",
            )
            is value
        )

    def test_a_naive_datetime_is_rejected_by_field_name(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidTimestampError,
            match="requested_at",
        ):
            aware_datetime(
                value=datetime(
                    year=1970,
                    month=1,
                    day=1,
                ),
                field_name="requested_at",
            )
