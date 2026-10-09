"""
Scoring predictions against labels, and fitting the heuristic's boundaries.
"""

from typing import (
    final,
)

from pytest import (
    raises,
)

from tests.services.ml.synthetic import (
    synthetic_examples,
)
from wordwinnow.domain.cefr import (
    DEFAULT_FREQUENCY_THRESHOLDS,
    LEVELS_ASCENDING,
    CefrLevel,
    FrequencyThresholds,
)
from wordwinnow.infrastructure.ml.evaluation import (
    EvaluationError,
    evaluate,
    tune_thresholds,
)
from wordwinnow.services.ml.dataset import (
    pseudo_label,
)


@final
class TestEvaluate:
    def test_perfect_predictions_score_one_with_a_diagonal_confusion(
        self,
    ) -> None:
        report = evaluate(
            expected=LEVELS_ASCENDING,
            predicted=LEVELS_ASCENDING,
        )

        assert report.accuracy == 1.0

        assert report.macro_f1 == 1.0

        assert report.within_one_level_accuracy == 1.0

        assert report.mean_absolute_level_error == 0.0

        assert report.spearman == 1.0

        assert report.n == 6

        assert report.labels == LEVELS_ASCENDING

        assert tuple(entry.level for entry in report.per_class) == LEVELS_ASCENDING

        assert all(entry.recall == 1.0 and entry.support == 1 for entry in report.per_class)

        assert all(
            report.confusion[index][index] == 1
            for index in range(
                6,
            )
        )

        assert (
            sum(
                sum(
                    row,
                )
                for row in report.confusion
            )
            == 6
        )

    def test_predictions_one_level_off_are_within_one_level(
        self,
    ) -> None:
        report = evaluate(
            expected=(
                CefrLevel.A1,
                CefrLevel.B1,
                CefrLevel.C1,
            ),
            predicted=(
                CefrLevel.A2,
                CefrLevel.B2,
                CefrLevel.C2,
            ),
        )

        assert report.accuracy == 0.0

        assert report.within_one_level_accuracy == 1.0

        assert report.mean_absolute_level_error == 1.0

        assert report.spearman == 1.0

        assert report.confusion[0][1] == 1

    def test_a_far_miss_counts_its_distance_and_a_constant_prediction_has_no_correlation(
        self,
    ) -> None:
        report = evaluate(
            expected=(
                CefrLevel.A1,
                CefrLevel.A1,
            ),
            predicted=(
                CefrLevel.A1,
                CefrLevel.C2,
            ),
        )

        assert report.within_one_level_accuracy == 0.5

        assert report.mean_absolute_level_error == 2.5

        assert report.spearman == 0.0

    def test_mismatched_or_empty_inputs_are_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=EvaluationError,
        ):
            evaluate(
                expected=(CefrLevel.A1,),
                predicted=(),
            )

        with raises(
            expected_exception=EvaluationError,
        ):
            evaluate(
                expected=(),
                predicted=(),
            )


@final
class TestTuneThresholds:
    def test_the_tuned_boundaries_are_descending_and_never_score_worse(
        self,
    ) -> None:
        examples = synthetic_examples(
            count=120,
            seed=0,
        )

        tuned = tune_thresholds(
            zipf_frequencies=[example.features.zipf_frequency for example in examples],
            levels=[example.level for example in examples],
        )

        assert isinstance(
            tuned,
            FrequencyThresholds,
        )

        assert list(
            tuned.boundaries,
        ) == sorted(
            tuned.boundaries,
            reverse=True,
        )

        expected = tuple(example.level for example in examples)

        before = evaluate(
            expected=expected,
            predicted=[
                pseudo_label(
                    example=example,
                    thresholds=DEFAULT_FREQUENCY_THRESHOLDS,
                )
                for example in examples
            ],
        )

        after = evaluate(
            expected=expected,
            predicted=[
                pseudo_label(
                    example=example,
                    thresholds=tuned,
                )
                for example in examples
            ],
        )

        assert after.mean_absolute_level_error <= before.mean_absolute_level_error

        assert after.accuracy > 0.8

    def test_no_labels_and_mismatched_lengths_are_refused(
        self,
    ) -> None:
        with raises(
            expected_exception=EvaluationError,
        ):
            tune_thresholds(
                zipf_frequencies=(),
                levels=(),
            )

        with raises(
            expected_exception=EvaluationError,
        ):
            tune_thresholds(
                zipf_frequencies=(
                    1.0,
                    2.0,
                ),
                levels=(CefrLevel.A1,),
            )
