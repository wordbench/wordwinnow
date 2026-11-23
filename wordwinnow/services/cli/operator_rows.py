"""
The lookups the operator view follows from one read to the next, and the rows
it gives them.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import (
    dataclass,
)
from enum import (
    Enum,
    auto,
)
from itertools import (
    islice,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.dictionary.free_dictionary import (
    LookupActivity,
    ProviderActivity,
    RequestOutcome,
    WaitCause,
)

# NOTE:
# The most words drawn a row each, the eight lookups an analysis has at once by default.
WORD_ROWS: Final = 8

# NOTE:
# How long the row of a lookup that has left stays, and how long the last word out stays on the wire: the first second
# of it bright.
LINGER_SECONDS: Final = 1.0

OUT_SECONDS: Final = 10.0

# NOTE:
# The most marks of failed or refused requests a lookup keeps, the newest; no recording of the product's own client
# carries more than three.
MARKS_AT_MOST: Final = 5

_IN_LINE: Final = frozenset(
    (
        WaitCause.BUDGET,
        WaitCause.COOLDOWN,
    ),
)

_FOLDED: Final = frozenset(
    (
        WaitCause.BUDGET,
        WaitCause.COOLDOWN,
        WaitCause.TRIAL,
    ),
)

_ASKING: Final = frozenset(
    (
        WaitCause.PROVIDER,
        WaitCause.RETRY,
    ),
)

# NOTE:
# The rank of a lookup that has left, after every lookup still at the client; and the place a lookup in line is given
# when the activity names none, after every place there is.
_LEFT: Final = 5

_NO_PLACE: Final = 99


class Ending(
    Enum,
):
    """
    How a lookup that left the client ended, as the counts tell it: the word
    was found, or not found, or let go, or turned away to be asked again; or
    the counts do not say.
    """

    FOUND = auto()

    NOT_FOUND = auto()

    LET_GO = auto()

    TURNED_AWAY = auto()

    ENDED = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Followed:
    """
    One lookup at the client as the view followed it, or for a second after it
    left: the order in which the view first saw it, its lemma, how many times
    the view saw this word turned away before, the marks of its requests that
    failed or were refused, the oldest first, its highest attempt, and what
    the client last said it waited for.

    A lookup that has left also says when, and how it ended.
    """

    number: int

    lemma: str

    earlier: int

    marks: tuple[RequestOutcome, ...]

    attempt: int

    lookup: LookupActivity

    left_at: float | None = None

    ending: Ending | None = None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Rows:
    """
    The rows the lookups have, topmost first, a gap where a row is free: those
    of the loop's body, and those of the body that folds the line at the
    bucket and the words behind the trial away, for each number of rows from
    one to the eight of the loop.
    """

    loop: tuple[Followed | None, ...]

    folded: MappingProxyType[int, tuple[Followed | None, ...]]


NO_ROWS: Final = Rows(
    loop=(),
    folded=MappingProxyType(
        mapping={},
    ),
)


def queued(
    *,
    provider: ProviderActivity,
) -> tuple[LookupActivity, ...]:
    """
    The lookups waiting for a token, in the order the bucket serves them: the
    order they came, which is how long each has waited.
    """

    return tuple(
        sorted(
            (lookup for lookup in provider.lookups if lookup.waiting_for in _IN_LINE),
            key=lambda lookup: -lookup.seconds,
        ),
    )


def places(
    *,
    provider: ProviderActivity,
) -> MappingProxyType[str, int]:
    """
    The place of each lookup in line, the first first, from one.
    """

    return MappingProxyType(
        mapping={
            lookup.lemma: place
            for (
                place,
                lookup,
            ) in enumerate(
                iterable=queued(
                    provider=provider,
                ),
                start=1,
            )
        },
    )


def in_line(
    *,
    followed: Followed,
) -> bool:
    """
    Whether the lookup is at the client, waiting for a token.
    """

    return followed.left_at is None and followed.lookup.waiting_for in _IN_LINE


def behind_the_trial(
    *,
    followed: Followed,
) -> bool:
    """
    Whether the lookup is at the client, waiting for the trial's outcome.
    """

    return followed.left_at is None and followed.lookup.waiting_for is WaitCause.TRIAL


def folded_away(
    *,
    followed: Followed,
) -> bool:
    """
    Whether a terminal too short for a row to each word draws the lookup in
    the row of the line at the bucket or the row behind the trial instead.
    """

    return followed.left_at is None and followed.lookup.waiting_for in _FOLDED


@final
class Assigner:
    """
    Which of the lookups has which row, for `budget` rows, advanced at every
    read whether or not it is drawn.

    A lookup keeps its row for as long as it has one, and a lookup that is new
    takes the lowest row that is free; `fold` leaves out the lookups that the
    folded rows draw instead.
    """

    def __init__(
        self,
        *,
        budget: int,
        fold: bool,
    ) -> None:
        self._budget: Final = budget

        self._fold: Final = fold

        self._slots: dict[int, int] = {}

    def advance(
        self,
        *,
        followed: Sequence[Followed],
        places: Mapping[str, int],
    ) -> tuple[Followed | None, ...]:
        """
        The rows after a read of the `followed` lookups, which may be at the
        client or have left within the second: the trial first, then the words
        asking or backing off, then the words in line, the first first, then
        the words behind the trial, and last a lookup that has left.
        """

        chosen = tuple(
            islice(
                sorted(
                    (
                        each
                        for each in followed
                        if self._wants(
                            each,
                        )
                    ),
                    key=lambda each: _rank(
                        each,
                        places,
                    ),
                ),
                self._budget,
            ),
        )

        numbers = frozenset(each.number for each in chosen)

        self._slots = {
            number: slot
            for (
                number,
                slot,
            ) in self._slots.items()
            if number in numbers
        }

        used = set(
            self._slots.values(),
        )

        for each in chosen:
            if each.number not in self._slots:
                slot = next(
                    free
                    for free in range(
                        self._budget,
                    )
                    if free not in used
                )

                self._slots[each.number] = slot

                used.add(
                    slot,
                )

        by_slot = MappingProxyType(
            mapping={self._slots[each.number]: each for each in chosen},
        )

        return tuple(
            by_slot.get(
                slot,
            )
            for slot in range(
                max(
                    by_slot,
                    default=-1,
                )
                + 1,
            )
        )

    def _wants(
        self,
        followed: Followed,
        /,
    ) -> bool:
        if followed.ending is Ending.FOUND:
            return False

        return not (
            self._fold
            and folded_away(
                followed=followed,
            )
        )


def _rank(
    followed: Followed,
    places: Mapping[str, int],
    /,
) -> tuple[int, int, int]:
    """
    Which lookup most needs a row of its own, the lowest first: by kind, then
    by place for the words in line, and every tie by the order in which the
    view first saw the lookup.
    """

    number = followed.number

    if followed.left_at is not None:
        return (
            _LEFT,
            number,
            number,
        )

    lookup = followed.lookup

    if lookup.trial:
        return (
            0,
            number,
            number,
        )

    if lookup.waiting_for in _ASKING:
        return (
            1,
            number,
            number,
        )

    if lookup.waiting_for is WaitCause.TRIAL:
        return (
            3,
            number,
            number,
        )

    return (
        2,
        places.get(
            followed.lemma,
            _NO_PLACE,
        ),
        number,
    )
