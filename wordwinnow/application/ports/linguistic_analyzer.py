"""
The linguistic analysis the pipeline starts from.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.language import (
    Sentence,
)


class LinguisticAnalyzer(
    Protocol,
):
    """
    Segments a text into sentences and analyzes every token in them.

    The work is CPU-bound and synchronous; the caller decides which thread
    runs it.
    """

    @property
    def function_words(
        self,
    ) -> frozenset[str]:
        """
        The closed-class words that are never vocabulary, lowercase.
        """

        ...

    def analyze(
        self,
        *,
        text: str,
    ) -> tuple[Sentence, ...]:
        """
        Segment, tokenize, tag, and lemmatize `text`.
        """

        ...
