"""
The experiment that measures every way the project assigns a level, and keeps
apart the three questions it answers.

Validity is agreement with the reference lists' external labels: it is
measured for the frequency heuristic as shipped, for the heuristic with its
boundaries fitted on the training split, for a majority-class baseline, and
for every candidate model.

A candidate is one estimator kind over one set of feature families, and the
families are added one at a time so the experiment says what each adds; the
candidate with the lowest cross-validated level error on the training split is
the one the project keeps.

Fidelity is agreement with the heuristic's own pseudo-labels, measured for the
chosen candidate trained to reproduce them, and it says nothing about CEFR
levels: it is reported to show that a model can agree closely with its teacher
while both are wrong about the word.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import (
    asdict,
    dataclass,
)
from json import (
    dumps,
)
from pathlib import (
    Path,
)
from statistics import (
    fmean,
    mode,
    pstdev,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)

from wordwinnow.domain.cefr import (
    CefrLevel,
    FrequencyThresholds,
)
from wordwinnow.domain.difficulty import (
    FeatureFamily,
)
from wordwinnow.infrastructure.ml.evaluation import (
    EvaluationReport,
    evaluate,
    tune_thresholds,
)
from wordwinnow.infrastructure.ml.model import (
    ModelKind,
    SklearnLevelEstimator,
    cross_validate,
    train,
)
from wordwinnow.services.ml.dataset import (
    LabeledExample,
    class_distribution,
    pseudo_label,
    relabel_with_pseudo_labels,
    split,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    WorkProgress,
)

# NOTE:
# The protocol every run follows: one fifth of the examples held out, five stratified folds over the rest.
TEST_FRACTION: Final = 0.2

FOLDS: Final = 5

RESULTS_FILE: Final = "results.json"

# NOTE:
# The families are added in this order, so each set is the previous one plus one family.
FAMILY_SETS: Final = (
    (FeatureFamily.FREQUENCY,),
    (
        FeatureFamily.FREQUENCY,
        FeatureFamily.LEXICAL,
    ),
    (
        FeatureFamily.FREQUENCY,
        FeatureFamily.LEXICAL,
        FeatureFamily.WORDNET,
    ),
)

# PERF:
# The configuration the product ships: the candidate with the lowest cross-validated level error in the executed
# notebook, 0.753, and 0.732 on the held-out words, against 0.811 for the tuned heuristic, which is fitted rather than
# cross-validated; `train_shipped_model` fits it to every example.
SHIPPED_FAMILIES: Final = (
    FeatureFamily.FREQUENCY,
    FeatureFamily.LEXICAL,
    FeatureFamily.WORDNET,
)

SHIPPED_MODEL_KIND: Final = ModelKind.ORDINAL_GRADIENT_BOOSTING

# NOTE:
# The method names the comparison figure and the summary use, in the order the bars appear.
MAJORITY_METHOD: Final = "majority class"

DEFAULT_HEURISTIC_METHOD: Final = "default heuristic"

TUNED_HEURISTIC_METHOD: Final = "tuned heuristic"

MODEL_METHOD: Final = "chosen model"


def families_label(
    *,
    families: Sequence[FeatureFamily],
) -> str:
    """
    The short name of a family set, such as `frequency+lexical`.
    """

    return "+".join(
        families,
    )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Candidate:
    """
    One estimator kind over one family set, with its cross-validation reports
    on the training split and its validity on the test split.
    """

    families: tuple[FeatureFamily, ...]

    model_kind: ModelKind

    cross_validation: tuple[EvaluationReport, ...]

    validity: EvaluationReport

    @property
    def name(
        self,
    ) -> str:
        """
        The candidate as the summary names it.
        """

        return f"{self.model_kind} on {
            families_label(
                families=self.families,
            )
        }"

    @property
    def cross_validated_error(
        self,
    ) -> float:
        """
        The mean absolute level error over the folds, which is what chooses
        the candidate.
        """

        return fmean(
            data=[report.mean_absolute_level_error for report in self.cross_validation],
        )


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class ExperimentResult:
    """
    Everything one run measured, in the order the experiment measures it.

    Every report but `fidelity` is scored against the external labels of the
    test split; `fidelity` is scored against pseudo-labels and is not
    comparable with the others.
    """

    train_size: int

    test_size: int

    train_distribution: MappingProxyType[CefrLevel, int]

    test_distribution: MappingProxyType[CefrLevel, int]

    majority_level: CefrLevel

    majority_baseline: EvaluationReport

    heuristic_default: EvaluationReport

    tuned_thresholds: FrequencyThresholds

    heuristic_tuned: EvaluationReport

    candidates: tuple[Candidate, ...]

    chosen: Candidate

    fidelity: EvaluationReport

    per_source_accuracy: MappingProxyType[str, float]

    estimator: SklearnLevelEstimator


def train_shipped_model(
    *,
    examples: Sequence[LabeledExample],
    seed: int,
) -> SklearnLevelEstimator:
    """
    Fit the shipped configuration to every example, for the runtime.

    The model carries no held-out scores, because none of the examples was
    held out; the experiment is where the configuration earned its place.
    """

    return train(
        features=[example.features for example in examples],
        levels=[example.level for example in examples],
        families=SHIPPED_FAMILIES,
        model_kind=SHIPPED_MODEL_KIND,
        seed=seed,
    )


def run_experiment(
    *,
    examples: Sequence[LabeledExample],
    seed: int,
    output_dir: Path | None,
    progress: WorkProgress = UNWATCHED,
) -> ExperimentResult:
    """
    Run the whole protocol once and, when `output_dir` is given, write the
    numbers and the figures there.

    `progress` hears each candidate as it is cross-validated, the longest part
    of a run.
    """

    (
        training,
        test,
    ) = split(
        examples=examples,
        test_fraction=TEST_FRACTION,
        seed=seed,
    )

    expected = tuple(example.level for example in test)

    majority_level = mode(
        data=[example.level for example in training],
    )

    majority_baseline = evaluate(
        expected=expected,
        predicted=[majority_level for _ in test],
    )

    heuristic_default = evaluate(
        expected=expected,
        predicted=[
            pseudo_label(
                example=example,
            )
            for example in test
        ],
    )

    tuned_thresholds = tune_thresholds(
        zipf_frequencies=[example.features.zipf_frequency for example in training],
        levels=[example.level for example in training],
    )

    heuristic_tuned = evaluate(
        expected=expected,
        predicted=[
            pseudo_label(
                example=example,
                thresholds=tuned_thresholds,
            )
            for example in test
        ],
    )

    plan = tuple(
        (
            families,
            model_kind,
        )
        for families in FAMILY_SETS
        for model_kind in ModelKind
    )

    progress.begin(
        description="cross-validating the candidate models",
        steps=len(
            plan,
        ),
    )

    fitted: list[Candidate] = []

    for (
        families,
        model_kind,
    ) in plan:
        progress.describe(
            description=f"cross-validating {model_kind} on {
                families_label(
                    families=families,
                )
            }",
        )

        fitted.append(
            _candidate(
                training,
                test,
                families,
                model_kind,
                seed,
            ),
        )

        progress.advance()

    candidates = tuple(
        fitted,
    )

    chosen = min(
        candidates,
        key=lambda candidate: candidate.cross_validated_error,
    )

    progress.begin(
        description=f"training {chosen.name} and measuring it on the held-out words",
        steps=None,
    )

    estimator = train(
        features=[example.features for example in training],
        levels=[example.level for example in training],
        families=chosen.families,
        model_kind=chosen.model_kind,
        seed=seed,
    )

    predicted = estimator.estimate_many(
        features=[example.features for example in test],
    )

    result = ExperimentResult(
        train_size=len(
            training,
        ),
        test_size=len(
            test,
        ),
        train_distribution=class_distribution(
            examples=training,
        ),
        test_distribution=class_distribution(
            examples=test,
        ),
        majority_level=majority_level,
        majority_baseline=majority_baseline,
        heuristic_default=heuristic_default,
        tuned_thresholds=tuned_thresholds,
        heuristic_tuned=heuristic_tuned,
        candidates=candidates,
        chosen=chosen,
        fidelity=_fidelity(
            training,
            test,
            chosen,
            seed,
        ),
        per_source_accuracy=_per_source_accuracy(
            test,
            predicted,
        ),
        estimator=estimator.with_metrics(
            metrics=_metrics(
                chosen.validity,
            ),
        ),
    )

    if output_dir is not None:
        progress.begin(
            description=f"writing {RESULTS_FILE} and the figures",
            steps=None,
        )

        _write_outputs(
            result,
            output_dir,
        )

    return result


def _candidate(
    training: Sequence[LabeledExample],
    test: Sequence[LabeledExample],
    families: Sequence[FeatureFamily],
    model_kind: ModelKind,
    seed: int,
    /,
) -> Candidate:
    features = tuple(example.features for example in training)

    levels = tuple(example.level for example in training)

    reports = cross_validate(
        features=features,
        levels=levels,
        families=families,
        model_kind=model_kind,
        folds=FOLDS,
        seed=seed,
    )

    model = train(
        features=features,
        levels=levels,
        families=families,
        model_kind=model_kind,
        seed=seed,
    )

    return Candidate(
        families=tuple(
            families,
        ),
        model_kind=model_kind,
        cross_validation=reports,
        validity=evaluate(
            expected=[example.level for example in test],
            predicted=model.estimate_many(
                features=[example.features for example in test],
            ),
        ),
    )


def _fidelity(
    training: Sequence[LabeledExample],
    test: Sequence[LabeledExample],
    chosen: Candidate,
    seed: int,
    /,
) -> EvaluationReport:
    pseudo_training = relabel_with_pseudo_labels(
        examples=training,
    )

    pseudo_test = relabel_with_pseudo_labels(
        examples=test,
    )

    model = train(
        features=[example.features for example in pseudo_training],
        levels=[example.level for example in pseudo_training],
        families=chosen.families,
        model_kind=chosen.model_kind,
        seed=seed,
    )

    return evaluate(
        expected=[example.level for example in pseudo_test],
        predicted=model.estimate_many(
            features=[example.features for example in pseudo_test],
        ),
    )


def _per_source_accuracy(
    test: Sequence[LabeledExample],
    predicted: Sequence[CefrLevel],
    /,
) -> MappingProxyType[str, float]:
    hits: dict[str, int] = {}

    totals: dict[str, int] = {}

    for (
        example,
        level,
    ) in zip(
        test,
        predicted,
        strict=True,
    ):
        totals[example.source] = (
            totals.get(
                example.source,
                0,
            )
            + 1
        )

        hits[example.source] = hits.get(
            example.source,
            0,
        ) + (1 if example.level is level else 0)

    return MappingProxyType(
        mapping={
            source: hits[source] / totals[source]
            for source in sorted(
                totals,
            )
        },
    )


def _metrics(
    report: EvaluationReport,
    /,
) -> dict[str, float]:
    return {
        "accuracy": report.accuracy,
        "macro_f1": report.macro_f1,
        "within_one_level_accuracy": report.within_one_level_accuracy,
        "mean_absolute_level_error": report.mean_absolute_level_error,
        "spearman": report.spearman,
    }


def _write_outputs(
    result: ExperimentResult,
    output_dir: Path,
    /,
) -> None:
    # NOTE:
    # The figures need matplotlib, which is an experiment tool from the development group rather than a runtime
    # dependency, so the module that draws them is imported only on the path that writes them.
    from wordwinnow.services.ml.figures import (
        write_figures,
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    (output_dir / RESULTS_FILE).write_text(
        data=dumps(
            obj=record_of(
                result=result,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    write_figures(
        result=result,
        output_dir=output_dir,
    )


def _distribution_record(
    distribution: Mapping[CefrLevel, int],
    /,
) -> dict[CefrLevel, int]:
    return dict(
        distribution,
    )


def _report_record(
    report: EvaluationReport,
    /,
) -> dict[str, object]:
    record = asdict(
        obj=report,
    )

    record["labels"] = list(
        report.labels,
    )

    record["per_class"] = [
        {
            **asdict(
                obj=entry,
            ),
            "level": entry.level,
        }
        for entry in report.per_class
    ]

    return record


def record_of(
    *,
    result: ExperimentResult,
) -> dict[str, object]:
    """
    The run's numbers as plain data, which is what `results.json` holds.
    """

    return {
        "train_size": result.train_size,
        "test_size": result.test_size,
        "train_distribution": _distribution_record(
            result.train_distribution,
        ),
        "test_distribution": _distribution_record(
            result.test_distribution,
        ),
        "majority_level": result.majority_level,
        "majority_baseline": _report_record(
            result.majority_baseline,
        ),
        "heuristic_default": _report_record(
            result.heuristic_default,
        ),
        "tuned_thresholds": list(
            result.tuned_thresholds.boundaries,
        ),
        "heuristic_tuned": _report_record(
            result.heuristic_tuned,
        ),
        "candidates": [
            {
                "families": list(
                    candidate.families,
                ),
                "model_kind": candidate.model_kind,
                "cross_validated_error": candidate.cross_validated_error,
                "cross_validation": [
                    _report_record(
                        report,
                    )
                    for report in candidate.cross_validation
                ],
                "validity": _report_record(
                    candidate.validity,
                ),
            }
            for candidate in result.candidates
        ],
        "chosen": result.chosen.name,
        "fidelity": _report_record(
            result.fidelity,
        ),
        "per_source_accuracy": dict(
            result.per_source_accuracy,
        ),
        "model_metadata": result.estimator.metadata.model_dump(
            mode="json",
        ),
    }


def render_summary(
    *,
    result: ExperimentResult,
) -> str:
    """
    The run's numbers as lines of text, for a terminal.
    """

    lines = [
        f"Examples: {result.train_size} for training, {result.test_size} held out.",
        "",
        "Validity against the external labels on the test split:",
        _score_line(
            MAJORITY_METHOD,
            result.majority_baseline,
        ),
        _score_line(
            DEFAULT_HEURISTIC_METHOD,
            result.heuristic_default,
        ),
        _score_line(
            TUNED_HEURISTIC_METHOD,
            result.heuristic_tuned,
        ),
        _score_line(
            f"{MODEL_METHOD}, {result.chosen.name}",
            result.chosen.validity,
        ),
        "",
        f"Tuned Zipf boundaries: {result.tuned_thresholds.boundaries}",
        f"Majority level: {result.majority_level}",
        "",
        "Candidates, by cross-validated mean absolute level error on the training split (mean +/- sd over the "
        "folds):",
    ]

    for candidate in sorted(
        result.candidates,
        key=lambda candidate: candidate.cross_validated_error,
    ):
        # NOTE:
        # A format specifier cannot span lines, so the statistics are bound before the f-string.
        spread = pstdev(
            data=[report.mean_absolute_level_error for report in candidate.cross_validation],
        )

        mark = " (chosen)" if candidate is result.chosen else ""

        lines.append(
            f"  {candidate.name}: {candidate.cross_validated_error:.3f} +/- {spread:.3f}{mark}",
        )

    lines.extend(
        (
            "",
            "Accuracy of the chosen model by source list:",
        ),
    )

    for (
        source,
        accuracy,
    ) in result.per_source_accuracy.items():
        lines.append(
            f"  {source}: {accuracy:.3f}",
        )

    lines.extend(
        (
            "",
            "Fidelity to the heuristic's pseudo-labels, which says nothing about CEFR levels:",
            _score_line(
                f"{result.chosen.name} trained on pseudo-labels",
                result.fidelity,
            ),
        ),
    )

    return "\n".join(
        lines,
    )


def _score_line(
    name: str,
    report: EvaluationReport,
    /,
) -> str:
    return (
        f"  {name}: mean absolute level error {report.mean_absolute_level_error:.3f}, within one level "
        f"{report.within_one_level_accuracy:.3f}, macro-F1 {report.macro_f1:.3f}, accuracy {report.accuracy:.3f}, "
        f"Spearman {report.spearman:.3f}"
    )
