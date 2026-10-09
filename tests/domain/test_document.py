"""
Tests for `Document`: a text to analyze, and, when it belongs to someone else,
the terms it carries with it.
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

from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
    InvalidDocumentError,
    Reference,
)

_OWN: Final = Document(
    title="a-scandal-in-bohemia",
    text="Irene Adler kept the photograph.",
    origin=DocumentOrigin.CUSTOM_TEXT,
    reference="a-scandal-in-bohemia.txt",
)

_BORROWED: Final = Document(
    title="New York Times feed: Science",
    text="A cab waits at the door\n\nThe King wore a black mask",
    origin=DocumentOrigin.NEW_YORK_TIMES,
    reference="rss/Science",
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
class TestDocument:
    def test_the_learner_s_own_text_carries_no_terms(
        self,
    ) -> None:
        assert _OWN.attribution is None

        assert _OWN.references == ()

        assert not _OWN.expires

    def test_a_borrowed_text_says_whose_it_is_where_to_read_it_and_how_long_it_may_stay(
        self,
    ) -> None:
        assert _BORROWED.attribution == "Data provided by The New York Times"

        assert _BORROWED.references[0].url == "https://example.com/photograph"

        assert _BORROWED.expires

    def test_a_document_needs_a_title_a_text_and_a_reference(
        self,
    ) -> None:
        for (
            title,
            text,
            reference,
        ) in (
            (
                " ",
                "Irene Adler kept the photograph.",
                "a-scandal-in-bohemia.txt",
            ),
            (
                "a-scandal-in-bohemia",
                " ",
                "a-scandal-in-bohemia.txt",
            ),
            (
                "a-scandal-in-bohemia",
                "Irene Adler kept the photograph.",
                " ",
            ),
        ):
            with raises(
                expected_exception=InvalidDocumentError,
            ):
                Document(
                    title=title,
                    text=text,
                    origin=DocumentOrigin.CUSTOM_TEXT,
                    reference=reference,
                )

    def test_a_retention_must_be_positive(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDocumentError,
            match="positive retention",
        ):
            Document(
                title="New York Times feed: Science",
                text="A cab waits at the door",
                origin=DocumentOrigin.NEW_YORK_TIMES,
                reference="rss/Science",
                retention=timedelta(),
            )


@final
class TestReference:
    def test_a_reference_needs_a_title_and_a_url(
        self,
    ) -> None:
        with raises(
            expected_exception=InvalidDocumentError,
        ):
            Reference(
                title=" ",
                url="https://example.com/photograph",
            )

        with raises(
            expected_exception=InvalidDocumentError,
        ):
            Reference(
                title="A cab waits at the door",
                url="",
            )
