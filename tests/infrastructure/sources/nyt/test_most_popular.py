"""
The most popular stories, by kind and period.
"""

from typing import (
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
    STORIES,
    mock_api,
)
from wordwinnow.application.errors import (
    SourceRejectedError,
)
from wordwinnow.infrastructure.sources.nyt.most_popular import (
    NytMostPopularSource,
)


@final
class TestMostPopular:
    async def test_the_kind_and_the_period_make_the_path(
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

        source = NytMostPopularSource(
            api=mock_api(
                handler=handler,
            ),
        )

        document = await source.acquire(
            topic="viewed",
            limit=1,
        )

        assert document.reference == "most-popular/viewed/7"

        assert document.text == "A cab waits at the door\n\nThe King wore a black mask"

        await source.acquire(
            topic="Emailed/30",
            limit=1,
        )

        assert tuple(request.url.path for request in seen) == (
            "/svc/mostpopular/v2/viewed/7.json",
            "/svc/mostpopular/v2/emailed/30.json",
        )

    async def test_an_unknown_kind_or_period_is_refused_before_any_request(
        self,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            raise AssertionError(
                "no request expected",
            )

        for topic in (
            "liked",
            "viewed/2",
        ):
            with raises(
                expected_exception=SourceRejectedError,
            ):
                await NytMostPopularSource(
                    api=mock_api(
                        handler=handler,
                    ),
                ).acquire(
                    topic=topic,
                    limit=5,
                )
