"""
How a word's features become the numbers a scikit-learn estimator reads.

The columns follow from the feature families a model is given, in one fixed
order, and the layout is recorded with every saved model so a model trained on
one layout is never fed another.
"""

from collections.abc import (
    Sequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from typing import (
    Final,
)

from numpy import (
    float64,
    zeros,
)
from numpy.typing import (
    NDArray,
)

from wordwinnow.domain.difficulty import (
    ALL_FAMILIES,
    FeatureFamily,
    LexicalFeatures,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)

# NOTE:
# The semantic categories WordNet 3.0 files its senses under, its 45 lexicographer files as `Synset.lexname()` names
# them, which is the closed vocabulary a sense's `category` takes; a word without a sense, or a category outside the
# list, encodes as no category at all.
CATEGORIES: Final = (
    "adj.all",
    "adj.pert",
    "adj.ppl",
    "adv.all",
    "noun.Tops",
    "noun.act",
    "noun.animal",
    "noun.artifact",
    "noun.attribute",
    "noun.body",
    "noun.cognition",
    "noun.communication",
    "noun.event",
    "noun.feeling",
    "noun.food",
    "noun.group",
    "noun.location",
    "noun.motive",
    "noun.object",
    "noun.person",
    "noun.phenomenon",
    "noun.plant",
    "noun.possession",
    "noun.process",
    "noun.quantity",
    "noun.relation",
    "noun.shape",
    "noun.state",
    "noun.substance",
    "noun.time",
    "verb.body",
    "verb.change",
    "verb.cognition",
    "verb.communication",
    "verb.competition",
    "verb.consumption",
    "verb.contact",
    "verb.creation",
    "verb.emotion",
    "verb.motion",
    "verb.perception",
    "verb.possession",
    "verb.social",
    "verb.stative",
    "verb.weather",
)

_FREQUENCY_COLUMNS: Final = ("zipf_frequency",)

_LEXICAL_COLUMNS: Final = (
    "character_count",
    *(f"part_of_speech_{part_of_speech}" for part_of_speech in PartOfSpeech),
)

_WORDNET_COLUMNS: Final = (
    "sense_count",
    "usage_count",
    "hyponym_count",
    *(f"category_{category}" for category in CATEGORIES),
)


def columns_for(
    *,
    families: Sequence[FeatureFamily],
) -> tuple[str, ...]:
    """
    The column layout of a model given these families, in family order.

    Raises `ValueError` when no family is given, because a model with no
    features has nothing to learn from.
    """

    chosen = frozenset(
        families,
    )

    if not chosen:
        raise ValueError(
            "a feature set requires at least one feature family",
        )

    columns: list[str] = []

    for family in ALL_FAMILIES:
        if family not in chosen:
            continue

        if family is FeatureFamily.FREQUENCY:
            columns.extend(
                _FREQUENCY_COLUMNS,
            )

        elif family is FeatureFamily.LEXICAL:
            columns.extend(
                _LEXICAL_COLUMNS,
            )

        else:
            columns.extend(
                _WORDNET_COLUMNS,
            )

    return tuple(
        columns,
    )


def encode(
    *,
    features: Sequence[LexicalFeatures],
    families: Sequence[FeatureFamily],
) -> NDArray[float64]:
    """
    One row per word, one column per entry of `columns_for(families)`.
    """

    chosen = frozenset(
        families,
    )

    columns = columns_for(
        families=families,
    )

    matrix = zeros(
        shape=(
            len(
                features,
            ),
            len(
                columns,
            ),
        ),
        dtype=float64,
    )

    for (
        row,
        item,
    ) in enumerate(
        iterable=features,
    ):
        matrix[row] = _row(
            item,
            chosen,
        )

    return matrix


def _row(
    item: LexicalFeatures,
    families: AbstractSet[FeatureFamily],
    /,
) -> tuple[float, ...]:
    values: list[float] = []

    if FeatureFamily.FREQUENCY in families:
        values.append(
            item.zipf_frequency,
        )

    if FeatureFamily.LEXICAL in families:
        values.append(
            item.character_count,
        )

        values.extend(1.0 if item.part_of_speech is part_of_speech else 0.0 for part_of_speech in PartOfSpeech)

    if FeatureFamily.WORDNET in families:
        values.extend(
            (
                item.sense_count,
                item.usage_count,
                item.hyponym_count,
            ),
        )

        values.extend(1.0 if item.category == category else 0.0 for category in CATEGORIES)

    return tuple(
        values,
    )
