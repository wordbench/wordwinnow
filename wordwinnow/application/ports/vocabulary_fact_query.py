"""
The aggregation side, as the application reads it.

Facts are written by the aggregation worker straight into its store; the
application only ever asks questions across them.
"""

from typing import (
    Protocol,
)

from wordwinnow.application.dto import (
    VocabularySummary,
)


class VocabularyFactQuery(
    Protocol,
):
    """
    Answers questions across every completed analysis.

    Raises `FactStoreUnavailableError` from the application's errors when the
    store could not be reached.
    """

    async def summarize(
        self,
        *,
        focus_limit: int,
    ) -> VocabularySummary:
        """
        Counts by level and the lemmas most often placed in the focus tier.
        """

        ...
