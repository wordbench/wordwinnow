"""
Where documents come from when the learner does not bring them.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.document import (
    Document,
)


class DocumentSource(
    Protocol,
):
    """
    Acquires one document about a topic from an external source.

    A topic is whatever the source organizes itself by: a section, a query, a
    month, a feed.

    Raises `SourceRejectedError` for a topic the source does not have and
    `SourceUnavailableError` when the source cannot be reached.
    """

    async def acquire(
        self,
        *,
        topic: str,
        limit: int,
    ) -> Document:
        """
        Combine up to `limit` texts about `topic` into one document.
        """

        ...
