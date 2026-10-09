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
)
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.linear_model import (
    LogisticRegression,
)
from sklearn.model_selection import (
    StratifiedKFold,
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
            sklearn_version=version(
                distribution_name="scikit-learn",
            ),
        ),
    )


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


def cross_validate(
    *,
    features: Sequence[LexicalFeatures],
    levels: Sequence[CefrLevel],
    families: Sequence[FeatureFamily],
    model_kind: ModelKind,
    folds: int,
    seed: int,
) -> tuple[EvaluationReport, ...]:
    """
    One report per stratified validation fold, each from a model trained on
    the other folds.
    """

    if folds < 2:
        raise TrainingError(
            f"cross-validation requires at least two folds, got {folds}",
        )

    splitter = StratifiedKFold(
        n_splits=folds,
        shuffle=True,
        random_state=seed,
    )

    ranks = tuple(level.rank for level in levels)

    reports: list[EvaluationReport] = []

    for (
        train_indices,
        validation_indices,
    ) in splitter.split(
        X=encode(
            features=features,
            families=families,
        ),
        y=ranks,
    ):
        model = train(
            features=[features[index] for index in train_indices.tolist()],
            levels=[levels[index] for index in train_indices.tolist()],
            families=families,
            model_kind=model_kind,
            seed=seed,
        )

        validation = validation_indices.tolist()

        reports.append(
            evaluate(
                expected=[levels[index] for index in validation],
                predicted=model.estimate_many(
                    features=[features[index] for index in validation],
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
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dump(
        value=estimator.pipeline,
        filename=path,
    )

    metadata_path(
        path=path,
    ).write_text(
        data=estimator.metadata.model_dump_json(
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load(
    *,
    path: Path,
) -> SklearnLevelEstimator | None:
    """
    Read the model at `path`, or `None` when there is no file there.

    Raises `ModelFileError` when the file has no sidecar, expects columns this
    code does not produce, or does not hold a pipeline.
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

    metadata = ModelMetadata.model_validate_json(
        json_data=sidecar.read_text(
            encoding="utf-8",
        ),
    )

    expected = columns_for(
        families=metadata.families,
    )

    if metadata.feature_columns != expected:
        raise ModelFileError(
            f"the model at {path} was trained on the feature columns {metadata.feature_columns}, not {expected}",
        )

    pipeline = load_joblib(
        filename=path,
    )

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
