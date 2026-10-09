"""
Article search, page by page, never more than five pages.
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
    mock_api,
    search_page,
)
from wordwinnow.application.errors import (
    SourceRejectedError,
)
from wordwinnow.infrastructure.sources.nyt.article_search import (
    NytArticleSearchSource,
)


@final
class TestArticleSearch:
    async def test_pages_are_fetched_until_the_limit_or_a_short_page(
        self,
    ) -> None:
        pages: list[str] = []

        def handler(
            request: Request,
            /,
        ) -> Response:
            pages.append(
                request.url.params["page"],
            )

            return Response(
                status_code=200,
                json=search_page(
                    count=10 if request.url.params["page"] == "0" else 2,
                ),
            )

        document = await NytArticleSearchSource(
            api=mock_api(
                handler=handler,
            ),
        ).acquire(
            topic="photograph",
            limit=25,
        )

        assert pages == [
            "0",
            "1",
        ]

        assert document.reference == "article-search/photograph"

        assert (
            len(
                document.references,
            )
            == 12
        )

        assert document.references[0].url == "https://example.com/0"

        assert document.text.startswith(
            "Headline 0\n\nAbstract 0.\n\nLead 0.\n\nHeadline 1\n\nAbstract 1.\n\n",
        )

    async def test_no_more_than_five_pages_are_asked_for(
        self,
    ) -> None:
        pages: list[str] = []

        def handler(
            request: Request,
            /,
        ) -> Response:
            pages.append(
                request.url.params["page"],
            )

            return Response(
                status_code=200,
                json=search_page(
                    count=10,
                ),
            )

        document = await NytArticleSearchSource(
            api=mock_api(
                handler=handler,
            ),
        ).acquire(
            topic="photograph",
            limit=100,
        )

        assert (
            len(
                pages,
            )
            == 5
        )

        assert (
            len(
                document.references,
            )
            == 50
        )

    async def test_a_blank_query_is_refused(
        self,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            raise AssertionError(
                "no request expected",
            )

        with raises(
            expected_exception=SourceRejectedError,
        ):
            await NytArticleSearchSource(
                api=mock_api(
                    handler=handler,
                ),
            ).acquire(
                topic="  ",
                limit=5,
            )
