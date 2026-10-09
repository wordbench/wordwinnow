"""
The command line's view of the enrichment service: what its dictionary is
doing, for the operator's view of an analysis.

An answer that cannot be read is an `ActivityUnreadableError` that says why,
so the view says so and the analysis goes on without it.
"""

from typing import (
    Final,
    final,
)

from httpx2 import (
    AsyncBaseTransport,
    AsyncClient,
    HTTPError,
)
from pydantic import (
    ValidationError,
)

from wordwinnow.infrastructure.dictionary.activity import (
    ActivityPayload,
    DictionaryActivity,
    from_payload,
)
from wordwinnow.services.progress import (
    ActivityUnreadableError,
)

# NOTE:
# Where the Compose stack publishes the enrichment service, as `intake_url`'s default is where it publishes intake; a
# worker reads `WORDWINNOW_ENRICHMENT_URL` to decide whether to ask the service at all, so that setting has no
# default.
PUBLISHED_URL: Final = "http://127.0.0.1:8100"

# NOTE:
# The activity is read four times a second, so an answer slower than a second is of no use to the view.
_TIMEOUT_SECONDS: Final = 1.0


@final
class EnrichmentClient:
    """
    Reads what the enrichment service's dictionary is doing.
    """

    def __init__(
        self,
        *,
        base_url: str,
        transport: AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url: Final = base_url

        self._client: Final = AsyncClient(
            timeout=_TIMEOUT_SECONDS,
            base_url=base_url,
            transport=transport,
        )

    async def close(
        self,
    ) -> None:
        """
        Release the connection pool.
        """

        await self._client.aclose()

    async def activity(
        self,
    ) -> DictionaryActivity:
        """
        What the dictionary is doing now.
        """

        try:
            response = await self._client.get(
                url="/activity",
            )

        except HTTPError as exception:
            raise ActivityUnreadableError(
                f"the enrichment service does not answer at {self._base_url}",
            ) from exception

        if response.status_code == 404:
            raise ActivityUnreadableError(
                "the enrichment service's dictionary asks no provider, so there is nothing to show",
            )

        if response.status_code != 200:
            raise ActivityUnreadableError(
                f"the enrichment service answered HTTP {response.status_code} for its activity",
            )

        try:
            payload = ActivityPayload.model_validate_json(
                json_data=response.content,
            )

        except ValidationError as exception:
            raise ActivityUnreadableError(
                f"the enrichment service at {self._base_url} answered something other than its activity",
            ) from exception

        return from_payload(
            payload=payload,
        )
