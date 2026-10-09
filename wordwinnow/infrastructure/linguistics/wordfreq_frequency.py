"""
General English word frequency from the wordfreq package.
"""

from typing import (
    final,
)

from wordfreq import (
    zipf_frequency,
)


@final
class WordfreqFrequency:
    """
    Zipf frequencies from wordfreq's combined English corpus.

    Satisfies `WordFrequency` structurally.
    """

    def zipf(
        self,
        *,
        lemma: str,
    ) -> float:
        return zipf_frequency(
            word=lemma,
            lang="en",
        )
