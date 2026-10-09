"""
The Article Search API: articles matching a query, newest first.
"""

from math import (
    ceil,
)
from typing import (
    Final,
    final,
)

from wordwinnow.application.errors import (
    SourceRejectedError,
)
from wordwinnow.domain.document import (
    Document,
)
from wordwinnow.infrastructure.sources.nyt.api import (
    NytApi,
    SearchDocument,
    SearchPayload,
    document_of,
    reference_of,
    search_paragraphs,
)

# NOTE:
# The API answers ten articles per page, and a key may make five requests a minute, so one analysis asks for at most
# five pages: fifty articles, within the budget the terms call reasonable.
PAGE_SIZE: Final = 10

MAX_PAGES: Final = 5


@final
class NytArticleSearchSource:
    """
    The newest articles matching a query, up to fifty of them.

    The topic is the query.
    """

    def __init__(
        self,
        *,
        api: NytApi,
    ) -> None:
        self._api: Final = api

    async def acquire(
        self,
        *,
        topic: str,
        limit: int,
    ) -> Document:
        query = topic.strip()

        if not query:
            raise SourceRejectedError(
                "New York Times article search requires a query",
            )

        documents: list[SearchDocument] = []

        pages = min(
            ceil(
                limit / PAGE_SIZE,
            ),
            MAX_PAGES,
        )

        for page in range(
            pages,
        ):
            payload = await self._api.get(
                path="/svc/search/v2/articlesearch.json",
                params={
                    "q": query,
                    "sort": "newest",
                    "page": str(
                        object=page,
                    ),
                },
                what=f"New York Times articles about {query!r}",
                model=SearchPayload,
            )

            documents.extend(
                payload.response.docs,
            )

            if (
                len(
                    payload.response.docs,
                )
                < PAGE_SIZE
            ):
                break

        return document_of(
            title=f"New York Times articles about {query}",
            reference=f"article-search/{query}",
            paragraphs=[
                paragraph
                for document in documents[:limit]
                for paragraph in search_paragraphs(
                    document=document,
                )
            ],
            references=[
                reference_of(
                    title=document.headline.main,
                    url=document.web_url,
                )
                for document in documents[:limit]
            ],
        )
