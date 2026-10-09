"""
The first external judgment about a word's level: a list people annotated.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)


class ReferenceLexicon(
    Protocol,
):
    """
    A word list annotated with CEFR levels by people.
    """

    def level_of(
        self,
        *,
        lemma: str,
        part_of_speech: PartOfSpeech,
    ) -> CefrLevel | None:
        """
        The level the list assigns, or `None` when the list lacks the word.
        """

        ...
