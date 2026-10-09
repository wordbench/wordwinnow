"""
The Most Popular API: the stories readers viewed, emailed, or shared most.
"""

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
    StoriesPayload,
    document_of,
    readable_stories,
    reference_of,
    story_paragraphs,
)

KINDS: Final = frozenset(
    {
        "viewed",
        "emailed",
        "shared",
    },
)

PERIODS: Final = frozenset(
    {
        "1",
        "7",
        "30",
    },
)

DEFAULT_PERIOD: Final = "7"


@final
class NytMostPopularSource:
    """
    The most popular stories: each headline and abstract as the API gives it.

    The topic is `viewed`, `emailed`, or `shared`, optionally followed by a
    period in days, as in `viewed/30`; the period is seven days when omitted.
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
        (
            kind,
            _,
            period,
        ) = (
            topic.strip()
            .lower()
            .partition(
                "/",
            )
        )

        period = period or DEFAULT_PERIOD

        if kind not in KINDS or period not in PERIODS:
            raise SourceRejectedError(
                "New York Times most popular stories require viewed, emailed, or shared, optionally with a period of "
                f"1, 7, or 30 days, got {topic!r}",
            )

        payload = await self._api.get(
            path=f"/svc/mostpopular/v2/{kind}/{period}.json",
            params={},
            what=f"New York Times most {kind} stories over {period} days",
            model=StoriesPayload,
        )

        stories = readable_stories(
            stories=payload.results,
        )[:limit]

        return document_of(
            title=f"New York Times most {kind} stories, {period} days",
            reference=f"most-popular/{kind}/{period}",
            paragraphs=[
                paragraph
                for story in stories
                for paragraph in story_paragraphs(
                    title=story.title,
                    abstract=story.abstract,
                )
            ],
            references=[
                reference_of(
                    title=story.title,
                    url=story.url,
                )
                for story in stories
            ],
        )
