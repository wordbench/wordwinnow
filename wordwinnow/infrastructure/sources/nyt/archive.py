"""
The Archive API: every article of one month, back to 1851.
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
    SearchPayload,
    document_of,
    reference_of,
    search_paragraphs,
)

FIRST_YEAR: Final = 1851


@final
class NytArchiveSource:
    """
    The first articles of one month as the archive lists them.

    The topic is a month, as in `2026-08`, and the same month is the same
    document every time, which is what makes this the one reproducible source.
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
            year,
            month,
        ) = _month_of(
            topic,
        )

        # WARN:
        # One month is one response of about twenty megabytes, whatever the limit; the limit only says how much of it
        # becomes the document.
        payload = await self._api.get(
            path=f"/svc/archive/v1/{year}/{month}.json",
            params={},
            what=f"New York Times archive for {year}-{month:02d}",
            model=SearchPayload,
        )

        return document_of(
            title=f"New York Times archive: {year}-{month:02d}",
            reference=f"archive/{year}-{month:02d}",
            paragraphs=[
                paragraph
                for document in payload.response.docs[:limit]
                for paragraph in search_paragraphs(
                    document=document,
                )
            ],
            references=[
                reference_of(
                    title=document.headline.main,
                    url=document.web_url,
                )
                for document in payload.response.docs[:limit]
            ],
        )


def _month_of(
    topic: str,
    /,
) -> tuple[int, int]:
    (
        year_text,
        _,
        month_text,
    ) = (
        topic.strip()
        .replace(
            "/",
            "-",
        )
        .partition(
            "-",
        )
    )

    try:
        year = int(
            year_text,
        )

        month = int(
            month_text,
        )

    except ValueError as exception:
        raise SourceRejectedError(
            f"New York Times archive requires a month in the form YYYY-MM, got {topic!r}",
        ) from exception

    if not 1 <= month <= 12:
        raise SourceRejectedError(
            f"New York Times archive requires a month from 01 to 12, got {topic!r}",
        )

    if year < FIRST_YEAR:
        raise SourceRejectedError(
            f"New York Times archive requires a month from {FIRST_YEAR}-01 on, got {topic!r}",
        )

    return (
        year,
        month,
    )
