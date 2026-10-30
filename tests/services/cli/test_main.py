"""
The command line itself, through Typer's runner.

These tests need the NLTK data `make nltk-data` installs.
"""

from collections.abc import (
    Callable,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from io import (
    StringIO,
)
from json import (
    loads,
)
from pathlib import (
    Path,
)
from re import (
    match,
    search,
)
from typing import (
    Final,
    final,
)

from platformdirs import (
    user_data_path,
)
from pytest import (
    MonkeyPatch,
    fixture,
    mark,
)
from rich.console import (
    Console,
)
from rich.text import (
    Text,
)
from typer import (
    rich_utils,
)
from typer.testing import (
    CliRunner,
)

from wordwinnow.application.dto import (
    ProcessingProgress,
)
from wordwinnow.application.errors import (
    LinguisticResourcesMissingError,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
    AnalysisStatus,
    Stage,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.infrastructure.composition import (
    API_SOURCES,
    FEED_SOURCE,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    REFERENCE_FILES,
)
from wordwinnow.infrastructure.lexicon.reference_sources import (
    REFERENCE_CITATIONS,
)
from wordwinnow.infrastructure.ml.model import (
    SklearnLevelEstimator,
    installed_sklearn_version,
    load,
    read_metadata,
    save,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.cli import (
    commands,
    main,
)
from wordwinnow.services.cli.intake_client import (
    WatchedAnalysis,
)
from wordwinnow.services.cli.main import (
    SOURCE_TOPICS,
    app,
)
from wordwinnow.services.intake.schemas import (
    AnalysisRequest,
)
from wordwinnow.services.progress import (
    UNWATCHED,
    AnalysisProgress,
    DictionaryWatch,
)

_TEXT: Final = (
    "The King wanted the photograph back. The groom looked very drunken, but Irene Adler kept the photograph."
)

_runner: Final = CliRunner()

_EPOCH: Final = datetime(
    year=1970,
    month=1,
    day=1,
    tzinfo=UTC,
)


# NOTE:
# Typer styles its output as for a terminal wherever `GITHUB_ACTIONS` is set, and its highlighter splits an option's
# name such as `--wait` into styled runs, so an assertion that reads a usage error as plain text passes locally and
# fails only in CI.
#
# Every test here styles Typer's output as CI does, in the standard colors whatever the local `TERM` says, so such an
# assertion fails locally too, and the assertions read the plain text under the styling.
@fixture(
    autouse=True,
)
def styled_as_in_ci(
    *,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        target=rich_utils,
        name="FORCE_TERMINAL",
        value=True,
    )

    monkeypatch.setattr(
        target=rich_utils,
        name="COLOR_SYSTEM",
        value="standard",
    )


@fixture
def workspace(
    *,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Path:
    monkeypatch.chdir(
        path=tmp_path,
    )

    monkeypatch.setenv(
        name="WORDWINNOW_DATABASE_URL",
        value=f"sqlite+aiosqlite:///{tmp_path / 'wordwinnow.db'}",
    )

    monkeypatch.setenv(
        name="WORDWINNOW_REFERENCE_LISTS_DIR",
        value=str(
            object=Path(
                __file__,
            ).parents[3]
            / "data/reference",
        ),
    )

    monkeypatch.setenv(
        name="WORDWINNOW_LEVEL_MODEL_PATH",
        value=str(
            object=tmp_path / "no-such-model.joblib",
        ),
    )

    return tmp_path


@final
class TestCli:
    def test_the_local_flow_from_the_command_line(
        self,
        *,
        workspace: Path,
    ) -> None:
        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        analyzed = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "A2",
                "--target",
                "C1",
                "--json",
            ],
        )

        assert analyzed.exit_code == 0, analyzed.output

        # NOTE:
        # JSON is read from standard output alone, the stream another program reads; log lines go to standard error.
        payload = loads(
            s=analyzed.stdout,
        )

        assert payload["status"] == "completed"

        assert payload["profile"]["target_level"] == "C1"

        assert "drunken" in (entry["lemma"] for entry in payload["items"])

        analysis_id = payload["analysis_id"]

        listed = _runner.invoke(
            app=app,
            args=[
                "list",
                "--local",
            ],
        )

        assert listed.exit_code == 0, listed.output

        assert "a-scandal-in-bohemia" in listed.output

        shown = _runner.invoke(
            app=app,
            args=[
                "show",
                analysis_id,
                "--local",
                "--rows",
                "5",
            ],
        )

        assert shown.exit_code == 0, shown.output

        assert "drunken" in shown.output

        assert "completed" in shown.output

        cards = _runner.invoke(
            app=app,
            args=[
                "show",
                analysis_id,
                "--local",
                "--detail",
                "--rows",
                "3",
            ],
        )

        assert cards.exit_code == 0, cards.output

        assert "in the text:" in cards.output

        exported = _runner.invoke(
            app=app,
            args=[
                "show",
                analysis_id,
                "--local",
                "--csv",
                "out.csv",
            ],
        )

        assert exported.exit_code == 0, exported.output

        assert (
            (workspace / "out.csv")
            .read_text(
                encoding="utf-8",
            )
            .startswith(
                "tier,lemma,",
            )
        )

    def test_a_missing_file_and_a_bad_id_fail_with_one_line(
        self,
        *,
        workspace: Path,
    ) -> None:
        missing = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--level",
                "B1",
                "no-such-file.txt",
                "--local",
                "--no-dictionary",
            ],
        )

        assert missing.exit_code == 1

        assert "no-such-file.txt does not exist" in missing.output

        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        no_known_words = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--level",
                "B1",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--known",
                "no-such-file.txt",
            ],
        )

        assert no_known_words.exit_code == 2

        assert "does not exist" in no_known_words.output

        backwards = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--level",
                "B2",
                "--target",
                "A1",
            ],
        )

        assert backwards.exit_code == 2

        assert "target level" in backwards.output

        assert "validation error" not in backwards.output

        assert "pydantic" not in backwards.output

        bad = _runner.invoke(
            app=app,
            args=[
                "show",
                "not-an-id",
                "--local",
            ],
        )

        assert bad.exit_code == 1

        assert "UUID" in bad.output

    def test_a_file_and_a_source_at_once_are_refused(
        self,
        *,
        workspace: Path,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--level",
                "B1",
                "a-scandal-in-bohemia.txt",
                "--source",
                "nyt-top-stories",
                "--topic",
                "science",
                "--local",
            ],
        )

        assert result.exit_code == 2

        assert "exactly one" in result.output

        half = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--level",
                "B1",
                "--source",
                "nyt-rss",
                "--local",
            ],
        )

        assert half.exit_code == 2

        assert "together" in half.output

    def test_an_analysis_without_a_level_is_refused(
        self,
        *,
        workspace: Path,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
            ],
        )

        assert result.exit_code == 2

        assert (
            "--level"
            in Text.from_ansi(
                text=result.output,
            ).plain
        )

    def test_a_report_says_how_many_words_it_left_out(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # NOTE:
        # A wide console keeps the note on one line, so the assertions read the whole of it.
        monkeypatch.setattr(
            target=main,
            name="_console",
            value=Console(
                width=200,
            ),
        )

        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        table = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
                "--rows",
                "1",
            ],
        )

        assert table.exit_code == 0, table.output

        assert "showing 1 of" in table.output

        assert "--rows raises the limit" in table.output

        truncated = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
                "--rows",
                "1",
                "--csv",
                "out.csv",
            ],
        )

        assert truncated.exit_code == 0, truncated.output

        assert "wrote 1 of" in truncated.output

        whole = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
                "--rows",
                "1000",
                "--csv",
                "out.csv",
            ],
        )

        assert whole.exit_code == 0, whole.output

        assert (
            " of "
            not in whole.output.split(
                sep="out.csv",
            )[0]
        )

    def test_a_text_with_nothing_to_study_says_so(
        self,
        *,
        workspace: Path,
    ) -> None:
        (workspace / "function-words.txt").write_text(
            data="The of and to it is.",
            encoding="utf-8",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "function-words.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
            ],
        )

        assert result.exit_code == 0, result.output

        assert "no words to study" in result.output

    def test_standard_input_is_analyzed_and_refused_without_text(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # NOTE:
        # Text another tool extracts, as `pdftotext` does from a PDF, reaches the command on standard input, which the
        # module binds when it is imported, so the test replaces that binding; a page that holds no text, as a scanned
        # one, reaches it as a form feed alone.
        monkeypatch.setattr(
            target=main,
            name="stdin",
            value=StringIO(
                initial_value=_TEXT,
            ),
        )

        piped = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "-",
                "--local",
                "--no-dictionary",
                "--level",
                "A2",
                "--json",
            ],
        )

        assert piped.exit_code == 0, piped.output

        payload = loads(
            s=piped.stdout,
        )

        assert payload["document"]["title"] == "standard input"

        assert "drunken" in (entry["lemma"] for entry in payload["items"])

        monkeypatch.setattr(
            target=main,
            name="stdin",
            value=StringIO(
                initial_value="\f\n",
            ),
        )

        blank = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "-",
                "--local",
                "--no-dictionary",
                "--level",
                "A2",
            ],
        )

        assert blank.exit_code == 1

        assert "a document requires non-blank text" in blank.output

    def test_a_failed_analysis_fails_the_command(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        async def failed(
            *,
            request: AnalysisRequest,
            settings: Settings,
            progress: AnalysisProgress = UNWATCHED,
            watch: DictionaryWatch | None = None,
        ) -> Analysis:
            analysis = request_an_analysis(
                document=Document(
                    title="a-scandal-in-bohemia",
                    text=_TEXT,
                    origin=DocumentOrigin.CUSTOM_TEXT,
                    reference="a-scandal-in-bohemia.txt",
                ),
                profile=LearnerProfile(
                    level=CefrLevel.B1,
                    target_level=CefrLevel.B2,
                ),
                options=AnalysisOptions(),
                at=_EPOCH,
            )

            analysis.start(
                at=_EPOCH,
            )

            analysis.fail(
                at=_EPOCH,
                reason="RuntimeError: the tagger was not there",
            )

            return analysis

        monkeypatch.setattr(
            target=commands,
            name="analyze_locally",
            value=failed,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--level",
                "B1",
            ],
        )

        assert result.exit_code == 1

        assert "the tagger was not there" in result.output

    def test_doctor_reports_an_unusable_model_and_exits_one(
        self,
        *,
        workspace: Path,
    ) -> None:
        (workspace / "no-such-model.joblib").write_bytes(
            data=b"not a model",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
            ],
        )

        assert result.exit_code == 1

        assert "BROKEN" in result.output

        assert "Traceback" not in result.output

    def test_a_missing_reference_list_names_the_setting_in_one_line(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_REFERENCE_LISTS_DIR",
            value=str(
                object=workspace / "no-lists",
            ),
        )

        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
            ],
        )

        assert result.exit_code == 1

        assert "WORDWINNOW_REFERENCE_LISTS_DIR" in result.output

        assert "Traceback" not in result.output

    def test_a_run_that_cannot_find_its_lists_leaves_no_store_behind(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_REFERENCE_LISTS_DIR",
            value=str(
                object=workspace / "no-lists",
            ),
        )

        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
            ],
        )

        assert result.exit_code == 1

        assert not (workspace / "wordwinnow.db").exists()

    def test_an_unconfigured_source_fails_with_one_line(
        self,
        *,
        workspace: Path,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--level",
                "B1",
                "--source",
                "nyt-archive",
                "--topic",
                "2026-08",
                "--local",
                "--no-dictionary",
            ],
        )

        assert result.exit_code == 1

        assert "no source named 'nyt-archive'" in result.output

    def test_doctor_reports_and_exits_zero_when_healthy(
        self,
        *,
        workspace: Path,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
            ],
        )

        assert result.exit_code == 0, result.output

        assert "WordNet 3.0" in result.output

        assert "level model" in result.output

    def test_doctor_reports_a_model_from_another_scikit_learn_and_exits_one(
        self,
        *,
        workspace: Path,
        stale_model: SklearnLevelEstimator,
    ) -> None:
        save(
            estimator=stale_model,
            path=Settings().level_model_path,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
            ],
        )

        assert result.exit_code == 1

        assert "STALE" in result.output

        assert "Traceback" not in result.output

    def test_doctor_reports_a_model_from_another_scikit_learn_that_this_one_cannot_read_and_exits_one(
        self,
        *,
        workspace: Path,
        put_model: Callable[..., None],
    ) -> None:
        put_model(
            path=Settings().level_model_path,
            state="another scikit-learn and unreadable",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
            ],
        )

        assert result.exit_code == 1

        assert "STALE" in result.output

        assert "Traceback" not in result.output

    @mark.parametrize(
        argnames="state",
        argvalues=(
            "a sidecar that is not JSON",
            "cut short",
        ),
    )
    def test_doctor_reports_a_damaged_model_file_and_exits_one(
        self,
        *,
        workspace: Path,
        put_model: Callable[..., None],
        state: str,
    ) -> None:
        put_model(
            path=Settings().level_model_path,
            state=state,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
            ],
        )

        assert result.exit_code == 1

        assert "BROKEN" in result.output

        assert "Traceback" not in result.output

    @mark.parametrize(
        argnames="view",
        argvalues=(
            "learner",
            "operator",
        ),
    )
    def test_off_a_terminal_a_run_writes_its_result_and_no_progress(
        self,
        *,
        workspace: Path,
        view: str,
    ) -> None:
        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--no-dictionary",
                "--level",
                "B1",
                "--json",
                "--view",
                view,
            ],
        )

        assert result.exit_code == 0, result.output

        assert (
            loads(
                s=result.stdout,
            )["status"]
            == "completed"
        )

        # NOTE:
        # Standard error holds log records and nothing else, not even the blank line a progress display leaves behind
        # on a stream that is not a terminal.
        assert all(
            match(
                pattern=r"\d\d:\d\d:\d\d\.\d{3} (?:DEBUG|INFO|WARNING|ERROR) ",
                string=line,
            )
            for line in result.stderr.splitlines()
        )

    def test_the_help_of_analyze_says_what_every_source_takes(
        self,
    ) -> None:
        assert tuple(
            SOURCE_TOPICS,
        ) == (
            FEED_SOURCE,
            *API_SOURCES,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--help",
            ],
        )

        assert result.exit_code == 0, result.output

        for name in SOURCE_TOPICS:
            assert name in result.stdout

        assert "WORDWINNOW_NYT_API_KEY" in result.stdout


def _processing() -> Analysis:
    analysis = request_an_analysis(
        document=Document(
            title="a-scandal-in-bohemia",
            text=_TEXT,
            origin=DocumentOrigin.CUSTOM_TEXT,
            reference="a-scandal-in-bohemia.txt",
        ),
        profile=LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
        ),
        options=AnalysisOptions(),
        at=_EPOCH,
    )

    analysis.start(
        at=_EPOCH,
    )

    return analysis


def _reported(
    reported_at: datetime,
    /,
) -> ProcessingProgress:
    return ProcessingProgress(
        stage=Stage.DICTIONARY_ENRICHMENT,
        steps=2,
        done=1,
        unavailable=0,
        dictionary_paused=False,
        reported_at=reported_at,
    )


@final
class TestShowingAnAnalysisInProgress:
    def test_it_says_where_the_analysis_stands_and_how_to_wait_for_it(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        analysis = _processing()

        async def watched(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            local: bool,
        ) -> WatchedAnalysis:
            return WatchedAnalysis(
                analysis=analysis,
                progress=_reported(
                    datetime.now(
                        tz=UTC,
                    )
                    - timedelta(
                        seconds=12,
                    ),
                ),
            )

        monkeypatch.setattr(
            target=commands,
            name="show",
            value=watched,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
            ],
        )

        assert result.exit_code == 0

        output = " ".join(
            result.output.split(),
        )

        assert search(
            pattern=(
                r"processing: looking the words up in the dictionary, 1 of 2; its worker last reported 1\d s ago;"
            ),
            string=output,
        )

        assert f"`wordwinnow show {analysis.id} --wait` waits for it" in output

        assert "admin requeue" not in output

    def test_a_worker_quiet_for_longer_than_the_requeue_allows_is_named_so(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        analysis = _processing()

        async def watched(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            local: bool,
        ) -> WatchedAnalysis:
            return WatchedAnalysis(
                analysis=analysis,
                progress=_reported(
                    _EPOCH,
                ),
            )

        monkeypatch.setattr(
            target=commands,
            name="show",
            value=watched,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
            ],
        )

        assert result.exit_code == 0

        assert "longer than the 600 s after which `wordwinnow admin requeue` hands it to a worker again" in " ".join(
            result.output.split(),
        )

    def test_the_json_carries_the_status_and_nothing_else(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        analysis = _processing()

        async def watched(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            local: bool,
        ) -> WatchedAnalysis:
            return WatchedAnalysis(
                analysis=analysis,
                progress=_reported(
                    _EPOCH,
                ),
            )

        monkeypatch.setattr(
            target=commands,
            name="show",
            value=watched,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--json",
            ],
        )

        assert result.exit_code == 0

        assert (
            loads(
                s=result.stdout,
            )["status"]
            == "processing"
        )

    def test_waiting_takes_up_the_analysis_named_and_shows_it_when_done(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        analysis = _processing()

        analysis.fail(
            at=_EPOCH,
            reason="RuntimeError: the tagger was not there",
        )

        waited: list[AnalysisId] = []

        async def wait_remotely(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            timeout_seconds: float,
            progress: AnalysisProgress = UNWATCHED,
            watch: DictionaryWatch | None = None,
        ) -> Analysis:
            waited.append(
                analysis_id,
            )

            return analysis

        monkeypatch.setattr(
            target=commands,
            name="wait_remotely",
            value=wait_remotely,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--wait",
                "--timeout",
                "5",
            ],
        )

        assert result.exit_code == 0

        assert waited == [
            analysis.id,
        ]

        assert analysis.status is AnalysisStatus.FAILED

        assert "the tagger was not there" in result.output

    def test_waiting_on_the_local_store_is_refused(
        self,
        *,
        workspace: Path,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=AnalysisId.new(),
                ),
                "--local",
                "--wait",
            ],
        )

        assert result.exit_code == 2

        assert (
            "--wait waits for the services"
            in Text.from_ansi(
                text=result.output,
            ).plain
        )

    def test_an_interrupted_wait_says_the_analysis_goes_on_and_ends_as_an_interrupt_does(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        async def interrupted(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            timeout_seconds: float,
            progress: AnalysisProgress = UNWATCHED,
            watch: DictionaryWatch | None = None,
        ) -> Analysis:
            raise commands.AnalysisWaitInterruptedError(
                f"stopped waiting for analysis {analysis_id}, which goes on without this command",
            )

        monkeypatch.setattr(
            target=commands,
            name="wait_remotely",
            value=interrupted,
        )

        analysis_id = AnalysisId.new()

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis_id,
                ),
                "--wait",
            ],
        )

        assert result.exit_code == 130

        assert f"stopped waiting for analysis {analysis_id}, which goes on" in " ".join(
            result.output.split(),
        )

        assert "error:" not in result.output


@fixture
def empty_directory(
    *,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Path:
    directory = tmp_path / "empty"

    directory.mkdir()

    monkeypatch.chdir(
        path=directory,
    )

    return directory


def _modification_times(
    directory: Path,
    /,
) -> dict[Path, int]:
    return {
        path: path.stat().st_mtime_ns
        for path in directory.rglob(
            pattern="*",
        )
        if path.is_file()
    }


@final
class TestSetup:
    def test_it_prepares_an_analysis_in_the_users_directory_and_a_second_run_changes_nothing(
        self,
        *,
        empty_directory: Path,
        requested_lists: list[str],
    ) -> None:
        data = user_data_path(
            appname="wordwinnow",
        )

        ready = (
            f"ready   NLTK data, reference lists in {data / 'reference'}, level model at "
            f"{data / 'cefr_level_model.joblib'}"
        )

        first = _runner.invoke(
            app=app,
            args=[
                "setup",
            ],
        )

        assert first.exit_code == 0, first.output

        lines = first.stdout.splitlines()

        assert lines[:2] == [f"downloaded reference list {name} to {data / 'reference'}" for name in REFERENCE_FILES]

        assert lines[2].startswith(
            "wrote ",
        )

        assert lines[2].endswith(
            f" to {data / 'cefr_level_model.joblib'}",
        )

        assert lines[3:] == [
            ready,
            *REFERENCE_CITATIONS,
        ]

        written = _modification_times(
            data,
        )

        assert sorted(
            path.relative_to(
                other=data,
            ).as_posix()
            for path in written
        ) == [
            "cefr_level_model.joblib",
            "cefr_level_model.joblib.metadata.json",
            f"reference/{REFERENCE_FILES[0]}",
            f"reference/{REFERENCE_FILES[1]}",
        ]

        second = _runner.invoke(
            app=app,
            args=[
                "setup",
            ],
        )

        assert second.exit_code == 0, second.output

        assert second.stdout.splitlines() == [
            ready,
            *REFERENCE_CITATIONS,
        ]

        assert written == _modification_times(
            data,
        )

        assert len(
            requested_lists,
        ) == len(
            REFERENCE_FILES,
        )

        assert (
            tuple(
                empty_directory.iterdir(),
            )
            == ()
        )

    @mark.parametrize(
        argnames="state",
        argvalues=(
            "another scikit-learn and unreadable",
            "cut short",
        ),
    )
    def test_it_trains_again_a_model_it_cannot_use_and_a_second_run_changes_nothing(
        self,
        *,
        empty_directory: Path,
        requested_lists: list[str],
        fitted_model: SklearnLevelEstimator,
        put_model: Callable[..., None],
        state: str,
    ) -> None:
        data = user_data_path(
            appname="wordwinnow",
        )

        path = data / "cefr_level_model.joblib"

        put_model(
            path=path,
            state=state,
        )

        first = _runner.invoke(
            app=app,
            args=[
                "setup",
            ],
        )

        assert first.exit_code == 0, first.output

        assert "Traceback" not in first.output

        assert (
            sum(
                1
                for line in first.stdout.splitlines()
                if line.startswith(
                    "wrote ",
                )
            )
            == 1
        )

        metadata = read_metadata(
            path=path,
        )

        assert metadata is not None

        assert metadata.sklearn_version == installed_sklearn_version()

        assert metadata.train_size > fitted_model.metadata.train_size

        assert (
            load(
                path=path,
            )
            is not None
        )

        assert sorted(path.name for path in data.iterdir()) == [
            "cefr_level_model.joblib",
            "cefr_level_model.joblib.metadata.json",
            "reference",
        ]

        written = _modification_times(
            data,
        )

        second = _runner.invoke(
            app=app,
            args=[
                "setup",
            ],
        )

        assert second.exit_code == 0, second.output

        assert second.stdout.splitlines() == [
            f"ready   NLTK data, reference lists in {data / 'reference'}, level model at {path}",
            *REFERENCE_CITATIONS,
        ]

        assert written == _modification_times(
            data,
        )

        assert (
            tuple(
                empty_directory.iterdir(),
            )
            == ()
        )

    def test_a_list_that_cannot_be_fetched_prints_one_error_line_and_exits_one(
        self,
        *,
        empty_directory: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        def download_reference_lists(
            *,
            directory: Path,
            client: object = None,
        ) -> tuple[str, ...]:
            raise LinguisticResourcesMissingError(
                "the reference list foo could not be fetched (nonexistent)",
            )

        monkeypatch.setattr(
            target=commands,
            name="download_reference_lists",
            value=download_reference_lists,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "setup",
            ],
        )

        assert result.exit_code == 1

        assert result.stdout == ""

        assert result.output.splitlines() == [
            "error: the reference list foo could not be fetched (nonexistent)",
        ]

        assert (
            tuple(
                empty_directory.iterdir(),
            )
            == ()
        )
