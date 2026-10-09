"""
One run of the experiment on synthetic examples: every section present, every
candidate measured, every output file written.
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
    TempPathFactory,
    fixture,
)

from tests.fakes.progress import (
    RecordingWorkProgress,
)
from tests.services.ml.synthetic import (
    LOWER_SOURCE,
    UPPER_SOURCE,
    synthetic_examples,
)
from wordwinnow.infrastructure.ml.model import (
    ModelKind,
)
from wordwinnow.services.ml.experiment import (
    FAMILY_SETS,
    FOLDS,
    RESULTS_FILE,
    ExperimentResult,
    families_label,
    render_summary,
    run_experiment,
)
from wordwinnow.services.ml.figures import (
    ABLATION_FIGURE,
    COMPARISON_FIGURE,
    CONFUSION_FIGURE,
)

_COUNT: Final = 300

_SEED: Final = 42

_SECTIONS: Final = frozenset(
    {
        "train_size",
        "test_size",
        "train_distribution",
        "test_distribution",
        "majority_level",
        "majority_baseline",
        "heuristic_default",
        "tuned_thresholds",
        "heuristic_tuned",
        "candidates",
        "chosen",
        "fidelity",
        "per_source_accuracy",
        "model_metadata",
    },
)


@fixture(
    scope="module",
)
def output_dir(
    *,
    tmp_path_factory: TempPathFactory,
) -> Path:
    return tmp_path_factory.mktemp(
        basename="experiment",
    )


@fixture(
    scope="module",
)
def progress() -> RecordingWorkProgress:
    return RecordingWorkProgress()


@fixture(
    scope="module",
)
def result(
    *,
    output_dir: Path,
    progress: RecordingWorkProgress,
) -> ExperimentResult:
    return run_experiment(
        examples=synthetic_examples(
            count=_COUNT,
            seed=0,
        ),
        seed=_SEED,
        output_dir=output_dir,
        progress=progress,
    )


@final
class TestRunExperiment:
    def test_every_section_is_measured_on_the_right_split(
        self,
        *,
        result: ExperimentResult,
    ) -> None:
        assert result.train_size + result.test_size == _COUNT

        assert (
            sum(
                result.train_distribution.values(),
            )
            == result.train_size
        )

        assert (
            sum(
                result.test_distribution.values(),
            )
            == result.test_size
        )

        assert result.heuristic_default.n == result.test_size

        assert result.heuristic_tuned.n == result.test_size

        assert result.majority_baseline.n == result.test_size

        assert result.fidelity.n == result.test_size

        assert {
            (
                candidate.families,
                candidate.model_kind,
            )
            for candidate in result.candidates
        } == {
            (
                families,
                kind,
            )
            for families in FAMILY_SETS
            for kind in ModelKind
        }

        assert all(
            len(
                candidate.cross_validation,
            )
            == FOLDS
            and candidate.validity.n == result.test_size
            for candidate in result.candidates
        )

        assert result.chosen.cross_validated_error == min(
            candidate.cross_validated_error for candidate in result.candidates
        )

        assert frozenset(
            result.per_source_accuracy,
        ) == {
            LOWER_SOURCE,
            UPPER_SOURCE,
        }

        assert result.estimator.metadata.model_kind is result.chosen.model_kind

        assert result.estimator.metadata.families == result.chosen.families

        assert (
            result.estimator.metadata.metrics["mean_absolute_level_error"]
            == result.chosen.validity.mean_absolute_level_error
        )

    def test_the_structure_is_learned(
        self,
        *,
        result: ExperimentResult,
    ) -> None:
        assert result.chosen.validity.accuracy > result.majority_baseline.accuracy

        assert result.chosen.validity.within_one_level_accuracy > 0.9

    def test_the_outputs_are_written(
        self,
        *,
        result: ExperimentResult,
        output_dir: Path,
    ) -> None:
        record = loads(
            s=(output_dir / RESULTS_FILE).read_text(
                encoding="utf-8",
            ),
        )

        assert (
            frozenset(
                record,
            )
            == _SECTIONS
        )

        assert record["chosen"] == result.chosen.name

        assert record["candidates"][0]["validity"]["confusion"] == [
            list(
                row,
            )
            for row in result.candidates[0].validity.confusion
        ]

        assert record["model_metadata"]["train_size"] == result.train_size

        for name in (
            COMPARISON_FIGURE,
            CONFUSION_FIGURE,
            ABLATION_FIGURE,
        ):
            assert (output_dir / name).stat().st_size > 0

    def test_the_summary_names_every_method_and_keeps_fidelity_apart(
        self,
        *,
        result: ExperimentResult,
    ) -> None:
        summary = render_summary(
            result=result,
        )

        assert "majority class" in summary

        assert "default heuristic" in summary

        assert "tuned heuristic" in summary

        assert f"chosen model, {result.chosen.name}" in summary

        assert "(chosen)" in summary

        assert "says nothing about CEFR levels" in summary

    def test_progress_counts_the_candidates_and_names_each_one_before_it_runs(
        self,
        *,
        result: ExperimentResult,
        progress: RecordingWorkProgress,
    ) -> None:
        candidates = tuple(
            f"cross-validating {model_kind} on {
                families_label(
                    families=families,
                )
            }"
            for families in FAMILY_SETS
            for model_kind in ModelKind
        )

        assert progress.events == [
            f"cross-validating the candidate models of {
                len(
                    candidates,
                )
            }",
            *(
                event
                for candidate in candidates
                for event in (
                    candidate,
                    "step",
                )
            ),
            f"training {result.chosen.name} and measuring it on the held-out words",
            f"writing {RESULTS_FILE} and the figures",
        ]
