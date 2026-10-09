"""
The learner, as much of them as the analysis needs.

A profile is a runtime policy handed to one analysis, not an account: the
level the learner says they have, the level they are working toward, and the
lemmas they already know.
"""

from collections.abc import (
    Iterable,
)
from dataclasses import (
    dataclass,
    field,
)
from typing import (
    final,
)

from wordwinnow.domain.cefr import (
    LEVELS_ASCENDING,
    CefrLevel,
)


@final
class InvalidLearnerProfileError(
    ValueError,
):
    """
    Raised when a learner profile would contain invalid data.
    """


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LearnerProfile:
    """
    What the analysis knows about the learner, and all it will ever use.

    `level` is declared by the learner and never inferred, `target_level` is
    at or above it and bounds the words the learner should study first, and
    `known_lemmas` are the words the learner has already learned, in
    lowercase.
    """

    level: CefrLevel

    target_level: CefrLevel

    known_lemmas: frozenset[str] = field(
        default_factory=frozenset,
    )

    def __post_init__(
        self,
    ) -> None:
        if self.target_level < self.level:
            raise InvalidLearnerProfileError(
                f"a learner profile requires a target level at or above {self.level}, got {self.target_level}",
            )

        # NOTE:
        # `knows()` compares in lowercase, so a known lemma in any other case would never match.
        not_lowercase = sorted(lemma for lemma in self.known_lemmas if lemma != lemma.lower())

        if not_lowercase:
            raise InvalidLearnerProfileError(
                f"a learner profile requires known lemmas in lowercase, got {not_lowercase}",
            )

    def knows(
        self,
        *,
        lemma: str,
    ) -> bool:
        """
        Whether the learner marked the lemma as already known.
        """

        return lemma.lower() in self.known_lemmas


def default_target_level(
    *,
    level: CefrLevel,
) -> CefrLevel:
    """
    The level a learner is taken to be working toward when they name none: one
    above their own, or C2 when they are already there.
    """

    index = LEVELS_ASCENDING.index(
        level,
    )

    return LEVELS_ASCENDING[
        min(
            index + 1,
            len(
                LEVELS_ASCENDING,
            )
            - 1,
        )
    ]


def known_lemmas_from_lines(
    *,
    lines: Iterable[str],
) -> frozenset[str]:
    """
    Read a learner's known vocabulary from lines of text, one lemma each.

    Blank lines and lines starting with `#` are ignored, and case does not
    matter.
    """

    return frozenset(
        stripped.lower()
        for line in lines
        if (stripped := line.strip())
        and not stripped.startswith(
            "#",
        )
    )
