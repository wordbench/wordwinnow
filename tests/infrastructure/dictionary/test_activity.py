"""
The dictionary's activity as it travels from the enrichment service to the
command line.
"""

from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.infrastructure.dictionary.activity import (
    ActivityPayload,
    DictionaryActivity,
    from_payload,
    to_payload,
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

# NOTE:
# A trial in flight with a lookup waiting for it, and a lookup pausing before its second attempt: every field of a
# lookup set in one of them.
_ACTIVITY: Final = DictionaryActivity(
    counts=LookupCounts(
        cached=34,
        found=47,
        not_found=3,
        unavailable=MappingProxyType(
            mapping={
                "circuit_open": 2,
                "provider_error": 1,
            },
        ),
    ),
    provider=ProviderActivity(
        circuit=BreakerState.HALF_OPEN,
        retry_in_seconds=None,
        tokens=1.5,
        burst=4,
        requests_per_second=2.0,
        waiting_for_budget=3,
        timeout_seconds=30.0,
        lookups=(
            LookupActivity(
                lemma="photograph",
                waiting_for=WaitCause.PROVIDER,
                attempt=1,
                seconds=18.2,
                trial=True,
            ),
            LookupActivity(
                lemma="mask",
                waiting_for=WaitCause.TRIAL,
                attempt=1,
                seconds=18.1,
            ),
            LookupActivity(
                lemma="groom",
                waiting_for=WaitCause.RETRY,
                attempt=2,
                seconds=0.9,
                delay_seconds=1.7,
                reason=FailureReason.RATE_LIMITED,
            ),
        ),
        retries=MappingProxyType(
            mapping={
                FailureReason.RATE_LIMITED: 1,
            },
        ),
    ),
)


@final
class TestActivityPayload:
    def test_an_activity_read_back_from_its_json_is_the_activity_written(
        self,
    ) -> None:
        payload = to_payload(
            activity=_ACTIVITY,
        )

        assert (
            from_payload(
                payload=ActivityPayload.model_validate_json(
                    json_data=payload.model_dump_json(),
                ),
            )
            == _ACTIVITY
        )

    def test_the_circuit_travels_by_the_name_the_metrics_give_it(
        self,
    ) -> None:
        assert (
            to_payload(
                activity=_ACTIVITY,
            ).model_dump(
                mode="json",
            )["provider"]["circuit"]
            == "half_open"
        )
