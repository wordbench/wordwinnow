"""
A lexical network: which senses a word has and how they relate to other words.

It is kept apart from the dictionary, which answers a different question about
the same word.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)


class LexicalSemantics(
    Protocol,
):
    """
    Lists the senses of a word in one part of speech.

    The work is local and synchronous.
    """

    def senses_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> tuple[LexicalSense, ...]:
        """
        The senses, most used first; empty when the network lacks the word.
        """

        ...
