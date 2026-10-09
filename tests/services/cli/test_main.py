"""
The command line itself, through Typer's runner.

These tests need the NLTK data `make nltk-data` installs.
"""

from datetime import (
    UTC,
    datetime,
)
from json import (
    loads,
)
from pathlib import (
    Path,
)
from re import (
    match,
)
from typing import (
    Final,
    final,
)

from pytest import (
    MonkeyPatch,
    fixture,
    mark,
)
from rich.console import (
    Console,
)
from typer.testing import (
    CliRunner,
)

from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisOptions,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.infrastructure.composition import (
    API_SOURCES,
    FEED_SOURCE,
)
from wordwinnow.infrastructure.settings import (
    Settings,
)
from wordwinnow.services.cli import (
    commands,
    main,
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

        assert "drunken" in {entry["lemma"] for entry in payload["items"]}

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

        assert "--level" in result.output

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
                "out.csv",
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
