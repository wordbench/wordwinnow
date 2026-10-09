"""
One real feed, read and discarded: nothing it returns is stored.
"""

from typing import (
    final,
)

from httpx2 import (
    AsyncClient,
)
from pytest import (
    mark,
)

from wordwinnow.infrastructure.sources.nyt.rss import (
    NytRssSource,
)


@final
class TestLiveFeed:
    @mark.live
    async def test_the_science_feed_answers_today(
        self,
    ) -> None:
        async with AsyncClient() as client:
            document = await NytRssSource(
                client=client,
            ).acquire(
                topic="Science",
                limit=5,
            )

        assert document.reference == "rss/Science"

        assert (
            len(
                document.text.split(),
            )
            > 20
        )

        # TEST:
        # The feed is read and checked here and nowhere else: nothing of it is written to a store, a file, or a log.
        assert document.expires

        assert document.references
