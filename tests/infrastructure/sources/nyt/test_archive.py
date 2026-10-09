"""
The archive, one month at a time.
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
from wordwinnow.infrastructure.sources.nyt.archive import (
    NytArchiveSource,
)


@final
class TestArchive:
    async def test_a_month_becomes_its_first_articles(
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
                json=search_page(
                    count=4,
                ),
            )

        document = await NytArchiveSource(
            api=mock_api(
                handler=handler,
            ),
        ).acquire(
            topic="2026/8",
            limit=3,
        )

        assert seen[0].url.path == "/svc/archive/v1/2026/8.json"

        assert document.reference == "archive/2026-08"

        assert (
            len(
                document.references,
            )
            == 3
        )

    async def test_a_month_the_archive_cannot_have_is_refused(
        self,
    ) -> None:
        def handler(
            request: Request,
            /,
        ) -> Response:
            raise AssertionError(
                "no request expected",
            )

        for (
            topic,
            reason,
        ) in (
            (
                "2026-13",
                "from 01 to 12",
            ),
            (
                "1850-01",
                "from 1851-01 on",
            ),
            (
                "august",
                "in the form YYYY-MM",
            ),
        ):
            with raises(
                expected_exception=SourceRejectedError,
                match=reason,
            ):
                await NytArchiveSource(
                    api=mock_api(
                        handler=handler,
                    ),
                ).acquire(
                    topic=topic,
                    limit=5,
                )
