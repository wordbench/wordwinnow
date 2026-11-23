"""
How the operator view follows each lookup at the client from one read of the
activity to the next: which marks it earned, how it ended, and which row it
has.
"""

from dataclasses import (
    replace,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    LookupActivity,
    RequestOutcome,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.services.cli.operator_clock import (
    within,
)
from wordwinnow.services.cli.operator_rows import (
    LINGER_SECONDS,
    MARKS_AT_MOST,
    NO_ROWS,
    WORD_ROWS,
    Assigner,
    Ending,
    Followed,
    Rows,
    places,
)

# NOTE:
# The endings that put a word on the wire past the provider, as the last word out.
_OUT: Final = frozenset(
    (
        Ending.FOUND,
        Ending.NOT_FOUND,
        Ending.LET_GO,
    ),
)

# NOTE:
# The endings after which the lookup's last request came back failed or refused, when the lookup was asking at the
# read before.
_AFTER_A_REQUEST: Final = frozenset(
    (
        Ending.TURNED_AWAY,
        Ending.LET_GO,
    ),
)

# NOTE:
# The reasons whose counts of lookups left unanswered grew, when only the provider's limit grew them.
_ONLY_THE_LIMIT: Final = frozenset(
    (FailureReason.RATE_LIMITED,),
)


@final
class Follower:
    """
    Follows each lookup at the client from the first read that saw it, and
    nowhere else: the drawing reads what it keeps and never changes it.

    A lookup is its lemma for as long as it stays at the client, since the
    client keys its lookups by it; a lemma seen again after it left is a new
    lookup, as it is for the client.
    """

    def __init__(
        self,
    ) -> None:
        self._followed: Final[dict[str, Followed]] = {}

        self._lingering: tuple[Followed, ...] = ()

        self._out: Followed | None = None

        self._turned_away: Final[dict[str, int]] = {}

        self._number = 0

        self._before: DictionaryActivity | None = None

        self._unavailable_heard = 0

        self._loop: Final = Assigner(
            budget=WORD_ROWS,
            fold=False,
        )

        self._folds: Final = MappingProxyType(
            mapping={
                budget: Assigner(
                    budget=budget,
                    fold=True,
                )
                for budget in range(
                    1,
                    WORD_ROWS + 1,
                )
            },
        )

        self._at_the_client: tuple[Followed, ...] = ()

        self._rows: Rows = NO_ROWS

    @property
    def followed(
        self,
    ) -> tuple[Followed, ...]:
        """
        The lookups at the client as the last read left them, the first seen
        first.
        """

        return self._at_the_client

    @property
    def lingering(
        self,
    ) -> tuple[Followed, ...]:
        """
        The lookups that left the client less than a second before the last
        read, each with how it ended.
        """

        return self._lingering

    @property
    def out(
        self,
    ) -> Followed | None:
        """
        The lookup that last left found, not found, or let go.
        """

        return self._out

    @property
    def rows(
        self,
    ) -> Rows:
        """
        The rows the lookups have, as the last read left them.
        """

        return self._rows

    def let_go_heard(
        self,
    ) -> None:
        """
        The stage reported a word let go, which the next read counts among the
        words that may have left the client.
        """

        self._unavailable_heard += 1

    def read(
        self,
        *,
        activity: DictionaryActivity,
        now: float,
        distributed: bool,
    ) -> None:
        """
        The activity was read at `now`; `distributed` says that the lookups
        are, or may be, the enrichment service's, for every analysis, and the
        command hears no word let go.
        """

        provider = activity.provider

        before = self._before

        present = MappingProxyType(
            mapping={lookup.lemma: lookup for lookup in provider.lookups},
        )

        gone = tuple(
            lemma
            for (
                lemma,
                followed,
            ) in self._followed.items()
            if lemma not in present or present[lemma].attempt < followed.attempt
        )

        ending = _ending(
            before=before,
            activity=activity,
            left=len(
                gone,
            ),
            heard=self._unavailable_heard,
            distributed=distributed,
        )

        refused = _only_rate_limited(
            before=before,
            activity=activity,
        )

        left: list[Followed] = []

        for lemma in gone:
            left.append(
                self._leave(
                    lemma,
                    ending=ending,
                    refused=refused,
                    now=now,
                ),
            )

        self._lingering = tuple(
            each
            for each in (
                *self._lingering,
                *left,
            )
            if each.left_at is not None
            and within(
                since=each.left_at,
                now=now,
                seconds=LINGER_SECONDS,
            )
            and each.lemma not in present
        )

        retried = _retried(
            before=before,
            activity=activity,
        )

        for (
            lemma,
            lookup,
        ) in present.items():
            self._follow(
                lemma,
                lookup,
                retried=retried,
            )

        self._at_the_client = tuple(
            self._followed.values(),
        )

        everyone = (
            *self._at_the_client,
            *self._lingering,
        )

        where = places(
            provider=provider,
        )

        self._rows = Rows(
            loop=self._loop.advance(
                followed=everyone,
                places=where,
            ),
            folded=MappingProxyType(
                mapping={
                    budget: assigner.advance(
                        followed=everyone,
                        places=where,
                    )
                    for (
                        budget,
                        assigner,
                    ) in self._folds.items()
                },
            ),
        )

        self._before = activity

        self._unavailable_heard = 0

    def _leave(
        self,
        lemma: str,
        /,
        *,
        ending: Ending,
        refused: bool,
        now: float,
    ) -> Followed:
        followed = self._followed.pop(
            lemma,
        )

        marks = followed.marks

        if ending in _AFTER_A_REQUEST and followed.lookup.waiting_for is WaitCause.PROVIDER:
            marks = _marked(
                marks,
                RequestOutcome.REFUSED if refused else RequestOutcome.FAILED,
            )

        if ending is Ending.TURNED_AWAY:
            self._turned_away[lemma] = (
                self._turned_away.get(
                    lemma,
                    0,
                )
                + 1
            )

        left = replace(
            followed,
            marks=marks,
            left_at=now,
            ending=ending,
        )

        if ending in _OUT:
            self._out = left

        return left

    def _follow(
        self,
        lemma: str,
        lookup: LookupActivity,
        /,
        *,
        retried: tuple[int, int],
    ) -> None:
        followed = self._followed.get(
            lemma,
        )

        if followed is None:
            self._number += 1

            # NOTE:
            # A lookup first seen past its first attempt had requests the reads missed, of the kind its wait says.
            self._followed[lemma] = Followed(
                number=self._number,
                lemma=lemma,
                earlier=self._turned_away.get(
                    lemma,
                    0,
                ),
                marks=_marked(
                    (),
                    *(
                        _kind(
                            lookup,
                            retried,
                        )
                        for _ in range(
                            max(
                                lookup.attempt - 1,
                                0,
                            ),
                        )
                    ),
                ),
                attempt=lookup.attempt,
                lookup=lookup,
            )

            return

        self._followed[lemma] = replace(
            followed,
            marks=_marked(
                followed.marks,
                *(
                    _kind(
                        lookup,
                        retried,
                    )
                    for _ in range(
                        max(
                            lookup.attempt - followed.attempt,
                            0,
                        ),
                    )
                ),
            ),
            attempt=max(
                lookup.attempt,
                followed.attempt,
            ),
            lookup=lookup,
        )


def _marked(
    marks: tuple[RequestOutcome, ...],
    /,
    *more: RequestOutcome,
) -> tuple[RequestOutcome, ...]:
    """
    `marks` with `more` after them, the newest `MARKS_AT_MOST` kept.
    """

    return (
        *marks,
        *more,
    )[-MARKS_AT_MOST:]


def _retried(
    *,
    before: DictionaryActivity | None,
    activity: DictionaryActivity,
) -> tuple[int, int]:
    """
    How many retries the client counted since the read before, after a refusal
    and after a failure: its `retries` are by reason.
    """

    earlier = {} if before is None else before.provider.retries

    refused = 0

    failed = 0

    for (
        reason,
        count,
    ) in activity.provider.retries.items():
        grown = max(
            count
            - earlier.get(
                reason,
                0,
            ),
            0,
        )

        if reason is FailureReason.RATE_LIMITED:
            refused += grown

        else:
            failed += grown

    return (
        refused,
        failed,
    )


def _kind(
    lookup: LookupActivity,
    retried: tuple[int, int],
    /,
) -> RequestOutcome:
    """
    Whether the request before this attempt failed or was refused: a backoff
    names its reason, a wait for the pause's end follows a refusal, and
    otherwise the client's own count of retries says which kind grew since the
    read before.
    """

    if lookup.waiting_for is WaitCause.RETRY:
        return RequestOutcome.REFUSED if lookup.reason is FailureReason.RATE_LIMITED else RequestOutcome.FAILED

    if lookup.waiting_for is WaitCause.COOLDOWN:
        return RequestOutcome.REFUSED

    (
        refused,
        failed,
    ) = retried

    return RequestOutcome.REFUSED if refused and not failed else RequestOutcome.FAILED


def _only_rate_limited(
    *,
    before: DictionaryActivity | None,
    activity: DictionaryActivity,
) -> bool:
    """
    Whether the only reason for which the counts of lookups left unanswered
    grew since the read before is the provider's limit.
    """

    earlier = {} if before is None else before.counts.unavailable

    return {
        reason
        for (
            reason,
            count,
        ) in activity.counts.unavailable.items()
        if count
        > earlier.get(
            reason,
            0,
        )
    } == _ONLY_THE_LIMIT


def _ending(
    *,
    before: DictionaryActivity | None,
    activity: DictionaryActivity,
    left: int,
    heard: int,
    distributed: bool,
) -> Ending:
    """
    How the `left` lookups that left the client since the read before ended,
    all in one way, from the growth of the counts and from how many words the
    stage reported let go meanwhile; `ended` where they say more than one way.

    Where the lookups are the service's, the words let go that the stage
    reports are this analysis's alone, so a count of lookups left unanswered
    says nothing of how they ended.
    """

    if before is None:
        return Ending.ENDED

    (
        earlier,
        now,
    ) = (
        before.counts,
        activity.counts,
    )

    grown = tuple(
        ending
        for (
            ending,
            count,
        ) in (
            (
                Ending.FOUND,
                now.found - earlier.found,
            ),
            (
                Ending.NOT_FOUND,
                now.not_found - earlier.not_found,
            ),
            (
                Ending.TURNED_AWAY,
                sum(
                    now.unavailable.values(),
                )
                - sum(
                    earlier.unavailable.values(),
                ),
            ),
        )
        if count > 0
    )

    if (
        len(
            grown,
        )
        != 1
    ):
        return Ending.ENDED

    (only,) = grown

    if only is not Ending.TURNED_AWAY:
        return only

    if distributed:
        return Ending.ENDED

    if heard >= left:
        return Ending.LET_GO

    return Ending.TURNED_AWAY if not heard else Ending.ENDED
