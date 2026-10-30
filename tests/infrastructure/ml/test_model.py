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

from pydantic import (
    ValidationError,
)
from pytest import (
    MonkeyPatch,
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
from wordwinnow.infrastructure.ml import (
    model,
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
    installed_sklearn_version,
    is_stale,
    load,
    metadata_path,
    read_metadata,
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
class TestStale:
    def test_a_model_is_stale_when_another_version_of_scikit_learn_trained_it(
        self,
        *,
        boosted: SklearnLevelEstimator,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        assert boosted.metadata.sklearn_version == installed_sklearn_version()

        assert not is_stale(
            metadata=boosted.metadata,
        )

        assert is_stale(
            metadata=stale_model.metadata,
        )


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


@final
class TestReadMetadata:
    def test_a_missing_file_is_no_model(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        assert (
            read_metadata(
                path=tmp_path / "nonexistent.joblib",
            )
            is None
        )

    def test_it_reads_the_sidecar_without_opening_the_model(
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

        path.write_bytes(
            data=b"foo",
        )

        assert (
            read_metadata(
                path=path,
            )
            == boosted.metadata
        )

        with raises(
            expected_exception=ModelFileError,
            match="cannot be read",
        ):
            load(
                path=path,
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
            match="requires a metadata sidecar",
        ):
            read_metadata(
                path=path,
            )

    @mark.parametrize(
        argnames="contents",
        argvalues=(
            b"",
            b"{not json",
            b"{}",
            b"[]",
            b"\xff\xfe\x00",
        ),
    )
    def test_a_sidecar_that_holds_no_model_metadata_is_refused_with_the_validation_error_as_its_cause(
        self,
        *,
        tmp_path: Path,
        boosted: SklearnLevelEstimator,
        contents: bytes,
    ) -> None:
        path = tmp_path / "model.joblib"

        save(
            estimator=boosted,
            path=path,
        )

        sidecar = metadata_path(
            path=path,
        )

        sidecar.write_bytes(
            data=contents,
        )

        with raises(
            expected_exception=ModelFileError,
            match="does not hold a model's metadata",
        ) as caught:
            read_metadata(
                path=path,
            )

        assert isinstance(
            caught.value.__cause__,
            ValidationError,
        )

        assert str(
            object=sidecar,
        ) in str(
            object=caught.value,
        )

        with raises(
            expected_exception=ModelFileError,
            match="does not hold a model's metadata",
        ):
            load(
                path=path,
            )


@final
class TestDamagedModelFile:
    @mark.parametrize(
        argnames="length",
        argvalues=(
            0,
            1,
            5,
            100,
            998,
            50_000,
        ),
    )
    def test_a_model_cut_short_is_refused_whatever_the_length(
        self,
        *,
        tmp_path: Path,
        boosted: SklearnLevelEstimator,
        length: int,
    ) -> None:
        path = tmp_path / "model.joblib"

        save(
            estimator=boosted,
            path=path,
        )

        whole = path.read_bytes()

        assert (
            len(
                whole,
            )
            > length
        )

        path.write_bytes(
            data=whole[:length],
        )

        with raises(
            expected_exception=ModelFileError,
            match=r"cannot be read \(\w+\)$",
        ) as caught:
            load(
                path=path,
            )

        assert caught.value.__cause__ is not None

    def test_a_file_that_was_never_a_model_is_refused(
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

        path.write_bytes(
            data=b"not a pickle",
        )

        with raises(
            expected_exception=ModelFileError,
            match=r"cannot be read \(\w+\)$",
        ):
            load(
                path=path,
            )


_MODEL: Final = "model.joblib"

_SIDECAR: Final = "model.joblib.metadata.json"


def _names(
    directory: Path,
    /,
) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


@final
class TestSavingStopsPartWay:
    def test_a_save_leaves_only_the_model_and_its_sidecar(
        self,
        *,
        tmp_path: Path,
        boosted: SklearnLevelEstimator,
    ) -> None:
        save(
            estimator=boosted,
            path=tmp_path / _MODEL,
        )

        assert _names(
            tmp_path,
        ) == [
            _MODEL,
            _SIDECAR,
        ]

    def test_a_second_save_replaces_both_files(
        self,
        *,
        tmp_path: Path,
        boosted: SklearnLevelEstimator,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / _MODEL

        save(
            estimator=boosted,
            path=path,
        )

        save(
            estimator=stale_model,
            path=path,
        )

        assert (
            read_metadata(
                path=path,
            )
            == stale_model.metadata
        )

        assert _names(
            tmp_path,
        ) == [
            _MODEL,
            _SIDECAR,
        ]

    def test_a_save_stopped_while_the_model_is_written_leaves_the_earlier_pair_as_it_was(
        self,
        *,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
        boosted: SklearnLevelEstimator,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / _MODEL

        sidecar = tmp_path / _SIDECAR

        save(
            estimator=boosted,
            path=path,
        )

        before = (
            path.read_bytes(),
            sidecar.read_bytes(),
        )

        def run_out_of_space(
            *,
            value: object,
            filename: Path,
        ) -> None:
            filename.write_bytes(
                data=b"half a model",
            )

            raise OSError(
                "no space left on device",
            )

        monkeypatch.setattr(
            target=model,
            name="dump",
            value=run_out_of_space,
        )

        with raises(
            expected_exception=OSError,
            match="no space left",
        ):
            save(
                estimator=stale_model,
                path=path,
            )

        assert (
            path.read_bytes(),
            sidecar.read_bytes(),
        ) == before

        assert _names(
            tmp_path,
        ) == [
            _MODEL,
            _SIDECAR,
        ]

        assert (
            read_metadata(
                path=path,
            )
            == boosted.metadata
        )

    def test_a_first_save_stopped_while_the_model_is_written_leaves_no_file(
        self,
        *,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
        boosted: SklearnLevelEstimator,
    ) -> None:
        def run_out_of_space(
            *,
            value: object,
            filename: Path,
        ) -> None:
            filename.write_bytes(
                data=b"half a model",
            )

            raise OSError(
                "no space left on device",
            )

        monkeypatch.setattr(
            target=model,
            name="dump",
            value=run_out_of_space,
        )

        with raises(
            expected_exception=OSError,
            match="no space left",
        ):
            save(
                estimator=boosted,
                path=tmp_path / _MODEL,
            )

        assert (
            tuple(
                tmp_path.iterdir(),
            )
            == ()
        )

        assert (
            load(
                path=tmp_path / _MODEL,
            )
            is None
        )

    def test_a_save_stopped_while_the_sidecar_is_written_leaves_the_earlier_pair_as_it_was(
        self,
        *,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
        boosted: SklearnLevelEstimator,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / _MODEL

        sidecar = tmp_path / _SIDECAR

        save(
            estimator=boosted,
            path=path,
        )

        before = (
            path.read_bytes(),
            sidecar.read_bytes(),
        )

        write_text = Path.write_text

        def run_out_of_space(
            self: Path,
            *,
            data: str,
            encoding: str | None = None,
        ) -> int:
            if self.name.endswith(
                ".part",
            ):
                raise OSError(
                    "no space left on device",
                )

            return write_text(
                self,
                data=data,
                encoding=encoding,
            )

        monkeypatch.setattr(
            target=Path,
            name="write_text",
            value=run_out_of_space,
        )

        with raises(
            expected_exception=OSError,
            match="no space left",
        ):
            save(
                estimator=stale_model,
                path=path,
            )

        assert (
            path.read_bytes(),
            sidecar.read_bytes(),
        ) == before

        assert _names(
            tmp_path,
        ) == [
            _MODEL,
            _SIDECAR,
        ]

    def test_a_save_stopped_as_the_files_are_put_in_place_leaves_a_model_without_a_sidecar_the_next_save_repairs(
        self,
        *,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
        boosted: SklearnLevelEstimator,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        path = tmp_path / _MODEL

        sidecar = tmp_path / _SIDECAR

        save(
            estimator=boosted,
            path=path,
        )

        replace = Path.replace

        def stop_at_the_sidecar(
            self: Path,
            *,
            target: Path,
        ) -> Path:
            if target == sidecar:
                raise OSError(
                    "stopped",
                )

            return replace(
                self,
                target=target,
            )

        with monkeypatch.context() as scoped:
            scoped.setattr(
                target=Path,
                name="replace",
                value=stop_at_the_sidecar,
            )

            with raises(
                expected_exception=OSError,
                match="stopped",
            ):
                save(
                    estimator=stale_model,
                    path=path,
                )

        assert _names(
            tmp_path,
        ) == [
            _MODEL,
        ]

        with raises(
            expected_exception=ModelFileError,
            match="requires a metadata sidecar",
        ):
            load(
                path=path,
            )

        save(
            estimator=stale_model,
            path=path,
        )

        loaded = load(
            path=path,
        )

        assert loaded is not None

        assert loaded.metadata == stale_model.metadata
