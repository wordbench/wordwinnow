"""
How common a word is, which is the one judgment of its level that always has
an answer.
"""

from typing import (
    Protocol,
)


class WordFrequency(
    Protocol,
):
    """
    How common a word is in general English.
    """

    def zipf(
        self,
        *,
        lemma: str,
    ) -> float:
        """
        The word's Zipf frequency, from 0 for unknown to about 8 for `the`.
        """

        ...
