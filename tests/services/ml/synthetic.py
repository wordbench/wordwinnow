"""
A synthetic labeled dataset with a structure a model can learn: a word of a
higher level is rarer, longer, has fewer senses, and sits in a rarer semantic
category.

Nothing in it is a real word, so a score on it says only that the code runs
and learns, never anything about CEFR levels.
"""

from random import (
    Random,
)
from typing import (
    Final,
)

from wordwinnow.domain.cefr import (
    LEVELS_ASCENDING,
)
from wordwinnow.domain.difficulty import (
    build_lexical_features,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.services.ml.dataset import (
    LabeledExample,
)

# NOTE:
# Two source names, split the way the real lists are: the lower four levels from one, the upper two from the other.
LOWER_SOURCE: Final = "cefrj.csv"

UPPER_SOURCE: Final = "octanove.csv"

_CATEGORIES: Final = (
    "noun.food",
    "noun.artifact",
    "noun.act",
    "noun.state",
    "noun.attribute",
    "noun.cognition",
)


def synthetic_examples(
    *,
    count: int,
    seed: int,
) -> tuple[LabeledExample, ...]:
    """
    `count` examples cycling through the six levels, one part of speech after
    another, deterministic for a seed.
    """

    generator = Random(
        x=seed,
    )

    parts = tuple(
        PartOfSpeech,
    )

    examples: list[LabeledExample] = []

    for index in range(
        count,
    ):
        level = LEVELS_ASCENDING[
            index
            % len(
                LEVELS_ASCENDING,
            )
        ]

        rank = level.rank

        part_of_speech = parts[
            index
            % len(
                parts,
            )
        ]

        sense_count = max(
            1,
            8
            - rank
            + generator.randint(
                a=-1,
                b=1,
            ),
        )

        senses = tuple(
            LexicalSense(
                key=f"foo{index}.{part_of_speech[0]}.{position:02d}",
                part_of_speech=part_of_speech,
                gloss="a gloss",
                example=None,
                synonyms=(),
                hypernyms=(),
                antonyms=(),
                usage_count=max(
                    0,
                    20 - 3 * rank - position,
                ),
                hyponym_count=max(
                    0,
                    6 - rank,
                ),
                category=_CATEGORIES[rank - 1],
            )
            for position in range(
                sense_count,
            )
        )

        examples.append(
            LabeledExample(
                features=build_lexical_features(
                    lemma="ba"
                    * (
                        rank
                        + generator.randint(
                            a=0,
                            b=1,
                        )
                    ),
                    part_of_speech=part_of_speech,
                    zipf_frequency=7.0
                    - 0.8 * rank
                    + generator.uniform(
                        a=-0.4,
                        b=0.4,
                    ),
                    senses=senses,
                ),
                level=level,
                source=LOWER_SOURCE if rank <= 4 else UPPER_SOURCE,
            ),
        )

    return tuple(
        examples,
    )
