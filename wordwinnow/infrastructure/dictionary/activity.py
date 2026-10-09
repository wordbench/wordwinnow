"""
What a dictionary that asks a provider is doing at one moment, for an operator
watching it: how its lookups so far were answered, the circuit, the request
budget, and each lookup in flight.

The snapshot is read in the process that runs the dictionary, and travels as a
payload from the enrichment service to the command line.
"""

from dataclasses import (
    dataclass,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    Literal,
    final,
)

from pydantic import (
    BaseModel,
)

from wordwinnow.infrastructure.dictionary.cache import (
    LookupCounts,
)
from wordwinnow.infrastructure.dictionary.free_dictionary import (
    LookupActivity,
    ProviderActivity,
    WaitCause,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    BreakerState,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
)

type CircuitName = Literal[
    "closed",
    "open",
    "half_open",
]

# NOTE:
# The circuit's states by the names the metrics give them, and back.
_CIRCUIT_NAMES: Final[MappingProxyType[BreakerState, CircuitName]] = MappingProxyType(
    mapping={
        BreakerState.CLOSED: "closed",
        BreakerState.OPEN: "open",
        BreakerState.HALF_OPEN: "half_open",
    },
)

_CIRCUITS: Final = MappingProxyType(
    mapping={
        name: state
        for (
            state,
            name,
        ) in _CIRCUIT_NAMES.items()
    },
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class DictionaryActivity:
    """
    What a dictionary is doing: how its lookups so far were answered, and what
    the client that asks its provider is doing.
    """

    counts: LookupCounts

    provider: ProviderActivity


class CountsPayload(
    BaseModel,
):
    """
    The counts on the wire, with each reason a lookup went unanswered for.
    """

    cached: int

    found: int

    not_found: int

    unavailable: dict[str, int]


class LookupActivityPayload(
    BaseModel,
):
    """
    One lookup in flight on the wire.
    """

    lemma: str

    waiting_for: WaitCause

    attempt: int

    seconds: float

    delay_seconds: float | None = None

    reason: FailureReason | None = None

    trial: bool = False


class ProviderActivityPayload(
    BaseModel,
):
    """
    The provider client's activity on the wire, with the circuit's state named
    as the metrics name it.
    """

    circuit: CircuitName

    retry_in_seconds: float | None = None

    tokens: float

    burst: int

    requests_per_second: float

    waiting_for_budget: int

    timeout_seconds: float

    lookups: list[LookupActivityPayload]

    retries: dict[FailureReason, int]


class ActivityPayload(
    BaseModel,
):
    """
    A whole activity on the wire.
    """

    counts: CountsPayload

    provider: ProviderActivityPayload


def to_payload(
    *,
    activity: DictionaryActivity,
) -> ActivityPayload:
    """
    The payload that carries `activity`.
    """

    provider = activity.provider

    return ActivityPayload(
        counts=CountsPayload(
            cached=activity.counts.cached,
            found=activity.counts.found,
            not_found=activity.counts.not_found,
            unavailable=dict(
                activity.counts.unavailable,
            ),
        ),
        provider=ProviderActivityPayload(
            circuit=_CIRCUIT_NAMES[provider.circuit],
            retry_in_seconds=provider.retry_in_seconds,
            tokens=provider.tokens,
            burst=provider.burst,
            requests_per_second=provider.requests_per_second,
            waiting_for_budget=provider.waiting_for_budget,
            timeout_seconds=provider.timeout_seconds,
            lookups=[
                LookupActivityPayload(
                    lemma=lookup.lemma,
                    waiting_for=lookup.waiting_for,
                    attempt=lookup.attempt,
                    seconds=lookup.seconds,
                    delay_seconds=lookup.delay_seconds,
                    reason=lookup.reason,
                    trial=lookup.trial,
                )
                for lookup in provider.lookups
            ],
            retries=dict(
                provider.retries,
            ),
        ),
    )


def from_payload(
    *,
    payload: ActivityPayload,
) -> DictionaryActivity:
    """
    The activity `payload` carries.
    """

    provider = payload.provider

    return DictionaryActivity(
        counts=LookupCounts(
            cached=payload.counts.cached,
            found=payload.counts.found,
            not_found=payload.counts.not_found,
            unavailable=MappingProxyType(
                mapping=dict(
                    payload.counts.unavailable,
                ),
            ),
        ),
        provider=ProviderActivity(
            circuit=_CIRCUITS[provider.circuit],
            retry_in_seconds=provider.retry_in_seconds,
            tokens=provider.tokens,
            burst=provider.burst,
            requests_per_second=provider.requests_per_second,
            waiting_for_budget=provider.waiting_for_budget,
            timeout_seconds=provider.timeout_seconds,
            lookups=tuple(
                LookupActivity(
                    lemma=lookup.lemma,
                    waiting_for=lookup.waiting_for,
                    attempt=lookup.attempt,
                    seconds=lookup.seconds,
                    delay_seconds=lookup.delay_seconds,
                    reason=lookup.reason,
                    trial=lookup.trial,
                )
                for lookup in provider.lookups
            ),
            retries=MappingProxyType(
                mapping=dict(
                    provider.retries,
                ),
            ),
        ),
    )
