"""
The Top Stories API: a section's current stories.
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


@final
class NytTopStoriesSource:
    """
    A section's top stories: each headline and abstract as the API gives it.

    The topic is a section name, such as `science` or `world`; an unknown one
    is refused by the API itself.
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
        section = topic.strip().lower()

        if not section:
            raise SourceRejectedError(
                "New York Times top stories require a section, such as science",
            )

        payload = await self._api.get(
            path=f"/svc/topstories/v2/{section}.json",
            params={},
            what=f"New York Times top stories for {section!r}",
            model=StoriesPayload,
        )

        stories = readable_stories(
            stories=payload.results,
        )[:limit]

        return document_of(
            title=f"New York Times Top Stories: {section}",
            reference=f"top-stories/{section}",
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
