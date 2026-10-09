"""
The labeled dataset behind the level experiment: one example per reference
entry, carrying the features a model may see and the level the list assigns.

A level read from a list is an external label, a judgment the list's compilers
made about the word.

The frequency heuristic's answer for the same word is a pseudo-label, an
output of the project's own code, and the two are kept apart by name so that
agreement with one is never reported as agreement with the other.
"""

from collections.abc import (
    Sequence,
)
from dataclasses import (
    dataclass,
)
from random import (
    Random,
)
from types import (
    MappingProxyType,
)
from typing import (
    Protocol,
    final,
)

from wordwinnow.application.ports.lexical_semantics import (
    LexicalSemantics,
)
from wordwinnow.application.ports.word_frequency import (
    WordFrequency,
)
from wordwinnow.domain.cefr import (
    DEFAULT_FREQUENCY_THRESHOLDS,
    LEVELS_ASCENDING,
    CefrLevel,
    FrequencyThresholds,
    estimate_level_from_frequency,
)
from wordwinnow.domain.difficulty import (
    LexicalFeatures,
    build_lexical_features,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    ReferenceEntry,
)
from wordwinnow.infrastructure.ml.model import (
    fingerprint,
)


@final
class DatasetError(
    ValueError,
):
    """
    Raised when a dataset cannot be built or split as asked.
    """


class ReferenceEntries(
    Protocol,
):
    """
    A reference list that can hand over every entry, which is what the
    experiment learns from.
    """

    def entries(
        self,
    ) -> tuple[ReferenceEntry, ...]:
        """
        Every entry, one per lemma and part of speech.
        """

        ...


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class LabeledExample:
    """
    One word with its features, its external label, and the list the label
    came from.
    """

    features: LexicalFeatures

    level: CefrLevel

    source: str


def build_dataset(
    *,
    reference_lexicon: ReferenceEntries,
    frequency: WordFrequency,
    lexical_semantics: LexicalSemantics,
) -> tuple[LabeledExample, ...]:
    """
    One example per reference entry, in the lexicon's own order.
    """

    return tuple(
        LabeledExample(
            features=build_lexical_features(
                lemma=entry.lemma,
                part_of_speech=entry.part_of_speech,
                zipf_frequency=frequency.zipf(
                    lemma=entry.lemma,
                ),
                senses=lexical_semantics.senses_of(
                    lemma=entry.lemma,
                    part_of_speech=entry.part_of_speech,
                ),
            ),
            level=entry.level,
            source=entry.source,
        )
        for entry in reference_lexicon.entries()
    )


def pseudo_label(
    *,
    example: LabeledExample,
    thresholds: FrequencyThresholds = DEFAULT_FREQUENCY_THRESHOLDS,
) -> CefrLevel:
    """
    The level the frequency heuristic assigns to the example's word.

    This is the project's own answer rather than a judgment from outside it,
    so agreement with it is fidelity, never validity.
    """

    return estimate_level_from_frequency(
        zipf_frequency=example.features.zipf_frequency,
        thresholds=thresholds,
    )


def relabel_with_pseudo_labels(
    *,
    examples: Sequence[LabeledExample],
    thresholds: FrequencyThresholds = DEFAULT_FREQUENCY_THRESHOLDS,
) -> tuple[LabeledExample, ...]:
    """
    The same examples with every external label replaced by the heuristic's
    pseudo-label.
    """

    return tuple(
        LabeledExample(
            features=example.features,
            level=pseudo_label(
                example=example,
                thresholds=thresholds,
            ),
            source=example.source,
        )
        for example in examples
    )


def dataset_fingerprint(
    *,
    examples: Sequence[LabeledExample],
) -> str:
    """
    A digest of the labeled words, independent of their order.
    """

    return fingerprint(
        features=[example.features for example in examples],
        levels=[example.level for example in examples],
    )


def class_distribution(
    *,
    examples: Sequence[LabeledExample],
) -> MappingProxyType[CefrLevel, int]:
    """
    How many examples carry each level, every level present.
    """

    counts = dict.fromkeys(
        LEVELS_ASCENDING,
        0,
    )

    for example in examples:
        counts[example.level] += 1

    return MappingProxyType(
        mapping=counts,
    )


def split(
    *,
    examples: Sequence[LabeledExample],
    test_fraction: float,
    seed: int,
) -> tuple[tuple[LabeledExample, ...], tuple[LabeledExample, ...]]:
    """
    Divide the examples into a training part and a test part, level by level.

    Each level contributes the same fraction of its examples to the test part,
    so a rare level is neither missing from that part nor concentrated in it.

    The same examples, fraction, and seed always produce the same split.
    """

    if not 0.0 < test_fraction < 1.0:
        raise DatasetError(
            f"a split requires a test fraction strictly between 0 and 1, got {test_fraction}",
        )

    generator = Random(
        x=seed,
    )

    train: list[LabeledExample] = []

    test: list[LabeledExample] = []

    for level in LEVELS_ASCENDING:
        members = [example for example in examples if example.level is level]

        generator.shuffle(
            x=members,
        )

        test_count = round(
            number=len(
                members,
            )
            * test_fraction,
        )

        test.extend(
            members[:test_count],
        )

        train.extend(
            members[test_count:],
        )

    return (
        tuple(
            train,
        ),
        tuple(
            test,
        ),
    )
