"""
Acquiring a document from a named source.
"""

from datetime import (
    timedelta,
)
from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from tests.fakes.ports import (
    FakeDocumentSource,
    UnavailableDocumentSource,
)
from wordwinnow.application.errors import (
    SourceNotConfiguredError,
    SourceUnavailableError,
)
from wordwinnow.application.use_cases.acquire_a_document import (
    acquire_a_document,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    Reference,
)

_DOCUMENT: Final = Document(
    title="New York Times Top Stories: science",
    text="A cab waits at the door\n\nThe King wore a black mask",
    origin=DocumentOrigin.NEW_YORK_TIMES,
    reference="top-stories/science",
    attribution="Data provided by The New York Times",
    references=(
        Reference(
            title="A cab waits at the door",
            url="https://example.com/photograph",
        ),
    ),
    retention=timedelta(
        hours=24,
    ),
)


@final
class TestAcquireADocument:
    async def test_the_configured_source_is_asked(
        self,
    ) -> None:
        source = FakeDocumentSource(
            document=_DOCUMENT,
        )

        document = await acquire_a_document(
            source="nyt-top-stories",
            topic="science",
            limit=5,
            sources={
                "nyt-top-stories": source,
            },
        )

        assert document == _DOCUMENT

        assert source.requests == [
            (
                "science",
                5,
            ),
        ]

    async def test_an_unconfigured_source_is_refused_with_the_configured_ones_named(
        self,
    ) -> None:
        with raises(
            expected_exception=SourceNotConfiguredError,
            match="configured: none",
        ):
            await acquire_a_document(
                source="nyt-top-stories",
                topic="science",
                limit=5,
                sources={},
            )

    async def test_an_unavailable_source_propagates_as_such(
        self,
    ) -> None:
        with raises(
            expected_exception=SourceUnavailableError,
        ):
            await acquire_a_document(
                source="nyt-top-stories",
                topic="science",
                limit=5,
                sources={
                    "nyt-top-stories": UnavailableDocumentSource(),
                },
            )
