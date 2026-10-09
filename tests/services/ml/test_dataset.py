"""
The labeled dataset: how it is built from the ports, how it is relabeled with
pseudo-labels, and how it is split.
"""

from typing import (
    Final,
    final,
)

from pytest import (
    raises,
)

from tests.fakes.ports import (
    FakeLexicalSemantics,
    FakeReferenceLexicon,
    FakeWordFrequency,
)
from tests.services.ml.synthetic import (
    synthetic_examples,
)
from wordwinnow.domain.cefr import (
    LEVELS_ASCENDING,
    CefrLevel,
    FrequencyThresholds,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.services.ml.dataset import (
    DatasetError,
    LabeledExample,
    build_dataset,
    class_distribution,
    dataset_fingerprint,
    pseudo_label,
    relabel_with_pseudo_labels,
    split,
)

# NOTE:
# The first two of WordNet 3.0's senses of `note`, with their real counts: a written record and the kind of short
# letter that brings the King of Bohemia to Baker Street.
_NOTE_SENSES: Final = (
    LexicalSense(
        key="note.n.01",
        part_of_speech=PartOfSpeech.NOUN,
        gloss="a brief written record",
        example="he made a note of the appointment",
        synonyms=(),
        hypernyms=("written record",),
        antonyms=(),
        usage_count=14,
        hyponym_count=4,
        category="noun.communication",
    ),
    LexicalSense(
        key="note.n.02",
        part_of_speech=PartOfSpeech.NOUN,
        gloss="a short personal letter",
        example="drop me a line when you get there",
        synonyms=(
            "short letter",
            "line",
            "billet",
        ),
        hypernyms=("personal letter",),
        antonyms=(),
        usage_count=13,
        hyponym_count=1,
        category="noun.communication",
    ),
)


@final
class TestBuildDataset:
    def test_one_example_per_entry_with_the_ports_answers(
        self,
    ) -> None:
        examples = build_dataset(
            reference_lexicon=FakeReferenceLexicon(
                levels={
                    (
                        "note",
                        PartOfSpeech.NOUN,
                    ): CefrLevel.A1,
                    (
                        "recess",
                        PartOfSpeech.NOUN,
                    ): CefrLevel.C1,
                },
            ),
            frequency=FakeWordFrequency(
                frequencies={
                    "note": 5.04,
                },
            ),
            lexical_semantics=FakeLexicalSemantics(
                senses={
                    (
                        "note",
                        PartOfSpeech.NOUN,
                    ): _NOTE_SENSES,
                },
            ),
        )

        assert tuple(example.features.lemma for example in examples) == (
            "note",
            "recess",
        )

        assert examples[0].features.zipf_frequency == 5.04

        assert examples[0].features.sense_count == 2

        assert examples[0].features.usage_count == 27

        assert examples[0].features.category == "noun.communication"

        assert examples[0].level is CefrLevel.A1

        assert examples[0].source == "fake"

        assert examples[1].features.zipf_frequency == 0.0

        assert examples[1].features.sense_count == 0

        assert examples[1].features.category is None

        assert examples[1].level is CefrLevel.C1


@final
class TestPseudoLabels:
    def test_the_pseudo_label_is_the_heuristic_answer_for_the_given_thresholds(
        self,
    ) -> None:
        example = synthetic_examples(
            count=1,
            seed=0,
        )[0]

        zipf = example.features.zipf_frequency

        assert zipf > 5.5

        assert (
            pseudo_label(
                example=example,
            )
            is CefrLevel.A1
        )

        assert (
            pseudo_label(
                example=example,
                thresholds=FrequencyThresholds(
                    boundaries=(
                        zipf + 1.5,
                        zipf + 1.0,
                        zipf + 0.5,
                        zipf - 0.5,
                        zipf - 1.0,
                    ),
                ),
            )
            is CefrLevel.B2
        )

    def test_relabeling_replaces_only_the_level(
        self,
    ) -> None:
        examples = synthetic_examples(
            count=12,
            seed=0,
        )

        relabeled = relabel_with_pseudo_labels(
            examples=examples,
        )

        assert tuple(example.features for example in relabeled) == tuple(example.features for example in examples)

        assert tuple(example.source for example in relabeled) == tuple(example.source for example in examples)

        assert tuple(example.level for example in relabeled) == tuple(
            pseudo_label(
                example=example,
            )
            for example in examples
        )


@final
class TestDatasetFingerprint:
    def test_the_fingerprint_ignores_order_and_notices_a_label(
        self,
    ) -> None:
        examples = synthetic_examples(
            count=12,
            seed=0,
        )

        reversed_examples = tuple(
            reversed(
                examples,
            ),
        )

        relabeled = (
            LabeledExample(
                features=examples[0].features,
                level=CefrLevel.C2,
                source=examples[0].source,
            ),
            *examples[1:],
        )

        assert dataset_fingerprint(
            examples=examples,
        ) == dataset_fingerprint(
            examples=reversed_examples,
        )

        assert dataset_fingerprint(
            examples=examples,
        ) != dataset_fingerprint(
            examples=relabeled,
        )


@final
class TestClassDistribution:
    def test_every_level_is_counted_even_when_absent(
        self,
    ) -> None:
        distribution = class_distribution(
            examples=synthetic_examples(
                count=7,
                seed=0,
            ),
        )

        assert (
            tuple(
                distribution,
            )
            == LEVELS_ASCENDING
        )

        assert distribution[CefrLevel.A1] == 2

        assert distribution[CefrLevel.C2] == 1


@final
class TestSplit:
    def test_the_split_is_stratified_complete_and_disjoint(
        self,
    ) -> None:
        examples = synthetic_examples(
            count=60,
            seed=0,
        )

        (
            training,
            test,
        ) = split(
            examples=examples,
            test_fraction=0.2,
            seed=42,
        )

        assert len(
            training,
        ) + len(
            test,
        ) == len(
            examples,
        )

        assert frozenset(
            training,
        ).isdisjoint(
            test,
        )

        for level in LEVELS_ASCENDING:
            assert sum(1 for example in test if example.level is level) == 2

            assert sum(1 for example in training if example.level is level) == 8

    def test_the_same_seed_gives_the_same_split(
        self,
    ) -> None:
        examples = synthetic_examples(
            count=60,
            seed=0,
        )

        first = split(
            examples=examples,
            test_fraction=0.2,
            seed=42,
        )

        second = split(
            examples=examples,
            test_fraction=0.2,
            seed=42,
        )

        other = split(
            examples=examples,
            test_fraction=0.2,
            seed=43,
        )

        assert first == second

        assert first != other

    def test_a_fraction_that_leaves_no_part_is_refused(
        self,
    ) -> None:
        examples = synthetic_examples(
            count=6,
            seed=0,
        )

        with raises(
            expected_exception=DatasetError,
        ):
            split(
                examples=examples,
                test_fraction=1.0,
                seed=42,
            )
