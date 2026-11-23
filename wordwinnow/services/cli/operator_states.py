"""
How the dictionary is answering, and what the operator view says of it: the
title's state, the sentence under the lane, what is due next, the words to ask
and ask again, and how the words that have their answer ended.
"""

from dataclasses import (
    dataclass,
)
from enum import (
    Enum,
    auto,
)
from math import (
    ceil,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.dictionary.free_dictionary import (
    RECOVERY_SECONDS,
    LookupActivity,
    ProviderActivity,
    RequestOutcome,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)
from wordwinnow.services.cli.operator_seen import (
    EPSILON,
    MINUTE,
    Seen,
)
from wordwinnow.services.progress import (
    ACTIVITY_INTERVAL_SECONDS,
)

# NOTE:
# How long a request must have asked, with nothing come back, for the title to say so; and from how long a request
# counts as slow.
QUIET_AFTER: Final = 5.0

SLOW_AFTER: Final = 5.0

# NOTE:
# The client's own jitter on the breaker's wait, a quarter shorter at most (`_RECOVERY_JITTER` in
# `free_dictionary.py`, which a test holds this equal to).
RECOVERY_JITTER: Final = 0.25

# NOTE:
# What each reason a lookup went unanswered for, or was asked again for, is called in the view; keyed by the reason's
# value, since an analysis's counts of unanswered lookups name each reason by it.
REASONS: Final = MappingProxyType(
    mapping={
        FailureReason.CIRCUIT_OPEN: "held back",
        FailureReason.TIMED_OUT: "timed out",
        FailureReason.TRANSPORT_ERROR: "connection failed",
        FailureReason.RATE_LIMITED: "rate limited",
        FailureReason.PROVIDER_ERROR: "server error",
        FailureReason.INVALID_RESPONSE: "unreadable answer",
    },
)

_SUFFIXES: Final = MappingProxyType(
    mapping={
        1: "st",
        2: "nd",
        3: "rd",
    },
)


class State(
    Enum,
):
    """
    How the dictionary is answering, in the order the view takes the first
    that holds.
    """

    FAILING = auto()

    ON_TRIAL = auto()

    RATE_LIMITED = auto()

    IDLE = auto()

    NO_ANSWER = auto()

    STRUGGLING = auto()

    SLOW = auto()

    ANSWERING = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Held:
    """
    The words the analysis holds, which are not at the client: how many, and
    in how many seconds the first asks again, `None` where none is due.
    """

    count: int

    wake_in: float | None


def ordinal(
    *,
    number: int,
) -> str:
    """
    `number` as an ordinal: `2nd`, `3rd`, `11th`.
    """

    if 10 <= number % 100 <= 20:
        return f"{number}th"

    suffix = _SUFFIXES.get(
        number % 10,
        "th",
    )

    return f"{number}{suffix}"


def ago(
    *,
    seconds: float,
) -> str:
    """
    A time that has passed, in whole seconds rounded down: `4 s`, `1 min 2 s`.
    """

    (
        minutes,
        rest,
    ) = divmod(
        int(
            seconds,
        ),
        60,
    )

    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def duration(
    *,
    seconds: float,
) -> str:
    """
    A time to come, as the learner's line says one: whole seconds rounded up,
    and minutes from sixty.
    """

    whole = ceil(
        seconds - EPSILON,
    )

    (
        minutes,
        rest,
    ) = divmod(
        whole,
        60,
    )

    if not minutes:
        return f"{rest} s"

    return f"{minutes} min" if not rest else f"{minutes} min {rest} s"


def reason_text(
    *,
    reason: FailureReason | None,
) -> str:
    """
    What the view calls a failure's reason.
    """

    return (
        "failed"
        if reason is None
        else REASONS.get(
            reason,
            reason,
        )
    )


def pause_of(
    *,
    provider: ProviderActivity,
) -> float | None:
    """
    The seconds a pause the provider asked for had to run at the read, or
    `None` where none is in force.
    """

    seconds = provider.cooldown_seconds

    return seconds if seconds is not None and seconds > 0 else None


def asking(
    *,
    provider: ProviderActivity,
) -> tuple[LookupActivity, ...]:
    """
    The lookups waiting for the provider's answer, the oldest first.
    """

    return tuple(lookup for lookup in provider.lookups if lookup.waiting_for is WaitCause.PROVIDER)


def quiet(
    *,
    provider: ProviderActivity,
) -> float | None:
    """
    How long nothing has come back while a request asks, once that is five
    seconds: the shorter of the oldest request's age and the time since the
    last request came back.
    """

    seconds = tuple(
        lookup.seconds
        for lookup in asking(
            provider=provider,
        )
    )

    if not seconds:
        return None

    oldest = max(
        seconds,
    )

    newest_back = min(
        (request.seconds for request in provider.recent),
        default=None,
    )

    silent = (
        oldest
        if newest_back is None
        else min(
            newest_back,
            oldest,
        )
    )

    return silent if silent >= QUIET_AFTER else None


def slow(
    *,
    provider: ProviderActivity,
) -> tuple[LookupActivity, ...]:
    """
    The requests that have asked five seconds or more, when they are two at
    least, or the only one asking: a provider that answers, slowly.
    """

    waiting = asking(
        provider=provider,
    )

    late = tuple(lookup for lookup in waiting if lookup.seconds >= SLOW_AFTER)

    return (
        late
        if len(
            late,
        )
        >= 2
        or (
            late
            and len(
                waiting,
            )
            == 1
        )
        else ()
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
            (
                lookup
                for lookup in provider.lookups
                if lookup.waiting_for
                in (
                    WaitCause.BUDGET,
                    WaitCause.COOLDOWN,
                )
            ),
            key=lambda lookup: -lookup.seconds,
        ),
    )


def trial_name(
    *,
    provider: ProviderActivity,
) -> str:
    """
    The trial the breaker will decide on next, by how many failed before it.
    """

    if provider.failed_trials is None:
        return "the trial"

    return f"trial {provider.failed_trials + 1}"


def held_words(
    *,
    seen: Seen,
) -> int:
    """
    How many words the analysis holds, as the stage or the worker last said.
    """

    if seen.stored is not None:
        return seen.stored.waiting_words

    report = seen.report

    return 0 if report is None else report.turned_away + report.others_sleeping


def held_of(
    *,
    seen: Seen,
    now: float,
) -> Held:
    """
    The words to ask again at `now`.

    Where the stage reports them, the count leaves out the held words that
    have woken and are at the client again, which `to ask` counts.
    """

    if seen.stored is not None:
        stored = seen.stored

        wake = (
            None
            if stored.next_in_seconds is None
            else max(
                stored.next_in_seconds - (now - stored.at),
                0.0,
            )
        )

        return Held(
            count=stored.waiting_words,
            wake_in=wake,
        )

    report = seen.report

    if report is None:
        return Held(
            count=0,
            wake_in=None,
        )

    wake = (
        None
        if report.retry_in_seconds is None
        else max(
            report.retry_in_seconds - (now - report.at),
            0.0,
        )
    )

    return Held(
        count=max(
            report.turned_away + report.others_sleeping,
            0,
        ),
        wake_in=wake,
    )


def to_ask(
    *,
    seen: Seen,
) -> int | None:
    """
    The words not yet asked, or `None` before the stage's count is known.

    For the stage here, the count less the words done, the words to ask again
    by the last report, and the lookups at the client; for a worker's
    analysis, less the words done and those it counts as waiting only, because
    the lookups at the client are the service's, for every analysis it serves.
    """

    if seen.steps is None:
        return None

    waiting = held_words(
        seen=seen,
    )

    if seen.stored is not None:
        return max(
            seen.steps - seen.done - waiting,
            0,
        )

    return max(
        seen.steps
        - seen.done
        - waiting
        - len(
            seen.activity.provider.lookups,
        ),
        0,
    )


def state_of(
    *,
    seen: Seen,
) -> State:
    """
    The first state of the title's table that holds at the last read.
    """

    provider = seen.activity.provider

    if provider.circuit is BreakerState.OPEN:
        return State.FAILING

    if provider.circuit is BreakerState.HALF_OPEN:
        return State.ON_TRIAL

    if (
        pause_of(
            provider=provider,
        )
        is not None
    ):
        return State.RATE_LIMITED

    if not provider.lookups and not held_words(
        seen=seen,
    ):
        return State.IDLE

    if (
        quiet(
            provider=provider,
        )
        is not None
    ):
        return State.NO_ANSWER

    if provider.consecutive_failures or any(lookup.waiting_for is WaitCause.RETRY for lookup in provider.lookups):
        return State.STRUGGLING

    if slow(
        provider=provider,
    ):
        return State.SLOW

    return State.ANSWERING


def closed_lately(
    *,
    seen: Seen,
    now: float,
) -> bool:
    """
    Whether the breaker closed within the last minute.
    """

    return seen.closed_at is not None and now - seen.closed_at < MINUTE


def minute_counts(
    *,
    provider: ProviderActivity,
) -> tuple[tuple[RequestOutcome, int, str], ...]:
    """
    The client's own requests of the last minute, by how they came back.
    """

    return tuple(
        (
            outcome,
            sum(1 for request in provider.recent if request.outcome is outcome),
            word,
        )
        for (
            outcome,
            word,
        ) in (
            (
                RequestOutcome.ANSWERED,
                "answered",
            ),
            (
                RequestOutcome.REFUSED,
                "refused",
            ),
            (
                RequestOutcome.FAILED,
                "failed",
            ),
        )
    )


def _opening(
    provider: ProviderActivity,
    /,
) -> str:
    if provider.failed_trials:
        cause = f"trial {provider.failed_trials} failed"

    elif provider.failure_threshold is None:
        cause = "failed requests in a row"

    else:
        cause = f"{provider.failure_threshold} failed requests in a row"

    if provider.opened_by is None:
        return cause

    return f"{cause} ({
        reason_text(
            reason=provider.opened_by,
        )
    })"


def cause(
    *,
    seen: Seen,
    now: float,
) -> str:
    """
    One sentence: why the state is what it is.
    """

    provider = seen.activity.provider

    match state_of(
        seen=seen,
    ):
        case State.FAILING:
            again = " again" if provider.failed_trials else ""

            why = _opening(
                provider,
            )

            if seen.open_since is None:
                return f"breaker open after {why}"

            since = now - seen.open_since

            when = (
                "just now"
                if since < 1
                else f"{
                    ago(
                        seconds=since,
                    )
                } ago"
            )

            return f"breaker opened{again} {when} after {why}"

        case State.ON_TRIAL:
            name = trial_name(
                provider=provider,
            )

            trial = next(
                (lookup for lookup in provider.lookups if lookup.trial),
                None,
            )

            if trial is None:
                return f"breaker half-open: the next word to ask is {name}"

            return f"breaker half-open: {name}, {trial.lemma}, decides; the rest wait for it"

        case State.RATE_LIMITED:
            return "429: the provider asked the client to slow down, and every request waits"

        case State.NO_ANSWER:
            oldest = max(
                lookup.seconds
                for lookup in asking(
                    provider=provider,
                )
            )

            silent = (
                quiet(
                    provider=provider,
                )
                or 0.0
            )

            return f"the oldest request has asked {
                int(
                    oldest,
                )
            } s, and nothing has come back for {
                int(
                    silent,
                )
            } s"

        case State.STRUGGLING:
            failed = provider.consecutive_failures or 0

            retrying = tuple(lookup for lookup in provider.lookups if lookup.waiting_for is WaitCause.RETRY)

            reason = next(
                (
                    reason_text(
                        reason=lookup.reason,
                    )
                    for lookup in retrying
                    if lookup.reason
                ),
                None,
            )

            if reason is None and seen.last_reason is not None:
                reason = reason_text(
                    reason=seen.last_reason,
                )

            if failed:
                said = f"{failed} requests failed in a row"

                if provider.failure_threshold is not None:
                    more = provider.failure_threshold - failed

                    said = f"{said}; {more} more and the breaker opens"

                    if failed == 1:
                        said = f"1 request failed; {more} more in a row and the breaker opens"

                return said if reason is None else f"{said} ({reason})"

            words = (
                "a word waits"
                if len(
                    retrying,
                )
                == 1
                else f"{
                    len(
                        retrying,
                    )
                } words wait"
            )

            return f"answering again; {words} out a backoff after a {reason or 'failure'}"

        case State.SLOW:
            late = slow(
                provider=provider,
            )

            oldest = max(lookup.seconds for lookup in late)

            count = (
                "1 request has"
                if len(
                    late,
                )
                == 1
                else f"{
                    len(
                        late,
                    )
                } requests have"
            )

            return f"answering slowly: {count} asked {SLOW_AFTER:g} s or more, the oldest {
                int(
                    oldest,
                )
            } s"

        case State.IDLE:
            if seen.steps is not None and seen.done >= seen.steps:
                return "idle: every word has its answer"

            return "idle: no word at the client and none held"

        case State.ANSWERING:
            if (
                closed_lately(
                    seen=seen,
                    now=now,
                )
                and seen.closed_at is not None
            ):
                lasted = (
                    "a while"
                    if seen.closed_after is None
                    else ago(
                        seconds=seen.closed_after,
                    )
                )

                since = now - seen.closed_at

                when = (
                    "just now"
                    if since < 1
                    else f"{
                        ago(
                            seconds=since,
                        )
                    } ago"
                )

                name = "the trial" if seen.closed_by is None else f"trial {seen.closed_by}"

                return f"answering; the breaker closed {when} by {name}, after {lasted} open"

            answered = sum(1 for request in provider.recent if request.outcome is RequestOutcome.ANSWERED)

            failed_or_refused = (
                len(
                    provider.recent,
                )
                - answered
            )

            others = "none failed or refused" if not failed_or_refused else f"{failed_or_refused} failed or refused"

            return f"answering: {answered} answered in the last minute, {others}"


def long_wait(
    *,
    seen: Seen,
    now: float,
) -> bool:
    """
    Whether the breaker is held open for longer than its own wait, which no
    part of the loop explains.
    """

    provider = seen.activity.provider

    if (
        provider.circuit is not BreakerState.OPEN
        or pause_of(
            provider=provider,
        )
        is not None
    ):
        return False

    if seen.open_since is None:
        return False

    waited = (
        now
        - seen.open_since
        + max(
            (provider.retry_in_seconds or 0.0) - (now - seen.read_at),
            0.0,
        )
    )

    return waited > RECOVERY_SECONDS + ACTIVITY_INTERVAL_SECONDS


def next_clause(
    *,
    seen: Seen,
    now: float,
    width: int,
) -> str | None:
    """
    What changes next and when, or `None` when nothing is due.
    """

    provider = seen.activity.provider

    since = now - seen.read_at

    pause = pause_of(
        provider=provider,
    )

    pause_left = (
        None
        if pause is None
        else max(
            pause - since,
            0.0,
        )
    )

    def fit(
        *candidates: str,
    ) -> str:
        return next(
            (
                candidate
                for candidate in candidates
                if len(
                    candidate,
                )
                <= width
            ),
            candidates[-1],
        )

    resumes = (
        ""
        if pause_left is None
        else f"in {
            duration(
                seconds=pause_left,
            )
        }, when the provider's pause ends"
    )

    trial = trial_name(
        provider=provider,
    )

    if provider.circuit is BreakerState.CLOSED:
        if (
            state_of(
                seen=seen,
            )
            is State.NO_ANSWER
        ):
            oldest = max(
                lookup.seconds
                for lookup in asking(
                    provider=provider,
                )
            )

            left = ceil(
                provider.timeout_seconds - oldest,
            )

            if left > 0:
                return fit(
                    f"if nothing comes back, the oldest request times out in {left} s at the earliest",
                    f"the oldest request times out in {left} s at the earliest",
                )

        if resumes:
            return resumes

    elif provider.circuit is BreakerState.OPEN:
        due = max(
            (provider.retry_in_seconds or 0.0) - since,
            0.0,
        )

        if pause_left is not None and pause_left > due:
            return f"{trial} {resumes}"

        said = f"{trial} in {
            duration(
                seconds=due,
            )
        }"

        if seen.open_since is None:
            return said

        period = now - seen.open_since + due

        if period > RECOVERY_SECONDS + ACTIVITY_INTERVAL_SECONDS:
            why = ": as long as the provider asked"

        else:
            cut = (
                round(
                    number=(RECOVERY_SECONDS - period) * 2,
                )
                / 2
            )

            if cut <= 0:
                why = f": the whole {RECOVERY_SECONDS:g} s wait"

            else:
                why = f": the {RECOVERY_SECONDS:g} s wait, {cut:g} s shorter at random"

        return fit(
            f"{said}{why}",
            said,
        )

    else:
        if resumes:
            return f"{trial} {resumes}"

        if any(lookup.trial for lookup in provider.lookups):
            shortest = RECOVERY_SECONDS * (1 - RECOVERY_JITTER)

            said = f"when {trial} is answered"

            return fit(
                f"{said}; if it fails, {shortest:g} to {RECOVERY_SECONDS:g} s or longer",
                said,
            )

        return f"{trial}, with the next lookup"

    held = held_of(
        seen=seen,
        now=now,
    )

    if held.count and held.wake_in is not None:
        return f"the first word turned away asks again in {
            duration(
                seconds=held.wake_in,
            )
        }"

    return None


def tally(
    *,
    seen: Seen,
) -> tuple[tuple[str, int], ...]:
    """
    How the words that have their answer ended, as the counts say, every one
    counted from the first read and never below nought: from the cache, found,
    not found, and the words let go.
    """

    counts = seen.activity.counts

    base = seen.baseline

    return (
        (
            "cache",
            max(
                counts.cached - base.cached,
                0,
            ),
        ),
        (
            "found",
            max(
                counts.found - base.found,
                0,
            ),
        ),
        (
            "not found",
            max(
                counts.not_found - base.not_found,
                0,
            ),
        ),
        (
            "let go",
            seen.let_go_total,
        ),
    )
