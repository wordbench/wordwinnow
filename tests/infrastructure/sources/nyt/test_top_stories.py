"""
Top stories: a section becomes one document of headlines and abstracts,
unedited, with its attribution, its links, and its retention.
"""

from datetime import (
    timedelta,
)
from typing import (
    final,
)

from httpx2 import (
    Request,
    Response,
)

from tests.infrastructure.sources.nyt.doubles import (
    KEY,
    STORIES,
    mock_api,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
    Reference,
)
from wordwinnow.infrastructure.sources.nyt.top_stories import (
    NytTopStoriesSource,
)


@final
class TestTopStories:
    async def test_a_section_becomes_one_document_of_titles_and_abstracts(
        self,
    ) -> None:
        seen: list[Request] = []

        def handler(
            request: Request,
            /,
        ) -> Response:
            seen.append(
                request,
            )

            return Response(
                status_code=200,
                json=STORIES,
            )

        document = await NytTopStoriesSource(
            api=mock_api(
                handler=handler,
            ),
        ).acquire(
            topic="Science",
            limit=2,
        )

        assert document.origin is DocumentOrigin.NEW_YORK_TIMES

        assert document.reference == "top-stories/science"

        assert (
            document.text == "A cab waits at the door\n\nThe King wore a black mask\n\n"
            "Holmes waits in Baker Street\n\nHe studies the note by the lamp."
        )

        assert document.attribution == "Data provided by The New York Times"

        assert document.retention == timedelta(
            hours=24,
        )

        assert document.references == (
            Reference(
                title="A cab waits at the door",
                url="https://example.com/photograph",
            ),
        )

        assert seen[0].url.path == "/svc/topstories/v2/science.json"

        assert seen[0].url.params["api-key"] == KEY

    async def test_a_page_embed_neither_takes_a_place_nor_adds_words(
        self,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
                json={
                    "status": "OK",
                    "results": [
                        {
                            "item_type": "EmbeddedInteractive",
                            "title": "Sign up for the newsletter",
                            "abstract": "Every week, the stories of the season.",
                            "url": "null",
                        },
                        {
                            "item_type": "EmbeddedInteractive",
                            "title": "",
                            "abstract": "",
                            "url": "",
                        },
                        {
                            "item_type": "Article",
                            "title": "A cab waits at the door",
                            "abstract": "The King wore a black mask",
                            "url": "https://example.com/photograph",
                        },
                        {
                            "item_type": "Interactive",
                            "title": "Holmes waits in Baker Street",
                            "abstract": "He studies the note by the lamp.",
                            "url": "https://example.com/note",
                        },
                    ],
                },
            )

        document = await NytTopStoriesSource(
            api=mock_api(
                handler=handler,
            ),
        ).acquire(
            topic="science",
            limit=2,
        )

        assert (
            document.text == "A cab waits at the door\n\nThe King wore a black mask\n\n"
            "Holmes waits in Baker Street\n\nHe studies the note by the lamp."
        )

        assert document.references == (
            Reference(
                title="A cab waits at the door",
                url="https://example.com/photograph",
            ),
            Reference(
                title="Holmes waits in Baker Street",
                url="https://example.com/note",
            ),
        )
