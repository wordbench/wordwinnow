"""
The scikit-learn estimator: training, cross-validation, and the model file
with its metadata sidecar.
"""

from json import (
    loads,
)
from pathlib import (
    Path,
)
from typing import (
    Final,
    final,
)

from pytest import (
    fixture,
    mark,
    raises,
)

from tests.services.ml.synthetic import (
    synthetic_examples,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.difficulty import (
    ALL_FAMILIES,
    FeatureFamily,
)
from wordwinnow.infrastructure.ml.evaluation import (
    evaluate,
)
from wordwinnow.infrastructure.ml.features import (
    columns_for,
)
from wordwinnow.infrastructure.ml.model import (
    ModelFileError,
    ModelKind,
    NoLevelEstimator,
    SklearnLevelEstimator,
    TrainingError,
    cross_validate,
    fingerprint,
    group_folds,
    load,
    metadata_path,
    save,
    train,
)
from wordwinnow.services.ml.dataset import (
    LabeledExample,
)

_SEED: Final = 42


@fixture(
    scope="module",
)
def examples() -> tuple[LabeledExample, ...]:
    return synthetic_examples(
        count=120,
        seed=0,
    )


@fixture(
    scope="module",
)
def boosted(
    *,
    examples: tuple[LabeledExample, ...],
) -> SklearnLevelEstimator:
    return train(
        features=[example.features for example in examples],
        levels=[example.level for example in examples],
        families=ALL_FAMILIES,
        model_kind=ModelKind.GRADIENT_BOOSTING,
        seed=_SEED,
    )


@final
class TestTrain:
    def test_a_fitted_model_learns_the_structure(
        self,
        *,
        examples: tuple[LabeledExample, ...],
        boosted: SklearnLevelEstimator,
    ) -> None:
        report = evaluate(
            expected=[example.level for example in examples],
            predicted=boosted.estimate_many(
                features=[example.features for example in examples],
            ),
        )

        assert report.accuracy >= 0.9

    @mark.parametrize(
        argnames="model_kind",
        argvalues=tuple(
            ModelKind,
        ),
    )
    def test_every_kind_trains_answers_a_level_and_records_its_provenance(
        self,
        *,
        examples: tuple[LabeledExample, ...],
        model_kind: ModelKind,
    ) -> None:
        estimator = train(
            features=[example.features for example in examples],
            levels=[example.level for example in examples],
            families=(
                FeatureFamily.FREQUENCY,
                FeatureFamily.WORDNET,
            ),
            model_kind=model_kind,
            seed=_SEED,
        )

        assert estimator.metadata.model_kind is model_kind

        assert estimator.metadata.families == (
            FeatureFamily.FREQUENCY,
            FeatureFamily.WORDNET,
        )

        assert estimator.metadata.feature_columns == columns_for(
            families=(
                FeatureFamily.FREQUENCY,
                FeatureFamily.WORDNET,
            ),
        )

        assert estimator.metadata.dataset_fingerprint == fingerprint(
            features=[example.features for example in examples],
            levels=[example.level for example in examples],
        )

        assert estimator.metadata.seed == _SEED

        assert estimator.metadata.train_size == 120

        assert estimator.metadata.metrics == {}

        levels = estimator.estimate_many(
            features=[example.features for example in examples[:5]],
        )

        assert (
            len(
                levels,
            )
            == 5
        )

        assert all(
            isinstance(
                level,
                CefrLevel,
            )
            for level in levels
        )

    def test_examples_of_one_level_and_mismatched_lengths_are_refused(
        self,
        *,
        examples: tuple[LabeledExample, ...],
    ) -> None:
        one_level = tuple(example for example in examples if example.level is CefrLevel.A1)

        with raises(
            expected_exception=TrainingError,
        ):
            train(
                features=[example.features for example in one_level],
                levels=[example.level for example in one_level],
                families=ALL_FAMILIES,
                model_kind=ModelKind.LOGISTIC_REGRESSION,
                seed=_SEED,
            )

        with raises(
            expected_exception=TrainingError,
        ):
            train(
                features=[example.features for example in examples],
                levels=[example.level for example in examples[:-1]],
                families=ALL_FAMILIES,
                model_kind=ModelKind.LOGISTIC_REGRESSION,
                seed=_SEED,
            )

    def test_metrics_are_recorded_on_a_copy(
        self,
        *,
        boosted: SklearnLevelEstimator,
    ) -> None:
        scored = boosted.with_metrics(
            metrics={
                "accuracy": 0.5,
            },
        )

        assert scored.metadata.metrics == {
            "accuracy": 0.5,
        }

        assert boosted.metadata.metrics == {}

    def test_no_words_get_no_levels(
        self,
        *,
        boosted: SklearnLevelEstimator,
    ) -> None:
        assert (
            boosted.estimate_many(
                features=[],
            )
            == ()
        )


@final
class TestNoLevelEstimator:
    def test_it_answers_none_for_every_word(
        self,
        *,
        examples: tuple[LabeledExample, ...],
    ) -> None:
        assert NoLevelEstimator().estimate_many(
            features=[example.features for example in examples[:3]],
        ) == (
            None,
            None,
            None,
        )


@final
class TestCrossValidate:
    def test_one_report_per_fold(
        self,
        *,
        examples: tuple[LabeledExample, ...],
    ) -> None:
        reports = cross_validate(
            features=[example.features for example in examples],
            levels=[example.level for example in examples],
            groups=[example.features.lemma for example in examples],
            families=ALL_FAMILIES,
            model_kind=ModelKind.LOGISTIC_REGRESSION,
            folds=3,
            seed=_SEED,
        )

        assert (
            len(
                reports,
            )
            == 3
        )

        assert all(0.0 <= report.macro_f1 <= 1.0 for report in reports)

        assert sum(report.n for report in reports) == 120

    def test_fewer_than_two_folds_are_refused(
        self,
        *,
        examples: tuple[LabeledExample, ...],
    ) -> None:
        with raises(
            expected_exception=TrainingError,
        ):
            cross_validate(
                features=[example.features for example in examples],
                levels=[example.level for example in examples],
                groups=[example.features.lemma for example in examples],
                families=ALL_FAMILIES,
                model_kind=ModelKind.LOGISTIC_REGRESSION,
                folds=1,
                seed=_SEED,
            )


@final
class TestGroupFolds:
    def test_every_group_lies_in_one_fold_and_every_example_in_one(
        self,
        *,
        examples: tuple[LabeledExample, ...],
    ) -> None:
        groups = tuple(
            f"foo{index // 3}"
            for index in range(
                len(
                    examples,
                ),
            )
        )

        divisions = group_folds(
            levels=[example.level for example in examples],
            groups=groups,
            folds=5,
            seed=_SEED,
        )

        assert sorted(
            index
            for (
                _,
                fold,
            ) in divisions
            for index in fold
        ) == list(
            range(
                len(
                    examples,
                ),
            ),
        )

        for (
            others,
            fold,
        ) in divisions:
            assert frozenset(groups[index] for index in others).isdisjoint(groups[index] for index in fold)

            assert len(
                others,
            ) + len(
                fold,
            ) == len(
                examples,
            )

    def test_the_same_seed_gives_the_same_folds(
        self,
        *,
        examples: tuple[LabeledExample, ...],
    ) -> None:
        levels = [example.level for example in examples]

        groups = [example.features.lemma for example in examples]

        first = group_folds(
            levels=levels,
            groups=groups,
            folds=5,
            seed=_SEED,
        )

        second = group_folds(
            levels=levels,
            groups=groups,
            folds=5,
            seed=_SEED,
        )

        other = group_folds(
            levels=levels,
            groups=groups,
            folds=5,
            seed=_SEED + 1,
        )

        assert first == second

        assert first != other


@final
class TestModelFile:
    def test_a_saved_model_loads_back_with_its_metadata_and_answers(
        self,
        *,
        tmp_path: Path,
        examples: tuple[LabeledExample, ...],
        boosted: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / "models" / "model.joblib"

        save(
            estimator=boosted,
            path=path,
        )

        sidecar = metadata_path(
            path=path,
        )

        assert sidecar == tmp_path / "models" / "model.joblib.metadata.json"

        record = loads(
            s=sidecar.read_text(
                encoding="utf-8",
            ),
        )

        assert record["model_kind"] == "gradient_boosting"

        assert record["families"] == [
            "frequency",
            "lexical",
            "wordnet",
        ]

        loaded = load(
            path=path,
        )

        assert loaded is not None

        assert loaded.metadata == boosted.metadata

        features = tuple(example.features for example in examples[:20])

        assert loaded.estimate_many(
            features=features,
        ) == boosted.estimate_many(
            features=features,
        )

    def test_a_missing_file_is_no_model(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        assert (
            load(
                path=tmp_path / "nonexistent.joblib",
            )
            is None
        )

    def test_a_file_without_its_sidecar_is_refused(
        self,
        *,
        tmp_path: Path,
        boosted: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / "model.joblib"

        save(
            estimator=boosted,
            path=path,
        )

        metadata_path(
            path=path,
        ).unlink()

        with raises(
            expected_exception=ModelFileError,
        ):
            load(
                path=path,
            )

    def test_another_feature_layout_is_refused(
        self,
        *,
        tmp_path: Path,
        boosted: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / "model.joblib"

        save(
            estimator=boosted,
            path=path,
        )

        sidecar = metadata_path(
            path=path,
        )

        record = loads(
            s=sidecar.read_text(
                encoding="utf-8",
            ),
        )

        record["feature_columns"] = record["feature_columns"][:-1]

        sidecar.write_text(
            data=str(
                object=record,
            ).replace(
                "'",
                '"',
            ),
            encoding="utf-8",
        )

        with raises(
            expected_exception=ModelFileError,
            match="feature columns",
        ):
            load(
                path=path,
            )
