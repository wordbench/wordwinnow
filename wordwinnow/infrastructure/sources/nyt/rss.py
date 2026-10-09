"""
The public RSS feeds: a section's latest stories, with no credential.
"""

from typing import (
    Final,
    final,
)
from xml.etree.ElementTree import (
    ParseError,
    fromstring,
)

from httpx2 import (
    AsyncClient,
    HTTPError,
    TimeoutException,
)

from wordwinnow.application.errors import (
    SourceRejectedError,
    SourceUnavailableError,
)
from wordwinnow.domain.document import (
    Document,
)
from wordwinnow.infrastructure.sources.nyt.api import (
    document_of,
    raise_for_status,
    reference_of,
    story_paragraphs,
)
from wordwinnow.infrastructure.user_agent import (
    IDENTIFYING_HEADERS,
)

DEFAULT_FEED_URL: Final = "https://rss.nytimes.com/services/xml/rss/nyt"


@final
class NytRssSource:
    """
    A feed's current items: each title and description as the feed gives it.

    The topic is a feed name as the New York Times spells it, such as
    `Science`, `World`, or `HomePage`.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_FEED_URL,
        client: AsyncClient,
    ) -> None:
        self._base_url: Final = base_url.rstrip(
            "/",
        )

        self._client: Final = client

    async def acquire(
        self,
        *,
        topic: str,
        limit: int,
    ) -> Document:
        feed = topic.strip()

        if not feed:
            raise SourceRejectedError(
                "New York Times feeds require a feed name, such as Science",
            )

        what = f"New York Times feed {feed!r}"

        try:
            response = await self._client.get(
                url=f"{self._base_url}/{feed}.xml",
                headers=IDENTIFYING_HEADERS,
            )

        except TimeoutException as exception:
            raise SourceUnavailableError(
                f"{what} timed out",
            ) from exception

        except HTTPError as exception:
            kind = type(
                exception,
            ).__name__

            raise SourceUnavailableError(
                f"{what} could not be fetched ({kind})",
            ) from exception

        raise_for_status(
            status=response.status_code,
            what=what,
            credentialed=False,
        )

        try:
            channel = fromstring(
                text=response.content,
            )

        except ParseError as exception:
            raise SourceUnavailableError(
                f"{what} answered with an unexpected payload",
            ) from exception

        items = list(
            channel.iterfind(
                path="channel/item",
            ),
        )[:limit]

        return document_of(
            title=f"New York Times feed: {feed}",
            reference=f"rss/{feed}",
            paragraphs=[
                paragraph
                for item in items
                for paragraph in story_paragraphs(
                    title=item.findtext(
                        path="title",
                        default="",
                    ),
                    abstract=item.findtext(
                        path="description",
                        default="",
                    ),
                )
            ],
            references=[
                reference_of(
                    title=item.findtext(
                        path="title",
                        default="",
                    ),
                    url=item.findtext(
                        path="link",
                        default="",
                    ),
                )
                for item in items
            ],
        )
