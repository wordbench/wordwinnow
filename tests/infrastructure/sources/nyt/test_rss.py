"""
The feeds, which need no key.
"""

from typing import (
    Final,
    final,
)

from httpx2 import (
    Request,
    Response,
)
from pytest import (
    raises,
)

from tests.infrastructure.sources.nyt.doubles import (
    mock_client,
)
from wordwinnow.application.errors import (
    SourceRejectedError,
    SourceUnavailableError,
)
from wordwinnow.domain.document import (
    DocumentOrigin,
)
from wordwinnow.infrastructure.sources.nyt.rss import (
    NytRssSource,
)
from wordwinnow.infrastructure.user_agent import (
    USER_AGENT,
)

_FEED: Final = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>NYT &gt; Science</title>
<item><title>A cab waits at the door</title><description>The King wore a black mask</description><link>https://example.com/photograph</link></item>
<item><title>Holmes waits in Baker Street</title><description>He studies the note by the lamp.</description></item>
<item><title>The King hides behind a mask</title><description>She keeps the photograph.</description></item>
</channel></rss>"""


@final
class TestRss:
    async def test_a_feed_becomes_one_document_without_a_credential(
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
                content=_FEED,
            )

        document = await NytRssSource(
            client=mock_client(
                handler=handler,
            ),
        ).acquire(
            topic="Science",
            limit=2,
        )

        assert seen[0].url.path == "/services/xml/rss/nyt/Science.xml"

        assert "api-key" not in seen[0].url.params

        assert document.origin is DocumentOrigin.NEW_YORK_TIMES

        assert document.reference == "rss/Science"

        assert (
            document.text == "A cab waits at the door\n\nThe King wore a black mask\n\n"
            "Holmes waits in Baker Street\n\nHe studies the note by the lamp."
        )

        assert document.attribution == "Data provided by The New York Times"

        assert tuple(reference.url for reference in document.references) == ("https://example.com/photograph",)

    async def test_a_feed_request_names_the_program(
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
                content=_FEED,
            )

        await NytRssSource(
            client=mock_client(
                handler=handler,
            ),
        ).acquire(
            topic="Science",
            limit=2,
        )

        assert seen[0].headers["User-Agent"] == USER_AGENT

    async def test_an_unknown_feed_is_refused_and_a_broken_feed_is_unavailable(
        self,
    ) -> None:
        for (
            status,
            body,
            expected,
        ) in (
            (
                404,
                b"",
                SourceRejectedError,
            ),
            (
                200,
                b"<rss><channel>",
                SourceUnavailableError,
            ),
        ):

            def handler(
                request: Request,
                /,
                *,
                status: int = status,
                body: bytes = body,
            ) -> Response:
                return Response(
                    status_code=status,
                    content=body,
                )

            with raises(
                expected_exception=expected,
            ):
                await NytRssSource(
                    client=mock_client(
                        handler=handler,
                    ),
                ).acquire(
                    topic="Nonexistent",
                    limit=5,
                )
