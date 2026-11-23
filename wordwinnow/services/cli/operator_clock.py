"""
The seconds the operator view counts in, and how it reads a moment of its
clock.
"""

from math import (
    floor,
)
from typing import (
    Final,
)

# NOTE:
# The seconds the lane and the notice of words let go look back over: the lane shows a minute, and a word let go is
# kept for as long as the lane can show it.
MINUTE: Final = 60

# NOTE:
# A clock second is the whole second of the clock's reading, and a reading a hair under a whole second is that second:
# the recorded times of a virtual clock are sums of fractions, and a request must stay in the cell it came back in.
EPSILON: Final = 1e-6


def clock_second(
    *,
    moment: float,
) -> int:
    """
    The clock second that `moment` falls in.
    """

    return floor(
        moment + EPSILON,
    )


def within(
    *,
    since: float,
    now: float,
    seconds: float,
) -> bool:
    """
    Whether `now` is less than `seconds` after `since`, reading a moment a
    hair short of `seconds` as `seconds`.
    """

    return now - since < seconds - EPSILON
