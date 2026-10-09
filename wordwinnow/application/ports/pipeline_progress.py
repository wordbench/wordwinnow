"""
How far the pipeline has come, for whoever runs it and wants to say so.
"""

from typing import (
    Protocol,
)

from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)


class PipelineProgress(
    Protocol,
):
    """
    Hears each stage as it starts, and each dictionary lookup as it ends.

    A stage names how many steps it takes when it knows; the analysis never
    depends on what the listener does with any of it.
    """

    def stage_started(
        self,
        *,
        stage: Stage,
        steps: int | None,
    ) -> None:
        """
        `stage` begins, and takes `steps` steps when the number is known.
        """

        ...

    def looked_up(
        self,
        *,
        lookup: DictionaryLookup,
    ) -> None:
        """
        One word of the dictionary stage has its answer, whatever it was.
        """

        ...
