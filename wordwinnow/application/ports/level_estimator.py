"""
The second external judgment about a word's level: a trained model.
"""

from collections.abc import (
    Sequence,
)
from typing import (
    Protocol,
)

from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.difficulty import (
    LexicalFeatures,
)


class LevelEstimator(
    Protocol,
):
    """
    A model that predicts levels from words' features, every word of a text in
    one call.
    """

    def estimate_many(
        self,
        *,
        features: Sequence[LexicalFeatures],
    ) -> tuple[CefrLevel | None, ...]:
        """
        One predicted level per word, in order, `None` where no trained model
        is available.
        """

        ...
