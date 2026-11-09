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

    def compound_senses_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> tuple[LexicalSense, ...]:
        """
        The senses the network lists for a hyphenated word under the network's
        own spelling of the compound, most used first; empty for a word
        without a hyphen and for a compound the network lacks in this part of
        speech.
        """

        ...
