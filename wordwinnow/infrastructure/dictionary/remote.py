"""
The enrichment service as a dictionary, for a worker that does not call the
provider itself.

The service answers every lookup with HTTP 200 and a `LookupPayload`, so an
unavailable provider arrives as an outcome; only the service being unreachable
or answering something else becomes a failure here.
"""

from logging import (
    getLogger,
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
    ) -> None:
        self._base_url: Final = enrichment_url.rstrip(
            "/",
        )

        self._timeout: Final = timeout_seconds

        self._client: Final = client

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
            )

        except HTTPError:
            lookup = unavailable(
                reason=FailureReason.TRANSPORT_ERROR,
            )

        else:
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
