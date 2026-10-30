"""
The scikit-learn adapter behind the `LevelEstimator` port: how a model is
trained, scored fold by fold, saved with its provenance, and loaded back.

A saved model travels with a metadata sidecar naming the feature families and
columns it expects and the data it was trained on, so a model is never fed a
feature matrix it was not trained for.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from enum import (
    auto,
)
from hashlib import (
    sha256,
)
from importlib.metadata import (
    version,
)
from pathlib import (
    Path,
)
from pickle import (
    UnpicklingError,
)
from struct import (
    error as struct_error,
)
from typing import (
    Final,
    final,
)

from joblib import (
    dump,
)
from joblib import (
    load as load_joblib,
)
from numpy import (
    clip,
    int64,
    rint,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.linear_model import (
    LogisticRegression,
)
from sklearn.model_selection import (
    StratifiedGroupKFold,
)
from sklearn.pipeline import (
    Pipeline,
)
from sklearn.preprocessing import (
    StandardScaler,
)

from wordwinnow.domain.cefr import (
    LEVELS_ASCENDING,
    CefrLevel,
)
from wordwinnow.domain.difficulty import (
    FeatureFamily,
    LexicalFeatures,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.infrastructure.ml.evaluation import (
    EvaluationReport,
    evaluate,
)
from wordwinnow.infrastructure.ml.features import (
    columns_for,
    encode,
)

# NOTE:
# Hyperparameters are fixed rather than searched: the experiment compares model families and feature families on equal
# terms, and a search inside each family is deferred work the notebook names under its limitations.
_LOGISTIC_MAX_ITERATIONS: Final = 2_000

# NOTE:
# What `pickle` and joblib raise for bytes that are not a whole pickle: a file cut short, an empty one, or one that
# was never a model.
#
# A file that cannot be opened is no part of this, and neither is a pickle that names a module this scikit-learn
# lacks, which the sidecar's version tells apart before the pickle is opened.
_NOT_A_WHOLE_PICKLE: Final = (
    EOFError,
    IndexError,
    KeyError,
    struct_error,
    UnpicklingError,
    ValueError,
)


@final
class TrainingError(
    ValueError,
):
    """
    Raised when a model is asked to learn or to be scored with examples it
    cannot use.
    """


@final
class ModelFileError(
    ValueError,
):
    """
    Raised when a model file or its metadata sidecar cannot be used by this
    code.
    """


class ModelKind(
    UnorderedStrEnum,
):
    """
    The estimator families the experiment compares.

    The first two classify the six levels as unrelated classes; the third
    regresses the level's rank and rounds, which treats the scale as the
    ordinal one it is.
    """

    LOGISTIC_REGRESSION = auto()

    GRADIENT_BOOSTING = auto()

    ORDINAL_GRADIENT_BOOSTING = auto()


class ModelMetadata(
    BaseModel,
):
    """
    What a saved model says about itself: its kind, the feature families and
    columns it expects, the data it was trained on, and the held-out scores it
    earned.

    `metrics` is empty for a model saved before any evaluation.
    """

    model_config = ConfigDict(
        frozen=True,
    )

    model_kind: ModelKind

    families: tuple[FeatureFamily, ...]

    feature_columns: tuple[str, ...]

    dataset_fingerprint: str

    seed: int

    train_size: int

    metrics: dict[str, float] = Field(
        default_factory=dict,
    )

    sklearn_version: str


@final
class SklearnLevelEstimator:
    """
    Predicts a level with a fitted scikit-learn pipeline.

    Satisfies `LevelEstimator` structurally, and always has an answer.
    """

    def __init__(
        self,
        *,
        pipeline: Pipeline,
        metadata: ModelMetadata,
    ) -> None:
        self._pipeline: Final = pipeline

        self._metadata: Final = metadata

    @property
    def pipeline(
        self,
    ) -> Pipeline:
        return self._pipeline

    @property
    def metadata(
        self,
    ) -> ModelMetadata:
        return self._metadata

    def with_metrics(
        self,
        *,
        metrics: Mapping[str, float],
    ) -> SklearnLevelEstimator:
        """
        The same model with held-out scores recorded in its metadata.
        """

        return SklearnLevelEstimator(
            pipeline=self._pipeline,
            metadata=self._metadata.model_copy(
                update={
                    "metrics": dict(
                        metrics,
                    ),
                },
            ),
        )

    def estimate_many(
        self,
        *,
        features: Sequence[LexicalFeatures],
    ) -> tuple[CefrLevel, ...]:
        if not features:
            return ()

        predicted = self._pipeline.predict(
            X=encode(
                features=features,
                families=self._metadata.families,
            ),
        )

        # NOTE:
        # A classifier answers a level's rank and a regressor a number near one; rounding and clipping give both the
        # same reading, a rank from 1 to 6.
        ranks = clip(
            a=rint(
                predicted,
            ),
            a_min=1,
            a_max=len(
                LEVELS_ASCENDING,
            ),
        ).astype(
            dtype=int64,
        )

        return tuple(
            LEVELS_ASCENDING[
                int(
                    rank,
                )
                - 1
            ]
            for rank in ranks
        )


@final
class NoLevelEstimator:
    """
    The estimator in place when no trained model file exists.

    Satisfies `LevelEstimator` structurally, and always defers to the
    frequency heuristic.
    """

    def estimate_many(
        self,
        *,
        features: Sequence[LexicalFeatures],
    ) -> tuple[CefrLevel | None, ...]:
        return tuple(None for _ in features)


def fingerprint(
    *,
    features: Sequence[LexicalFeatures],
    levels: Sequence[CefrLevel],
) -> str:
    """
    A digest of the labeled words, independent of their order.

    Two datasets with the same lemmas, parts of speech, and levels share a
    fingerprint, so a saved model can say which data it was trained on.
    """

    rows = sorted(
        f"{item.lemma}\t{item.part_of_speech}\t{level}"
        for (
            item,
            level,
        ) in zip(
            features,
            levels,
            strict=True,
        )
    )

    return sha256(
        data="\n".join(
            rows,
        ).encode(),
    ).hexdigest()


def train(
    *,
    features: Sequence[LexicalFeatures],
    levels: Sequence[CefrLevel],
    families: Sequence[FeatureFamily],
    model_kind: ModelKind,
    seed: int,
) -> SklearnLevelEstimator:
    """
    Fit a model of the given kind, over the given families, to the levels of
    these words.

    The levels may be external labels or pseudo-labels; the model learns
    whichever it is given, and the caller is the one who knows which.
    """

    if len(
        features,
    ) != len(
        levels,
    ):
        raise TrainingError(
            f"training requires one level per word, got {
                len(
                    features,
                )
            } words and {
                len(
                    levels,
                )
            } levels",
        )

    if (
        len(
            frozenset(
                levels,
            ),
        )
        < 2
    ):
        raise TrainingError(
            "training requires words of at least two levels, got one",
        )

    pipeline = _pipeline(
        model_kind,
        seed,
    )

    pipeline.fit(
        X=encode(
            features=features,
            families=families,
        ),
        y=[level.rank for level in levels],
    )

    return SklearnLevelEstimator(
        pipeline=pipeline,
        metadata=ModelMetadata(
            model_kind=model_kind,
            families=tuple(
                families,
            ),
            feature_columns=columns_for(
                families=families,
            ),
            dataset_fingerprint=fingerprint(
                features=features,
                levels=levels,
            ),
            seed=seed,
            train_size=len(
                features,
            ),
            sklearn_version=installed_sklearn_version(),
        ),
    )


def installed_sklearn_version() -> str:
    """
    The version of scikit-learn this process runs, which a model records when
    it is trained.
    """

    return version(
        distribution_name="scikit-learn",
    )


def is_stale(
    *,
    metadata: ModelMetadata,
) -> bool:
    """
    Whether the model `metadata` describes was trained with another version of
    scikit-learn than the one installed, so that a new model is wanted.
    """

    return metadata.sklearn_version != installed_sklearn_version()


def _pipeline(
    model_kind: ModelKind,
    seed: int,
    /,
) -> Pipeline:
    match model_kind:
        case ModelKind.LOGISTIC_REGRESSION:
            # NOTE:
            # With the default lbfgs solver scikit-learn fits one multinomial model over the six levels rather than
            # six one-versus-rest models, and the installed version has no switch to say so.
            return Pipeline(
                steps=[
                    (
                        "scale",
                        StandardScaler(),
                    ),
                    (
                        "classify",
                        LogisticRegression(
                            random_state=seed,
                            max_iter=_LOGISTIC_MAX_ITERATIONS,
                        ),
                    ),
                ],
            )

        case ModelKind.GRADIENT_BOOSTING:
            return Pipeline(
                steps=[
                    (
                        "classify",
                        HistGradientBoostingClassifier(
                            random_state=seed,
                        ),
                    ),
                ],
            )

        case ModelKind.ORDINAL_GRADIENT_BOOSTING:
            return Pipeline(
                steps=[
                    (
                        "regress",
                        HistGradientBoostingRegressor(
                            random_state=seed,
                        ),
                    ),
                ],
            )


def group_folds(
    *,
    levels: Sequence[CefrLevel],
    groups: Sequence[str],
    folds: int,
    seed: int,
) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    """
    Divide the examples `folds` ways and return, for each fold, the indices of
    the other folds and of that fold.

    Every group lies in one fold, and each fold's levels come as near the
    whole's proportions as whole groups allow.

    The same levels, groups, fold count, and seed always produce the same
    folds.
    """

    if folds < 2:
        raise TrainingError(
            f"a division into folds requires at least two, got {folds}",
        )

    splitter = StratifiedGroupKFold(
        n_splits=folds,
        shuffle=True,
        random_state=seed,
    )

    ranks = tuple(level.rank for level in levels)

    return tuple(
        (
            tuple(
                others.tolist(),
            ),
            tuple(
                fold.tolist(),
            ),
        )
        for (
            others,
            fold,
        ) in splitter.split(
            X=[
                [
                    rank,
                ]
                for rank in ranks
            ],
            y=ranks,
            groups=groups,
        )
    )


def cross_validate(
    *,
    features: Sequence[LexicalFeatures],
    levels: Sequence[CefrLevel],
    groups: Sequence[str],
    families: Sequence[FeatureFamily],
    model_kind: ModelKind,
    folds: int,
    seed: int,
) -> tuple[EvaluationReport, ...]:
    """
    One report per validation fold, each from a model trained on the other
    folds, the folds made by `group_folds` so that no group is on both sides.
    """

    reports: list[EvaluationReport] = []

    for (
        train_indices,
        validation_indices,
    ) in group_folds(
        levels=levels,
        groups=groups,
        folds=folds,
        seed=seed,
    ):
        model = train(
            features=[features[index] for index in train_indices],
            levels=[levels[index] for index in train_indices],
            families=families,
            model_kind=model_kind,
            seed=seed,
        )

        reports.append(
            evaluate(
                expected=[levels[index] for index in validation_indices],
                predicted=model.estimate_many(
                    features=[features[index] for index in validation_indices],
                ),
            ),
        )

    return tuple(
        reports,
    )


def metadata_path(
    *,
    path: Path,
) -> Path:
    """
    Where the metadata sidecar of the model file at `path` lives.
    """

    return path.with_name(
        name=f"{path.name}.metadata.json",
    )


def save(
    *,
    estimator: SklearnLevelEstimator,
    path: Path,
) -> None:
    """
    Write the fitted pipeline to `path` and its metadata beside it.

    A save that raises, or whose process is stopped, part of the way never
    leaves a half-written model at `path`: a failure while writing leaves the
    earlier model and sidecar as they were, and one while putting them in
    place leaves at most a model without its sidecar, which `load` refuses.

    Nothing is flushed to disk before the files are renamed, so a crash of the
    machine itself can still leave either file cut short, which
    `read_metadata` and `load` refuse as well.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    sidecar = metadata_path(
        path=path,
    )

    partial_model = path.with_name(
        name=f"{path.name}.part",
    )

    partial_sidecar = sidecar.with_name(
        name=f"{sidecar.name}.part",
    )

    try:
        dump(
            value=estimator.pipeline,
            filename=partial_model,
        )

        partial_sidecar.write_text(
            data=estimator.metadata.model_dump_json(
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # NOTE:
        # Both files are whole before either takes its place, and the old sidecar goes first, so that no moment leaves
        # a new model beside the sidecar of an older one: an interruption from here on leaves a model without a
        # sidecar, which `load` refuses and `wordwinnow setup` trains again.
        sidecar.unlink(
            missing_ok=True,
        )

        partial_model.replace(
            target=path,
        )

        partial_sidecar.replace(
            target=sidecar,
        )

    finally:
        partial_model.unlink(
            missing_ok=True,
        )

        partial_sidecar.unlink(
            missing_ok=True,
        )


def read_metadata(
    *,
    path: Path,
) -> ModelMetadata | None:
    """
    Read the metadata sidecar of the model at `path` without opening the
    model, or `None` when there is no file there.

    Raises `ModelFileError` when the file has no sidecar, or the sidecar does
    not hold a model's metadata.
    """

    if not path.exists():
        return None

    sidecar = metadata_path(
        path=path,
    )

    if not sidecar.exists():
        raise ModelFileError(
            f"a saved model requires a metadata sidecar, and {sidecar} does not exist",
        )

    try:
        return ModelMetadata.model_validate_json(
            json_data=sidecar.read_bytes(),
        )

    except ValidationError as exception:
        raise ModelFileError(
            f"the metadata sidecar {sidecar} does not hold a model's metadata",
        ) from exception


def load(
    *,
    path: Path,
) -> SklearnLevelEstimator | None:
    """
    Read the model at `path`, or `None` when there is no file there.

    Raises `ModelFileError` when the file has no sidecar, its sidecar does not
    hold a model's metadata, it expects columns this code does not produce, it
    is not a whole pickle, or it does not hold a pipeline.
    """

    metadata = read_metadata(
        path=path,
    )

    if metadata is None:
        return None

    expected = columns_for(
        families=metadata.families,
    )

    if metadata.feature_columns != expected:
        raise ModelFileError(
            f"the model at {path} was trained on the feature columns {metadata.feature_columns}, not {expected}",
        )

    try:
        pipeline = load_joblib(
            filename=path,
        )

    except _NOT_A_WHOLE_PICKLE as exception:
        kind = type(
            exception,
        ).__name__

        raise ModelFileError(
            f"the model at {path} cannot be read ({kind})",
        ) from exception

    if not isinstance(
        pipeline,
        Pipeline,
    ):
        raise ModelFileError(
            f"a saved model requires a scikit-learn pipeline, got {
                type(
                    pipeline,
                ).__name__
            } in {path}",
        )

    return SklearnLevelEstimator(
        pipeline=pipeline,
        metadata=metadata,
    )
