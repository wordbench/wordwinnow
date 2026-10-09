"""
The dictionary in place when an analysis runs without one.
"""

from typing import (
    final,
)

from wordwinnow.domain.dictionary import (
    DictionaryLookup,
)


@final
class DisabledDictionary:
    """
    A dictionary that is never asked, because the analysis options say the
    dictionary is off.

    Satisfies `Dictionary` structurally, and raises if the pipeline asks it
    anyway, which would be a defect in the pipeline.
    """

    async def look_up(
        self,
        *,
        lemma: str,
    ) -> DictionaryLookup:
        raise RuntimeError(
            f"the dictionary is disabled, so {lemma!r} cannot be looked up",
        )
