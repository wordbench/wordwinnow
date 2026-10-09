"""
The text a learner wants to study, and where it came from.
"""

from dataclasses import (
    dataclass,
)
from datetime import (
    timedelta,
)
from enum import (
    auto,
)
from typing import (
    final,
)

from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)


@final
class InvalidDocumentError(
    ValueError,
):
    """
    Raised when a document would contain invalid data.
    """


class DocumentOrigin(
    UnorderedStrEnum,
):
    """
    The kind of source a document was acquired from.
    """

    CUSTOM_TEXT = auto()

    NEW_YORK_TIMES = auto()


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Reference:
    """
    One text a document was composed from, and where to read it in full.
    """

    title: str

    url: str

    def __post_init__(
        self,
    ) -> None:
        if not self.title.strip():
            raise InvalidDocumentError(
                "a reference requires a non-blank title",
            )

        if not self.url.strip():
            raise InvalidDocumentError(
                "a reference requires a non-blank url",
            )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Document:
    """
    A text to analyze.

    `reference` says where the text came from in the origin's own terms: a
    file name, or a source's topic.

    A text that belongs to someone else carries the `attribution` its owner
    asks to be shown, the `references` to read it in full, and a `retention`:
    how long the text may be kept before an analysis of it is deleted.

    The learner's own text carries none of those.
    """

    title: str

    text: str

    origin: DocumentOrigin

    reference: str

    attribution: str | None = None

    references: tuple[Reference, ...] = ()

    retention: timedelta | None = None

    def __post_init__(
        self,
    ) -> None:
        if not self.title.strip():
            raise InvalidDocumentError(
                "a document requires a non-blank title",
            )

        if not self.text.strip():
            raise InvalidDocumentError(
                "a document requires non-blank text",
            )

        if not self.reference.strip():
            raise InvalidDocumentError(
                "a document requires a non-blank reference",
            )

        if self.retention is not None and self.retention <= timedelta():
            raise InvalidDocumentError(
                f"a document requires a positive retention, got {self.retention}",
            )

    @property
    def expires(
        self,
    ) -> bool:
        """
        Whether the text may be kept only for a while.
        """

        return self.retention is not None
