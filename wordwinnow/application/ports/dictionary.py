"""
A dictionary: what a word means and how it is pronounced.

It is kept apart from the lexical network, which answers a different question
about the same word.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)


class Dictionary(
    Protocol,
):
    """
    Looks a word up in a dictionary.

    The outcome is always a `DictionaryLookup`; an unreachable dictionary is
    an outcome, not an exception, because the analysis continues without it.
    """

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        """
        Every entry the dictionary has for `lemma`, in any part of speech.
        """

        ...
