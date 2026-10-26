"""
The enrichment service as a dictionary, for a worker that does not call the
provider itself.

The service answers every lookup with HTTP 200 and a `LookupPayload`, so an
unavailable provider arrives as an outcome; only the service being unreachable
or answering something else becomes a failure here, and an unreachable service
names a time to ask again, so an analysis waits for it as it waits for the
provider.
"""

from logging import (
    getLogger,
)
from time import (
    monotonic,
)
from typing import (
    Final,
    final,
)
from urllib.parse import (
    quote,
)

from httpx2 import (
    AsyncClient,
    HTTPError,
    Response,
    TimeoutException,
)

from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)
from wordwinnow.infrastructure.dictionary.resilience import (
    MonotonicClock,
)
from wordwinnow.infrastructure.dictionary.wire import (
    FailureReason,
    LookupPayload,
    from_wire,
    unavailable,
)
from wordwinnow.infrastructure.logging import (
    CORRELATION_HEADER,
    current_correlation_id,
)

_logger: Final = getLogger(
    name="wordwinnow.dictionary",
)

# PERF:
# When to ask the service again after it could not be reached, or did not answer in time: after as long as it has been
# unreachable so far, a second at the least and fifteen at the most, so a restart of a second or two costs a second or
# two, a word waiting through a long outage asks about four times a minute, and lookups that fail together count as
# one outage rather than as many failures.
#
# Simulated with 300 words and eight lookups at a time, a service that refused connections for two seconds to a minute
# cost 214 of the definitions every time without a time to ask again, and one that stopped answering cost 8 to 96;
# with it neither cost any, and the stage ended later by the length of the outage and a second and a half at most.
_FIRST_RETRY_SECONDS: Final = 1.0

_LONGEST_RETRY_SECONDS: Final = 15.0


@final
class RemoteDictionary:
    """
    Looks words up through the enrichment service.

    Satisfies `Dictionary` structurally.
    """

    def __init__(
        self,
        *,
        enrichment_url: str,
        timeout_seconds: float,
        client: AsyncClient,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._base_url: Final = enrichment_url.rstrip(
            "/",
        )

        self._timeout: Final = timeout_seconds

        self._client: Final = client

        self._clock: Final = clock

        # NOTE:
        # When the service last stopped answering, and `None` while it answers.
        self._unreachable_since: float | None = None

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        headers: dict[str, str] = {}

        correlation_id = current_correlation_id()

        if correlation_id is not None:
            headers[CORRELATION_HEADER] = correlation_id

        try:
            response = await self._client.get(
                url=f"{self._base_url}/lookups/{
                    quote(
                        string=lemma,
                        safe='',
                    )
                }",
                headers=headers,
                timeout=self._timeout,
            )

        except TimeoutException:
            lookup = unavailable(
                reason=FailureReason.TIMED_OUT,
                retry_after_seconds=self._unreachable(),
            )

        except HTTPError:
            lookup = unavailable(
                reason=FailureReason.TRANSPORT_ERROR,
                retry_after_seconds=self._unreachable(),
            )

        else:
            self._unreachable_since = None

            lookup = _lookup_of(
                response,
            )

        _logger.debug(
            msg="dictionary.remote.lookup",
            extra={
                "lemma": lemma,
                "outcome": lookup.outcome,
                "failure_reason": lookup.failure_reason,
            },
        )

        return lookup

    def _unreachable(
        self,
    ) -> float:
        now = self._clock()

        if self._unreachable_since is None:
            self._unreachable_since = now

        return min(
            _LONGEST_RETRY_SECONDS,
            max(
                _FIRST_RETRY_SECONDS,
                now - self._unreachable_since,
            ),
        )


def _lookup_of(
    response: Response,
    /,
) -> DictionaryLookup:
    if response.status_code != 200:
        return unavailable(
            reason=FailureReason.PROVIDER_ERROR,
        )

    # NOTE:
    # A validation error and an inconsistent payload are both `ValueError`, and both mean the service did not answer
    # with a lookup.
    try:
        return from_wire(
            payload=LookupPayload.model_validate_json(
                json_data=response.content,
            ),
        )

    except ValueError:
        return unavailable(
            reason=FailureReason.INVALID_RESPONSE,
        )
