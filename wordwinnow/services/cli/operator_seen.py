"""
What the operator view keeps from the activity it reads and the progress it
hears, and the immutable picture of it that the drawing reads.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from math import (
    floor,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.application.ports.pipeline_progress import (
    HeldWords,
)
from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
    LookupOutcome,
)
from wordwinnow.infrastructure.dictionary.activity import (
    DictionaryActivity,
)
from wordwinnow.infrastructure.dictionary.cache import (
    LookupCounts,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    ProviderActivity,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)

# NOTE:
# The seconds the lane and the notice of words let go look back over: the lane shows a minute, and a word let go is
# kept for as long as the lane can show it.
MINUTE: Final = 60

# NOTE:
# A clock second is the whole second of the clock's reading, and a reading a hair under a whole second is that second:
# the recorded times of a virtual clock are sums of fractions, and a request must stay in the cell it came back in.
EPSILON: Final = 1e-6

_RANK: Final = MappingProxyType(
    mapping={
        BreakerState.CLOSED: 0,
        BreakerState.HALF_OPEN: 1,
        BreakerState.OPEN: 2,
    },
)


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


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Report:
    """
    The last `dictionary_waiting` report: when it was heard, how many words
    were turned away and how many others sleep, and when the next of them asks
    again.
    """

    at: float

    turned_away: int

    others_sleeping: int

    retry_in_seconds: float | None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Stored:
    """
    The last progress a worker stored, as the command waiting for the analysis
    heard it: when, the words it counts as waiting, and when the next check or
    ask is.
    """

    at: float

    waiting_words: int

    next_in_seconds: float | None


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Seen:
    """
    Everything the drawing needs, as the view last read and heard it.

    `stored` is set when the analysis runs in a worker, and then the activity
    is the enrichment service's, for every analysis it serves, while the
    stage's own count comes from the stored progress alone.

    `let_go` holds each time some words were let go, with how many, for the
    last minute, and `circuits` the breaker's worst state in each clock second
    of it, the oldest first.
    """

    activity: DictionaryActivity

    read_at: float

    baseline: LookupCounts

    steps: int | None

    done: int

    let_go: tuple[tuple[float, int], ...]

    let_go_total: int

    report: Report | None

    stored: Stored | None

    open_since: float | None

    closed_at: float | None

    closed_after: float | None

    closed_by: int | None

    circuits: tuple[tuple[int, BreakerState], ...]

    last_reason: FailureReason | None


@final
class Keeper:
    """
    Advances what the view keeps, at a read of the activity or on a report of
    the progress, and nowhere else.

    Each call that changes what is kept makes a new `Seen`, which the drawing
    reads whole.
    """

    def __init__(
        self,
    ) -> None:
        self._seen: Seen | None = None

        self._baseline: LookupCounts | None = None

        self._before: ProviderActivity | None = None

        self._was_closed: bool | None = None

        self._opened: float | None = None

        self._failed_trials: int | None = None

        self._open_since: float | None = None

        self._closed_at: float | None = None

        self._closed_after: float | None = None

        self._closed_by: int | None = None

        self._circuits: dict[int, BreakerState] = {}

        self._last_reason: FailureReason | None = None

        self._steps: int | None = None

        self._done = 0

        self._let_go: list[tuple[float, int]] = []

        self._let_go_total = 0

        self._report: Report | None = None

        self._stored: Stored | None = None

        self._unavailable: int | None = None

        self._activity: DictionaryActivity | None = None

        self._read_at = 0.0

    @property
    def seen(
        self,
    ) -> Seen | None:
        """
        What the drawing reads: `None` until the activity has been read.
        """

        return self._seen

    def watched(
        self,
        *,
        activity: DictionaryActivity,
        now: float,
    ) -> None:
        """
        The activity was read at `now`.
        """

        provider = activity.provider

        if self._baseline is None:
            self._baseline = activity.counts

        self._breaker(
            provider=provider,
            now=now,
        )

        second = clock_second(
            moment=now,
        )

        worst = self._circuits.get(
            second,
        )

        if worst is None or _RANK[provider.circuit] > _RANK[worst]:
            self._circuits[second] = provider.circuit

        self._circuits = {
            each: state
            for (
                each,
                state,
            ) in self._circuits.items()
            if each > second - MINUTE
        }

        retrying = tuple(lookup for lookup in provider.lookups if lookup.waiting_for is WaitCause.RETRY)

        for lookup in retrying:
            if lookup.reason is not None and lookup.reason is not FailureReason.RATE_LIMITED:
                self._last_reason = lookup.reason

        if not provider.consecutive_failures and not retrying:
            self._last_reason = None

        self._before = provider

        self._activity = activity

        self._read_at = now

        self._prune(
            now=now,
        )

        self._publish()

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        """
        A stage began; only the dictionary stage's count is kept.
        """

        if stage is not Stage.DICTIONARY_ENRICHMENT:
            return

        self._steps = steps

        self._done = 0

        self._let_go = []

        self._let_go_total = 0

        self._report = None

        self._publish()

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
        now: float,
    ) -> None:
        """
        A word of the dictionary stage has its answer.
        """

        self._done += 1

        if lookup.outcome is LookupOutcome.UNAVAILABLE:
            self._let_go.append(
                (
                    now,
                    1,
                ),
            )

            self._let_go_total += 1

        self._prune(
            now=now,
        )

        self._publish()

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        retry_in_seconds: float | None,
        now: float,
    ) -> None:
        """
        The stage reported the words it holds.
        """

        self._report = Report(
            at=now,
            turned_away=sum(group.turned_away for group in held),
            others_sleeping=sum(group.others_sleeping for group in held),
            retry_in_seconds=retry_in_seconds,
        )

        self._publish()

    def processing(
        self,
        *,
        progress: ProcessingProgress,
        now: float,
    ) -> None:
        """
        A worker's stored progress arrived.

        The words let go since the report before are the growth of its count
        of words the dictionary left unanswered; the first report only sets
        the count they grow from.
        """

        if progress.stage is not Stage.DICTIONARY_ENRICHMENT:
            return

        self._steps = progress.steps

        self._done = progress.done

        if self._unavailable is not None and progress.unavailable > self._unavailable:
            self._let_go.append(
                (
                    now,
                    progress.unavailable - self._unavailable,
                ),
            )

        self._unavailable = progress.unavailable

        self._stored = Stored(
            at=now,
            waiting_words=progress.dictionary_waiting_words or 0,
            next_in_seconds=progress.dictionary_next_in_seconds,
        )

        self._prune(
            now=now,
        )

        self._publish()

    def _breaker(
        self,
        *,
        provider: ProviderActivity,
        now: float,
    ) -> None:
        before = self._before

        if provider.circuit is not BreakerState.OPEN:
            self._open_since = None

        elif before is not None and (
            before.circuit is not BreakerState.OPEN or (provider.failed_trials or 0) > (before.failed_trials or 0)
        ):
            self._open_since = now

        closed = provider.circuit is BreakerState.CLOSED

        if not closed:
            self._failed_trials = provider.failed_trials

            if self._was_closed:
                self._opened = now

            elif self._was_closed is None:
                self._opened = None

        elif self._was_closed is False:
            self._closed_at = now

            self._closed_after = None if self._opened is None else now - self._opened

            self._closed_by = None if self._failed_trials is None else self._failed_trials + 1

        self._was_closed = closed

    def _prune(
        self,
        *,
        now: float,
    ) -> None:
        self._let_go = [
            (
                at,
                words,
            )
            for (
                at,
                words,
            ) in self._let_go
            if now - at < MINUTE
        ]

    def _publish(
        self,
    ) -> None:
        if self._activity is None or self._baseline is None:
            return

        self._seen = Seen(
            activity=self._activity,
            read_at=self._read_at,
            baseline=self._baseline,
            steps=self._steps,
            done=self._done,
            let_go=tuple(
                self._let_go,
            ),
            let_go_total=self._let_go_total,
            report=self._report,
            stored=self._stored,
            open_since=self._open_since,
            closed_at=self._closed_at,
            closed_after=self._closed_after,
            closed_by=self._closed_by,
            circuits=tuple(
                sorted(
                    self._circuits.items(),
                ),
            ),
            last_reason=self._last_reason,
        )
