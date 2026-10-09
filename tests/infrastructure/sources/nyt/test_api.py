"""
The New York Times API client: how a refusal maps onto the source errors, the
one property every failure keeps, that the key never appears in what it
raises, and which results become stories and links.
"""

from typing import (
    final,
)

from httpx2 import (
    ConnectError,
    Request,
    Response,
)
from pytest import (
    raises,
)

from tests.infrastructure.sources.nyt.doubles import (
    KEY,
    STORIES,
    mock_api,
)
from wordwinnow.application.errors import (
    SourceNotConfiguredError,
    SourceRejectedError,
    SourceUnavailableError,
)
from wordwinnow.domain.document import (
    Reference,
)
from wordwinnow.infrastructure.sources.nyt.api import (
    Story,
    readable_stories,
    reference_of,
)
from wordwinnow.infrastructure.sources.nyt.top_stories import (
    NytTopStoriesSource,
)
from wordwinnow.infrastructure.user_agent import (
    USER_AGENT,
)


@final
class TestNytApi:
    async def test_failures_are_mapped_and_never_leak_the_key(
        self,
    ) -> None:
        for (
            status,
            expected,
        ) in (
            (
                401,
                SourceNotConfiguredError,
            ),
            (
                404,
                SourceRejectedError,
            ),
            (
                429,
                SourceUnavailableError,
            ),
            (
                503,
                SourceUnavailableError,
            ),
        ):

            def handler(
                request: Request,
                /,
                *,
                status: int = status,
            ) -> Response:
                return Response(
                    status_code=status,
                    json={
                        "fault": "no",
                    },
                )

            with raises(
                expected_exception=expected,
            ) as caught:
                await NytTopStoriesSource(
                    api=mock_api(
                        handler=handler,
                    ),
                ).acquire(
                    topic="science",
                    limit=5,
                )

            assert KEY not in str(
                object=caught.value,
            )

    async def test_every_request_names_the_program(
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

        await NytTopStoriesSource(
            api=mock_api(
                handler=handler,
            ),
        ).acquire(
            topic="science",
            limit=5,
        )

        assert seen[0].headers["User-Agent"] == USER_AGENT

    async def test_a_refused_key_says_what_to_check(
        self,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=401,
                json={
                    "fault": "no",
                },
            )

        with raises(
            expected_exception=SourceNotConfiguredError,
            match="has this API enabled",
        ):
            await NytTopStoriesSource(
                api=mock_api(
                    handler=handler,
                ),
            ).acquire(
                topic="science",
                limit=5,
            )

    async def test_a_transport_error_is_unavailable_without_the_url(
        self,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            raise ConnectError(
                message="connection refused",
            )

        with raises(
            expected_exception=SourceUnavailableError,
        ) as caught:
            await NytTopStoriesSource(
                api=mock_api(
                    handler=handler,
                ),
            ).acquire(
                topic="science",
                limit=5,
            )

        assert KEY not in str(
            object=caught.value,
        )

        assert "api-key" not in str(
            object=caught.value,
        )

    async def test_an_empty_section_and_a_bad_payload_are_unavailable(
        self,
    ) -> None:
        for body in (
            b'{"results": []}',
            b"not json",
        ):

            def handler(
                request: Request,
                /,
                *,
                body: bytes = body,
            ) -> Response:
                return Response(
                    status_code=200,
                    content=body,
                )

            with raises(
                expected_exception=SourceUnavailableError,
            ):
                await NytTopStoriesSource(
                    api=mock_api(
                        handler=handler,
                    ),
                ).acquire(
                    topic="science",
                    limit=5,
                )


@final
class TestStories:
    def test_a_page_embed_and_a_result_with_nothing_to_read_are_left_out(
        self,
    ) -> None:
        stories = (
            Story(
                title="A cab waits at the door",
                url="https://example.com/photograph",
                item_type="Article",
            ),
            Story(
                url="https://example.com/nothing",
            ),
            Story(
                title="Sign up for the newsletter",
                url="null",
                item_type="EmbeddedInteractive",
            ),
            Story(
                abstract="He studies the note by the lamp.",
                url="https://example.com/note",
            ),
        )

        assert readable_stories(
            stories=stories,
        ) == (
            stories[0],
            stories[3],
        )

    def test_a_link_that_is_not_a_web_address_is_left_out(
        self,
    ) -> None:
        for url in (
            "",
            "null",
            "nyt://embeddedinteractive/foo",
        ):
            assert (
                reference_of(
                    title="foo",
                    url=url,
                )
                is None
            )

        assert reference_of(
            title="foo",
            url="https://example.com/foo",
        ) == Reference(
            title="foo",
            url="https://example.com/foo",
        )
