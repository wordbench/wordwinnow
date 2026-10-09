"""
Fetch a document from one of the configured external sources.
"""

from collections.abc import (
    Mapping,
)

from wordwinnow.application.errors import (
    SourceNotConfiguredError,
)
from wordwinnow.application.ports.document_source import (
    DocumentSource,
)
from wordwinnow.domain.document import (
    Document,
)


async def acquire_a_document(
    *,
    source: str,
    topic: str,
    limit: int,
    sources: Mapping[str, DocumentSource],
) -> Document:
    """
    Acquire one document about `topic` from the source named `source`.

    A name without a configured source is refused rather than guessed at, so a
    missing credential shows up as a clear answer.
    """

    configured = sources.get(
        source,
    )

    if configured is None:
        names = ", ".join(
            sorted(
                sources,
            ),
        )

        raise SourceNotConfiguredError(
            f"no source named {source!r} is configured; configured: {names or 'none'}",
        )

    return await configured.acquire(
        topic=topic,
        limit=limit,
    )
