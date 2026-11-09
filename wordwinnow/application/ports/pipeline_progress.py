"""
How far the pipeline has come, for whoever runs it and wants to say so.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from typing import (
    Protocol,
    final,
)

from wordwinnow.domain.analysis import (
    Stage,
)
from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class HeldWords:
    """
    The words the dictionary turned away with a time to ask again, whose last
    refusal had one failure reason.

    `turned_away` is how many of them were turned away after the dictionary
    last answered one of the held words, and `turned_away_wake_in_seconds` how
    soon the earliest of those that sleep asks again.

    `others_sleeping` is how many of the rest still sleep.

    `wake_in_seconds` is how soon the earliest sleeping word of either kind
    asks again, `longest_wait_seconds` the longest wait one of them was given
    for its current sleep, and `repeated` whether one of them sleeps for the
    second time or more, each `None` or false while none sleeps.
    """

    reason: str | None

    turned_away: int

    turned_away_wake_in_seconds: float | None

    others_sleeping: int

    wake_in_seconds: float | None

    longest_wait_seconds: float | None

    repeated: bool


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

    def dictionary_waiting(
        self,
        *,
        held: Sequence[HeldWords],
        ending: bool,
        retry_in_seconds: float | None,
        patience_left_seconds: float,
    ) -> None:
        """
        The dictionary stage holds the words in `held`, grouped by the reason
        for which each was last turned away, and waits `patience_left_seconds`
        more at most for the dictionary to answer them.

        The next of them that sleeps asks in `retry_in_seconds`, which is
        `None` once none sleeps.

        `ending` says that a held word has left and the `looked_up` for it
        follows at once, so a listener that draws what it knows draws it
        there, and never between the two.
        """

        ...
