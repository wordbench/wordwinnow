"""
The command line itself, through Typer's runner.

These tests need the NLTK data `make nltk-data` installs.
"""

from asyncio import (
    run,
)
from collections.abc import (
    Callable,
    Sequence,
)
from csv import (
    reader,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from io import (
    BytesIO,
    StringIO,
    TextIOWrapper,
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
from tomllib import (
    loads as load_toml,
)
from typing import (
    Final,
    final,
)

from httpx2 import (
    AsyncBaseTransport,
    MockTransport,
    Request,
    Response,
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
from typer.core import (
    TyperGroup,
)
from typer.main import (
    get_command,
)
from typer.testing import (
    CliRunner,
)

from wordwinnow.application.dto import (
    AnalysisSummary,
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
    StageTiming,
    request_an_analysis,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
    LevelAssessment,
    LevelSource,
)
from wordwinnow.domain.dictionary import (
    LookupOutcome,
    SelectedDefinition,
)
from wordwinnow.domain.document import (
    Document,
    DocumentOrigin,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
    InvalidIdentifierError,
)
from wordwinnow.domain.language import (
    PartOfSpeech,
)
from wordwinnow.domain.learner import (
    LearnerProfile,
)
from wordwinnow.domain.senses import (
    LexicalSense,
)
from wordwinnow.domain.study import (
    DictionaryInformation,
    StudyItem,
    StudyTier,
)
from wordwinnow.domain.vocabulary import (
    VocabularyItem,
)
from wordwinnow.infrastructure.composition import (
    API_SOURCES,
    FEED_SOURCE,
    storage_profile,
)
from wordwinnow.infrastructure.lexicon.reference_lists import (
    REFERENCE_FILES,
)
from wordwinnow.infrastructure.lexicon.reference_sources import (
    REFERENCE_CITATIONS,
)
from wordwinnow.infrastructure.linguistics.nltk_resources import (
    REQUIRED_RESOURCES,
    NltkResource,
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
    IntakeAddressError,
    IntakeRejectedError,
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
    RemoteAnalysisProgress,
    WorkProgress,
)

_TEXT: Final = (
    "The King wanted the photograph back. The groom looked very drunken, but Irene Adler kept the photograph."
)

_PAGE: Final = f'<!DOCTYPE html>\n<html lang="en">\n<body><p>{_TEXT}</p></body>\n</html>\n'

# TEST:
# What follows the name of an input in the refusal of a whole web page, written out here and not taken from the
# product, so that a change to its words fails the tests that read them.
_PAGE_REFUSAL: Final = (
    "is a web page; extract its article's text first, as the operating guide at github.com/wordbench/wordwinnow shows"
)

_runner: Final = CliRunner()


def _standard_input(
    content: bytes,
    /,
    *,
    errors: str = "strict",
) -> TextIOWrapper:
    """
    A text stream over `content`, as standard input is: the command reads the
    bytes from its buffer, and `errors` says how the stream itself decodes.
    """

    return TextIOWrapper(
        buffer=BytesIO(
            initial_bytes=content,
        ),
        encoding="utf-8",
        errors=errors,
    )


def _words(
    text: str,
    /,
) -> str:
    return " ".join(
        text.split(),
    )


# NOTE:
# A usage error is drawn in a box, and a message longer than the terminal is wide runs over its lines between the
# box's sides, so the sides are taken out before the words are read.
def _usage_error(
    output: str,
    /,
) -> str:
    return _words(
        Text.from_ansi(
            text=output,
        ).plain.replace(
            "│",
            " ",
        ),
    )


_LEARNER_PANEL: Final = "The learner's commands"

_OPERATOR_PANEL: Final = "The operator's commands"

_GUIDANCE: Final = "Start with wordwinnow setup."


def _plain(
    output: str,
    /,
) -> str:
    return Text.from_ansi(
        text=output,
    ).plain


def _line_of(
    plain: str,
    text: str,
    /,
) -> int:
    start = plain.index(
        text,
    )

    return plain[:start].count(
        "\n",
    )


# NOTE:
# The rows of one panel of the top-level help, in the order printed: a row starts with the name right after the
# panel's side, and the lines that continue a summary start with spaces there.
def _panel_rows(
    plain: str,
    title: str,
    /,
) -> tuple[str, ...]:
    names: list[str] = []

    inside = False

    for line in plain.splitlines():
        if line.startswith(
            f"╭─ {title} ",
        ):
            inside = True

        elif inside and line.startswith(
            "╰",
        ):
            break

        elif inside and (
            row := match(
                pattern=r"│ (\S+)\s",
                string=line,
            )
        ):
            names.append(
                row.group(
                    1,
                ),
            )

    return tuple(
        names,
    )


def _helps(
    group: TyperGroup,
    prefix: str,
    /,
) -> dict[str, str]:
    helps: dict[str, str] = {}

    for (
        command_name,
        command,
    ) in group.commands.items():
        path = f"{prefix}{command_name}"

        helps[path] = command.help or ""

        if isinstance(
            command,
            TyperGroup,
        ):
            helps.update(
                _helps(
                    command,
                    f"{path} ",
                ),
            )

    return helps


def _written_version() -> str:
    return str(
        object=load_toml(
            (
                Path(
                    __file__,
                ).parents[3]
                / "pyproject.toml"
            ).read_text(
                encoding="utf-8",
            ),
        )["project"]["version"],
    )


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


# TEST:
# Every way a command reaches the distributed mode, replaced by one that records itself and fails, so that a test of
# the local mode shows it called none of them.
@fixture
def services_called(
    *,
    monkeypatch: MonkeyPatch,
) -> list[str]:
    called: list[str] = []

    async def analyze_remotely(
        *,
        request: AnalysisRequest,
        settings: Settings,
        wait: bool,
        timeout_seconds: float,
        progress: AnalysisProgress = UNWATCHED,
        watch: DictionaryWatch | None = None,
    ) -> Analysis:
        called.append(
            "analyze_remotely",
        )

        raise RuntimeError(
            "the distributed mode was asked",
        )

    async def wait_remotely(
        *,
        analysis_id: AnalysisId,
        settings: Settings,
        timeout_seconds: float,
        progress: AnalysisProgress = UNWATCHED,
        watch: DictionaryWatch | None = None,
    ) -> Analysis:
        called.append(
            "wait_remotely",
        )

        raise RuntimeError(
            "the distributed mode was asked",
        )

    def intake_client(
        *,
        base_url: str,
        transport: AsyncBaseTransport | None = None,
        needed_by: str = "this command",
        otherwise: str | None = None,
    ) -> None:
        called.append(
            "IntakeClient",
        )

        raise RuntimeError(
            "the distributed mode was asked",
        )

    monkeypatch.setattr(
        target=commands,
        name="analyze_remotely",
        value=analyze_remotely,
    )

    monkeypatch.setattr(
        target=commands,
        name="wait_remotely",
        value=wait_remotely,
    )

    monkeypatch.setattr(
        target=commands,
        name="IntakeClient",
        value=intake_client,
    )

    return called


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

        assert f"wordwinnow show {analysis_id} shows this again." in _words(
            shown.output,
        )

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

        assert (
            bad.output
            == "error: 'not-an-id' is not an analysis identifier; `wordwinnow list` shows the identifiers of the "
            "stored analyses\n"
        )

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

        assert "Showing 1 of" in table.output

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

    def test_the_report_of_a_text_read_from_standard_input_says_so(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            target=main,
            name="stdin",
            value=_standard_input(
                _TEXT.encode(),
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
            ],
        )

        assert piped.exit_code == 0, piped.output

        assert "read from standard input, beginning" in piped.output

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
            value=_standard_input(
                _TEXT.encode(),
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
            value=_standard_input(
                b"\f\n",
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

        # NOTE:
        # The learner is told the code to quote and where the whole reason is, which only the operator's view prints.
        assert "Status failed: Wordwinnow met an internal error, code RuntimeError; run wordwinnow doctor" in _words(
            result.output,
        )

        assert "the tagger was not there" not in result.output

        operator = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--local",
                "--level",
                "B1",
                "--view",
                "operator",
            ],
        )

        assert operator.exit_code == 1

        assert "Failure RuntimeError: the tagger was not there" in _words(
            operator.stderr,
        )

        assert "the tagger was not there" not in operator.stdout

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

        payload = loads(
            s=result.stdout,
        )

        assert payload["status"] == "completed"

        lines = result.stderr.splitlines()

        figures_at = next(
            (
                index
                for (
                    index,
                    line,
                ) in enumerate(
                    iterable=lines,
                )
                if line.startswith(
                    "Analysis ",
                )
            ),
            None,
        )

        records = lines if figures_at is None else lines[: figures_at - 1]

        # NOTE:
        # Standard error holds log records and nothing else, not even the blank line a progress display leaves behind
        # on a stream that is not a terminal; the operator's view then adds the analysis's figures after them, which
        # the learner's view never prints.
        assert all(
            match(
                pattern=r"\d\d:\d\d:\d\d\.\d{3} (?:DEBUG|INFO|WARNING|ERROR) ",
                string=line,
            )
            for line in records
        )

        if view == "learner":
            assert figures_at is None

        else:
            assert figures_at is not None

            assert lines[figures_at - 1] == ""

            assert match(
                pattern=(
                    rf"Analysis {payload['analysis_id']} Document custom_text: a-scandal-in-bohemia\.txt Status "
                    r"completed Levels reference list \d+, model \d+, frequency heuristic \d+ Timing queue wait "
                    r"\d+\.\d\ds; \S+"
                ),
                string=_words(
                    "\n".join(
                        lines[figures_at:],
                    ),
                ),
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

    def test_the_examples_of_analyze_work_as_written_by_a_learner_who_installed_it(
        self,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "--help",
            ],
        )

        assert result.exit_code == 0, result.output

        plain = Text.from_ansi(
            text=result.stdout,
        ).plain

        examples = plain.partition(
            "Examples",
        )[2]

        assert "wordwinnow analyze chapter.txt --level A2 --target B1 --no-dictionary" in examples

        assert "wordwinnow analyze --source nyt-rss --topic Science --level B1" in examples

        assert "--local" not in examples

        assert "data/corpus" not in plain

    def test_the_help_sorts_the_commands_into_the_learners_panel_and_the_operators(
        self,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "--help",
            ],
        )

        assert result.exit_code == 0, result.output

        plain = _plain(
            result.stdout,
        )

        assert _line_of(
            plain,
            _LEARNER_PANEL,
        ) < _line_of(
            plain,
            _OPERATOR_PANEL,
        )

        assert _panel_rows(
            plain,
            _LEARNER_PANEL,
        ) == (
            "setup",
            "analyze",
            "show",
            "list",
            "doctor",
        )

        assert _panel_rows(
            plain,
            _OPERATOR_PANEL,
        ) == (
            "stats",
            "admin",
            "db",
            "serve",
            "work",
            "ml",
            "bench",
        )

        assert not any(
            line.startswith(
                "╭─ Commands ",
            )
            for line in plain.splitlines()
        )

    def test_the_bare_command_prints_both_panels_and_the_guidance_and_exits_two(
        self,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[],
        )

        assert result.exit_code == 2, result.output

        plain = _plain(
            result.output,
        )

        assert (
            len(
                _panel_rows(
                    plain,
                    _LEARNER_PANEL,
                ),
            )
            == 5
        )

        assert (
            len(
                _panel_rows(
                    plain,
                    _OPERATOR_PANEL,
                ),
            )
            == 7
        )

        assert (
            plain.count(
                _GUIDANCE,
            )
            == 1
        )

    def test_the_guidance_follows_the_panels_once_and_is_in_no_subcommands_help(
        self,
    ) -> None:
        top = _plain(
            _runner.invoke(
                app=app,
                args=[
                    "--help",
                ],
            ).stdout,
        )

        assert (
            top.count(
                _GUIDANCE,
            )
            == 1
        )

        assert _line_of(
            top,
            _GUIDANCE,
        ) > _line_of(
            top,
            _OPERATOR_PANEL,
        )

        for arguments in (
            [
                "analyze",
                "--help",
            ],
            [
                "admin",
                "--help",
            ],
        ):
            assert _GUIDANCE not in _plain(
                _runner.invoke(
                    app=app,
                    args=arguments,
                ).stdout,
            )

    @mark.parametrize(
        argnames="command",
        argvalues=(
            [],
            [
                "analyze",
            ],
            [
                "admin",
            ],
            [
                "admin",
                "requeue",
            ],
        ),
    )
    def test_dash_h_prints_what_help_prints(
        self,
        *,
        command: list[str],
    ) -> None:
        short = _runner.invoke(
            app=app,
            args=[
                *command,
                "-h",
            ],
        )

        long = _runner.invoke(
            app=app,
            args=[
                *command,
                "--help",
            ],
        )

        assert short.exit_code == 0, short.output

        assert long.exit_code == 0, long.output

        assert short.output == long.output

    def test_every_summary_is_one_line_and_the_two_that_broke_say_what_they_were_asked_to(
        self,
    ) -> None:
        root = get_command(
            typer_instance=app,
        )

        assert isinstance(
            root,
            TyperGroup,
        )

        helps = _helps(
            root,
            "",
        )

        assert (
            len(
                helps,
            )
            == 24
        )

        for (
            path,
            help_text,
        ) in helps.items():
            assert (
                "\n"
                not in help_text.split(
                    sep="\n\n",
                )[0]
            ), path

        assert (
            helps["show"].split(
                sep="\n\n",
            )[0]
            == "Show a stored analysis, or wait for one still in progress."
        )

        assert (
            helps["admin requeue"].split(
                sep="\n\n",
            )[0]
            == "Hand stale analyses back to the workers."
        )

    def test_version_prints_the_version_pyproject_writes_and_nothing_else(
        self,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "--version",
            ],
        )

        assert result.exit_code == 0, result.output

        assert result.stdout == f"wordwinnow {_written_version()}\n"

        assert result.stderr == ""

        help_result = _runner.invoke(
            app=app,
            args=[
                "--help",
            ],
        )

        options = (
            _plain(
                help_result.stdout,
            )
            .partition(
                "Options",
            )[2]
            .partition(
                "╰",
            )[0]
        )

        assert "--version" in options

        assert "Show the installed version and exit." in options

    def test_version_runs_nothing_else(
        self,
        *,
        monkeypatch: MonkeyPatch,
    ) -> None:
        called: list[str] = []

        def set_up(
            *,
            settings: Settings,
            progress: WorkProgress = UNWATCHED,
        ) -> tuple[str, ...]:
            called.append(
                "set_up",
            )

            raise AssertionError(
                "`setup` ran after `--version`",
            )

        monkeypatch.setattr(
            target=commands,
            name="set_up",
            value=set_up,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "--version",
                "setup",
            ],
        )

        assert result.exit_code == 0, result.output

        assert result.stdout == f"wordwinnow {_written_version()}\n"

        assert called == []


def _requested() -> Analysis:
    return request_an_analysis(
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


def _processing() -> Analysis:
    analysis = _requested()

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
                "--remote",
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

        assert f"`wordwinnow show {analysis.id} --remote --wait` waits for it" in output

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
                "--remote",
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
                "--remote",
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
                "--remote",
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

        assert "Status failed: Wordwinnow met an internal error, code RuntimeError; run wordwinnow doctor" in _words(
            result.output,
        )

        assert "the tagger was not there" not in result.output

        operator = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--remote",
                "--wait",
                "--timeout",
                "5",
                "--view",
                "operator",
            ],
        )

        assert operator.exit_code == 0

        assert "Failure RuntimeError: the tagger was not there" in _words(
            operator.stderr,
        )

        assert "the tagger was not there" not in operator.stdout

    @mark.parametrize(
        argnames="mode",
        argvalues=(
            (),
            ("--local",),
        ),
    )
    def test_waiting_without_remote_is_refused(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        mode: tuple[str, ...],
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=AnalysisId.new(),
                ),
                *mode,
                "--wait",
            ],
        )

        assert result.exit_code == 2

        assert (
            "--wait waits for the distributed mode's services, which --remote asks for; a local analysis is done "
            "when the command that ran it ends"
        ) in _usage_error(
            result.output,
        )

        assert not services_called

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
                "--remote",
                "--wait",
            ],
        )

        assert result.exit_code == 130

        assert f"stopped waiting for analysis {analysis_id}, which goes on" in " ".join(
            result.output.split(),
        )

        assert "error:" not in result.output


def _stored(
    analysis: Analysis,
    /,
) -> None:
    async def store() -> None:
        async with (
            storage_profile(
                settings=Settings(),
            ) as profile,
            profile.new_unit_of_work() as uow,
        ):
            await uow.analyses.save(
                analysis=analysis,
            )

            await uow.commit()

    run(
        main=store(),
    )


def _completed(
    *,
    meaning: str | None = None,
    other_meaning: str | None = None,
    retention: timedelta | None = None,
    at: datetime = _EPOCH,
) -> Analysis:
    """
    A completed analysis of one word, with no dictionary asked, and `meaning`
    as WordNet's gloss of the word when there is one, and `other_meaning` as a
    second sense of it when that is given, of a text kept for `retention` when
    it is given, requested at `at`.
    """

    analysis = request_an_analysis(
        document=Document(
            title="foo",
            text="foo",
            origin=DocumentOrigin.CUSTOM_TEXT,
            reference="foo.txt",
            retention=retention,
        ),
        profile=LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
        ),
        options=AnalysisOptions(
            include_dictionary=False,
        ),
        at=at,
    )

    analysis.start(
        at=at,
    )

    analysis.complete(
        at=at,
        items=(
            StudyItem(
                item=VocabularyItem(
                    lemma="foo",
                    part_of_speech=PartOfSpeech.NOUN,
                    occurrence_count=2,
                    example_sentence="foo",
                ),
                zipf_frequency=4.0,
                level=LevelAssessment(
                    level=CefrLevel.B2,
                    source=LevelSource.REFERENCE_LIST,
                ),
                tier=StudyTier.FOCUS,
                dictionary=None,
                senses=(
                    ()
                    if meaning is None
                    else (
                        LexicalSense(
                            key="foo.n.01",
                            part_of_speech=PartOfSpeech.NOUN,
                            gloss=meaning,
                            example=None,
                            synonyms=(),
                            hypernyms=(),
                            antonyms=(),
                            usage_count=1,
                            hyponym_count=0,
                            category="noun.attribute",
                        ),
                        *(
                            ()
                            if other_meaning is None
                            else (
                                LexicalSense(
                                    key="foo.n.02",
                                    part_of_speech=PartOfSpeech.NOUN,
                                    gloss=other_meaning,
                                    example=None,
                                    synonyms=(),
                                    hypernyms=(),
                                    antonyms=(),
                                    usage_count=0,
                                    hyponym_count=0,
                                    category="noun.attribute",
                                ),
                            )
                        ),
                    )
                ),
            ),
        ),
        stage_timings=(
            StageTiming(
                stage=Stage.WINNOWING,
                seconds=0.5,
            ),
        ),
    )

    return analysis


@final
class TestTheDefaultMode:
    def test_with_no_mode_option_the_commands_run_in_this_process_and_call_no_service(
        self,
        *,
        workspace: Path,
        services_called: list[str],
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
                "--no-dictionary",
                "--level",
                "A2",
                "--json",
            ],
        )

        assert analyzed.exit_code == 0, analyzed.output

        payload = loads(
            s=analyzed.stdout,
        )

        assert payload["status"] == "completed"

        listed = _runner.invoke(
            app=app,
            args=[
                "list",
            ],
        )

        assert listed.exit_code == 0, listed.output

        assert payload["analysis_id"] in listed.output

        shown = _runner.invoke(
            app=app,
            args=[
                "show",
                payload["analysis_id"],
            ],
        )

        assert shown.exit_code == 0, shown.output

        assert f"wordwinnow show {payload['analysis_id']} shows this again." in _words(
            shown.output,
        )

        assert not services_called

    def test_with_remote_the_commands_reach_the_services(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        reached: list[str] = []

        requested = _requested()

        async def analyze_remotely(
            *,
            request: AnalysisRequest,
            settings: Settings,
            wait: bool,
            timeout_seconds: float,
            progress: AnalysisProgress = UNWATCHED,
            watch: DictionaryWatch | None = None,
        ) -> Analysis:
            reached.append(
                f"analyze wait={wait}",
            )

            return requested

        async def show(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            local: bool,
        ) -> WatchedAnalysis:
            reached.append(
                f"show local={local}",
            )

            return WatchedAnalysis(
                analysis=requested,
                progress=None,
            )

        async def list_recent(
            *,
            settings: Settings,
            local: bool,
            limit: int,
        ) -> tuple[AnalysisSummary, ...]:
            reached.append(
                f"list local={local}",
            )

            return ()

        for (
            name,
            double,
        ) in (
            (
                "analyze_remotely",
                analyze_remotely,
            ),
            (
                "show",
                show,
            ),
            (
                "list_recent",
                list_recent,
            ),
        ):
            monkeypatch.setattr(
                target=commands,
                name=name,
                value=double,
            )

        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        analyzed = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--remote",
                "--no-wait",
                "--level",
                "A2",
            ],
        )

        assert analyzed.exit_code == 0, analyzed.output

        # NOTE:
        # The two commands the line hands out are the ones that reach the services, so each carries --remote.
        assert (
            f"requested analysis {requested.id}; `wordwinnow show {requested.id} --remote --wait` waits for it, and "
            f"`wordwinnow show {requested.id} --remote` shows how far it has come"
        ) in _words(
            analyzed.output,
        )

        for command in (
            "show",
            "list",
        ):
            result = _runner.invoke(
                app=app,
                args=[
                    command,
                    *(
                        (
                            str(
                                object=requested.id,
                            ),
                        )
                        if command == "show"
                        else ()
                    ),
                    "--remote",
                ],
            )

            assert result.exit_code == 0, result.output

        assert reached == [
            "analyze wait=False",
            "show local=False",
            "list local=False",
        ]


@final
class TestOptionsOfTheDistributedMode:
    @mark.parametrize(
        argnames="mode",
        argvalues=(
            (),
            ("--local",),
        ),
    )
    def test_not_waiting_without_remote_is_refused_before_anything_is_read_or_stored(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        mode: tuple[str, ...],
    ) -> None:
        # NOTE:
        # The file does not exist, so a command that read it before it refused would end with exit code 1 and the
        # file's own message instead.
        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "no-such-file.txt",
                *mode,
                "--no-wait",
                "--level",
                "A2",
            ],
        )

        assert result.exit_code == 2

        assert (
            "--no-wait leaves the analysis to the distributed mode's services, which --remote asks for; a local "
            "analysis is done when the command that ran it ends"
        ) in _usage_error(
            result.output,
        )

        assert not (workspace / "wordwinnow.db").exists()

        assert not services_called


@final
class TestAnIntakeThatCannotServe:
    def test_an_error_line_prints_brackets_as_written(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        async def list_recent(
            *,
            settings: Settings,
            local: bool,
            limit: int,
            transport: AsyncBaseTransport | None = None,
        ) -> tuple[AnalysisSummary, ...]:
            raise IntakeAddressError(
                value="[bold]foo[/bold]",
            )

        monkeypatch.setattr(
            target=commands,
            name="list_recent",
            value=list_recent,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "list",
                "--remote",
            ],
        )

        assert result.exit_code == 1

        assert result.output.splitlines() == [
            'error: WORDWINNOW_INTAKE_URL is "[bold]foo[/bold]", which is not an http:// or https:// address with a '
            "host",
        ]

    def test_an_invalid_intake_address_fails_with_one_line(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_INTAKE_URL",
            value="localhost:8010",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "list",
                "--remote",
            ],
        )

        assert result.exit_code == 1

        assert result.output.splitlines() == [
            'error: WORDWINNOW_INTAKE_URL is "localhost:8010", which is not an http:// or https:// address with a '
            "host",
        ]

    def test_an_invalid_intake_address_leaves_the_local_mode_alone(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        monkeypatch: MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_INTAKE_URL",
            value="localhost:8010",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "list",
            ],
        )

        assert result.exit_code == 0, result.output

        assert not services_called

    @mark.parametrize(
        argnames=(
            "headers",
            "content",
            "line",
        ),
        argvalues=(
            (
                {
                    "content-type": "text/html; charset=utf-8",
                },
                b"<!doctype html><title>foo</title>",
                "error: something answered at http://intake.invalid:8010, but not as a Wordwinnow intake service "
                "(HTTP 200, text/html)",
            ),
            (
                {
                    "content-type": "application/json",
                },
                b'{"foo": "bar"}',
                "error: the service at http://intake.invalid:8010 answered HTTP 200 with JSON this command cannot "
                "read; if it is the intake service, its version may not match this command's",
            ),
        ),
        ids=(
            "a page",
            "json of another shape",
        ),
    )
    def test_an_answer_that_is_not_intake_ends_analyze_in_one_line_with_status_one(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
        headers: dict[str, str],
        content: bytes,
        line: str,
    ) -> None:
        monkeypatch.setenv(
            name="WORDWINNOW_INTAKE_URL",
            value="http://intake.invalid:8010",
        )

        monkeypatch.setattr(
            target=main,
            name="stdin",
            value=_standard_input(
                _TEXT.encode(),
            ),
        )

        remotely = commands.analyze_remotely

        def handler(
            request: Request,
            /,
        ) -> Response:
            return Response(
                status_code=200,
                headers=headers,
                content=content,
            )

        async def analyze_remotely(
            *,
            request: AnalysisRequest,
            settings: Settings,
            wait: bool,
            timeout_seconds: float,
            progress: RemoteAnalysisProgress = UNWATCHED,
            watch: DictionaryWatch | None = None,
        ) -> Analysis:
            return await remotely(
                request=request,
                settings=settings,
                wait=wait,
                timeout_seconds=timeout_seconds,
                transport=MockTransport(
                    handler=handler,
                ),
                progress=progress,
                watch=watch,
            )

        monkeypatch.setattr(
            target=commands,
            name="analyze_remotely",
            value=analyze_remotely,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "-",
                "--remote",
                "--level",
                "B1",
                "--no-dictionary",
            ],
        )

        assert result.exit_code == 1, result.output

        assert result.output == f"{line}\n"

    def test_a_control_character_in_an_error_line_is_shown_escaped(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        async def list_recent(
            *,
            settings: Settings,
            local: bool,
            limit: int,
            transport: AsyncBaseTransport | None = None,
        ) -> tuple[AnalysisSummary, ...]:
            raise IntakeRejectedError(
                "foo\x1b[2Jbar\nbaz\rqux\t\x00\x7f\x9b\u202equux\u2028corge",
            )

        monkeypatch.setattr(
            target=commands,
            name="list_recent",
            value=list_recent,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "list",
                "--remote",
            ],
        )

        assert result.exit_code == 1

        assert result.output == "error: foo\\x1b[2Jbar\\nbaz\\rqux\\t\\x00\\x7f\\x9b\\u202equux\\u2028corge\n"

    def test_a_final_backslash_prints_as_written(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        async def list_recent(
            *,
            settings: Settings,
            local: bool,
            limit: int,
            transport: AsyncBaseTransport | None = None,
        ) -> tuple[AnalysisSummary, ...]:
            raise IntakeRejectedError(
                "foo\\",
            )

        monkeypatch.setattr(
            target=commands,
            name="list_recent",
            value=list_recent,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "list",
                "--remote",
            ],
        )

        assert result.exit_code == 1

        assert result.output == "error: foo\\\n"

    @mark.parametrize(
        argnames="remote",
        argvalues=(
            False,
            True,
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    def test_standard_input_that_is_not_utf8_is_refused_in_one_line(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        monkeypatch: MonkeyPatch,
        remote: bool,
    ) -> None:
        # NOTE:
        # A stream that takes the bytes in as the POSIX locale does, with `surrogateescape`, where a stream's own
        # `read` would hand the command text that fails later and not at once.
        monkeypatch.setattr(
            target=main,
            name="stdin",
            value=_standard_input(
                b"caf\xe9",
                errors="surrogateescape",
            ),
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "-",
                *(("--remote",) if remote else ()),
                "--level",
                "B1",
                "--no-dictionary",
            ],
        )

        assert result.exit_code == 1

        assert result.output == "error: standard input is not UTF-8 text\n"

        assert not (workspace / "wordwinnow.db").exists()

        assert not services_called

    @mark.parametrize(
        argnames="remote",
        argvalues=(
            False,
            True,
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    def test_a_web_page_on_standard_input_is_refused_in_one_line(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        monkeypatch: MonkeyPatch,
        remote: bool,
    ) -> None:
        monkeypatch.setattr(
            target=main,
            name="stdin",
            value=_standard_input(
                _PAGE.encode(),
            ),
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "-",
                *(("--remote",) if remote else ()),
                "--level",
                "B1",
                "--no-dictionary",
            ],
        )

        assert result.exit_code == 1, result.output

        assert result.output == f"error: standard input {_PAGE_REFUSAL}\n"

        assert not (workspace / "wordwinnow.db").exists()

        assert not services_called

    @mark.parametrize(
        argnames="remote",
        argvalues=(
            False,
            True,
        ),
        ids=(
            "local",
            "remote",
        ),
    )
    def test_a_web_page_in_a_file_is_refused_in_one_line(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        remote: bool,
    ) -> None:
        page = workspace / "page.html"

        page.write_text(
            data=_PAGE,
            encoding="utf-8",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                str(
                    object=page,
                ),
                *(("--remote",) if remote else ()),
                "--level",
                "B1",
                "--no-dictionary",
            ],
        )

        assert result.exit_code == 1, result.output

        assert result.output == f"error: {page} {_PAGE_REFUSAL}\n"

        assert not (workspace / "wordwinnow.db").exists()

        assert not services_called


@final
class TestShowingALocalAnalysis:
    def test_an_id_the_store_lacks_is_one_line_that_says_where_else_to_look(
        self,
        *,
        workspace: Path,
        services_called: list[str],
    ) -> None:
        analysis_id = AnalysisId.new()

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis_id,
                ),
            ],
        )

        assert result.exit_code == 1

        assert (
            _words(
                result.output,
            )
            == f"error: no analysis {analysis_id} in the local store; --remote looks for it in the distributed mode"
        )

        assert not services_called

    @mark.parametrize(
        argnames="stored_as",
        argvalues=(
            _requested,
            _processing,
        ),
        ids=(
            "requested",
            "processing",
        ),
    )
    def test_a_local_analysis_left_unfinished_says_it_goes_on_only_while_its_command_runs(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        stored_as: Callable[[], Analysis],
    ) -> None:
        analysis = stored_as()

        _stored(
            analysis,
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

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert "Status" in output

        assert output.endswith(
            "a local analysis goes on only while the command that runs it is running; if that command has stopped, "
            "run `wordwinnow analyze` on the text again",
        )

        assert "--wait" not in output

        assert "--remote" not in output

        assert not services_called

    def test_the_json_of_such_an_analysis_carries_no_line(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _processing()

        _stored(
            analysis,
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

        assert result.exit_code == 0, result.output

        assert (
            loads(
                s=result.stdout,
            )["status"]
            == "processing"
        )

    def test_a_meaning_longer_than_its_column_is_not_cut_at_80_columns(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # NOTE:
        # The positive control of the report's width tests: a console 80 columns wide, as macOS Terminal opens, and a
        # word in the meaning that the earlier table's narrow column could not hold.
        monkeypatch.setattr(
            target=main,
            name="_console",
            value=Console(
                width=80,
            ),
        )

        meaning = " ".join(
            (
                "foo",
                "bar",
                "baz",
                "qux",
                "incomprehensibilities",
            )
            * 4,
        )

        analysis = _completed(
            meaning=meaning,
        )

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--local",
            ],
        )

        assert result.exit_code == 0, result.output

        assert "…" not in result.output

        assert meaning in _words(
            result.output,
        )

    def test_a_stored_analysis_shown_with_the_view_prints_its_figures_too(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        analysis = _completed()

        _stored(
            analysis,
        )

        monkeypatch.setattr(
            target=main,
            name="_console",
            value=Console(
                width=100,
            ),
        )

        arguments = [
            "show",
            str(
                object=analysis.id,
            ),
        ]

        learner = _runner.invoke(
            app=app,
            args=arguments,
        )

        operator = _runner.invoke(
            app=app,
            args=[
                *arguments,
                "--view",
                "operator",
            ],
        )

        assert operator.exit_code == 0, operator.output

        assert operator.stdout == learner.stdout

        assert learner.stderr == ""

        assert _words(
            operator.stderr,
        ) == (
            f"Analysis {analysis.id} Document custom_text: foo.txt Status completed Levels reference list 1, model "
            "0, frequency heuristic 0 Timing queue wait 0.00s; winnowing=0.50s"
        )


@final
class TestAnalyzingWithAnUnusableModel:
    @mark.parametrize(
        argnames="state",
        argvalues=(
            "another scikit-learn",
            "another scikit-learn and unreadable",
            "cut short",
            "a sidecar that is not JSON",
            "without a sidecar",
        ),
    )
    def test_analyze_stops_in_one_line_that_names_setup_and_creates_no_store(
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

        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        result = _runner.invoke(
            app=app,
            args=[
                "analyze",
                "a-scandal-in-bohemia.txt",
                "--no-dictionary",
                "--level",
                "B1",
            ],
        )

        assert result.exit_code == 1, result.output

        output = _words(
            result.output,
        )

        assert output.startswith(
            "error: ",
        )

        assert output.endswith(
            "; run `wordwinnow setup`",
        )

        assert (
            output.count(
                "error:",
            )
            == 1
        )

        assert "Traceback" not in output

        assert not (workspace / "wordwinnow.db").exists()

    def test_show_and_list_still_work_beside_a_model_analyze_refuses(
        self,
        *,
        workspace: Path,
        stale_model: SklearnLevelEstimator,
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
                "--no-dictionary",
                "--level",
                "B1",
                "--json",
            ],
        )

        assert analyzed.exit_code == 0, analyzed.output

        analysis_id = loads(
            s=analyzed.stdout,
        )["analysis_id"]

        save(
            estimator=stale_model,
            path=Settings().level_model_path,
        )

        listed = _runner.invoke(
            app=app,
            args=[
                "list",
            ],
        )

        assert listed.exit_code == 0, listed.output

        assert analysis_id in listed.output

        shown = _runner.invoke(
            app=app,
            args=[
                "show",
                analysis_id,
            ],
        )

        assert shown.exit_code == 0, shown.output

        assert f"wordwinnow show {analysis_id} shows this again." in _words(
            shown.output,
        )


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


def _ready(
    reference_lists: Path,
    level_model: Path,
    /,
) -> tuple[str, ...]:
    """
    What `setup` prints once everything is ready, before the citations.
    """

    return (
        "ready   NLTK data: punkt_tab, averaged_perceptron_tagger_eng, stopwords, wordnet",
        f"        reference lists: {reference_lists}",
        f"        level model: {level_model}",
        "        next: wordwinnow analyze FILE --level LEVEL",
    )


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

        ready = _ready(
            data / "reference",
            data / "cefr_level_model.joblib",
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
            *ready,
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
            *ready,
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
            *_ready(
                data / "reference",
                path,
            ),
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


@final
class TestTheHelpOfTheViewOption:
    @mark.parametrize(
        argnames=(
            "command",
            "said",
        ),
        argvalues=(
            (
                "analyze",
                "What standard error shows: while the analysis runs, the learner's line, or, for an operator, that "
                "line with what the dictionary is doing under it; and for an operator, the analysis's figures after "
                "its report.",
            ),
            (
                "show",
                "What standard error shows: while --wait waits, the learner's line, or, for an operator, that line "
                "with what the dictionary is doing under it; and for an operator, the analysis's figures after its "
                "report, a stored analysis's too.",
            ),
        ),
    )
    def test_the_help_says_what_the_option_covers(
        self,
        *,
        command: str,
        said: str,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                command,
                "--help",
            ],
        )

        assert result.exit_code == 0, result.output

        assert said in _usage_error(
            result.output,
        )


@final
class TestShowingByIdentifier:
    @mark.parametrize(
        argnames="mode",
        argvalues=(
            "--local",
            "--remote",
        ),
    )
    @mark.parametrize(
        argnames=(
            "text",
            "expected",
        ),
        argvalues=(
            (
                "foo",
                "error: 'foo' is not an analysis identifier; `wordwinnow list` shows the identifiers of the stored "
                "analyses",
            ),
            (
                "it's",
                'error: "it\'s" is not an analysis identifier; `wordwinnow list` shows the identifiers of the stored '
                "analyses",
            ),
            (
                "foo\x1b[2Jbar\u202ebaz\nqux",
                r"error: 'foo\x1b[2Jbar\u202ebaz\nqux' is not an analysis identifier; `wordwinnow list` shows the "
                "identifiers of the stored analyses",
            ),
        ),
        ids=(
            "plain",
            "quote",
            "controls",
        ),
    )
    def test_a_text_that_is_not_an_identifier_is_one_line_that_points_to_list(
        self,
        *,
        workspace: Path,
        services_called: list[str],
        text: str,
        expected: str,
        mode: str,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                "show",
                text,
                mode,
            ],
        )

        assert result.exit_code == 1

        assert result.stdout == ""

        assert result.output == f"{expected}\n"

        assert services_called == []

    def test_an_identifier_written_without_hyphens_is_still_read_as_one(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis_id = AnalysisId.new()

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                analysis_id.value.hex,
            ],
        )

        assert result.exit_code == 1

        assert (
            result.output
            == f"error: no analysis {analysis_id} in the local store; --remote looks for it in the distributed mode\n"
        )

    def test_an_identifier_error_beyond_the_argument_keeps_its_own_words(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        async def show(
            *,
            analysis_id: AnalysisId,
            settings: Settings,
            local: bool,
            transport: AsyncBaseTransport | None = None,
        ) -> WatchedAnalysis:
            raise InvalidIdentifierError(
                "AnalysisId requires a UUID string, got 'foo'",
            )

        monkeypatch.setattr(
            target=commands,
            name="show",
            value=show,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=AnalysisId.new(),
                ),
            ],
        )

        assert result.exit_code == 1

        assert result.output == "error: AnalysisId requires a UUID string, got 'foo'\n"


@final
class TestDoctorInstallingTheNltkData:
    @mark.parametrize(
        argnames="args",
        argvalues=(
            [
                "setup",
            ],
            [
                "doctor",
                "--install-nltk-data",
            ],
        ),
        ids=(
            "setup",
            "doctor",
        ),
    )
    def test_a_package_that_cannot_be_fetched_stops_doctor_with_the_line_setup_prints(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
        args: list[str],
    ) -> None:
        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return REQUIRED_RESOURCES[:1]

        def download_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
            target: Path | None = None,
            client: object = None,
        ) -> tuple[NltkResource, ...]:
            raise LinguisticResourcesMissingError(
                "NLTK package foo could not be fetched from https://example.com/foo.zip (ConnectError)",
            )

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="download_resources",
            value=download_resources,
        )

        result = _runner.invoke(
            app=app,
            args=args,
        )

        assert result.exit_code == 1

        assert result.stdout == ""

        assert result.output.splitlines() == [
            "error: NLTK package foo could not be fetched from https://example.com/foo.zip (ConnectError)",
        ]

    def test_a_package_fetched_is_named_before_the_report(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        absent = list(
            REQUIRED_RESOURCES[:1],
        )

        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return tuple(resource for resource in resources if resource in absent)

        def download_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
            target: Path | None = None,
            client: object = None,
        ) -> tuple[NltkResource, ...]:
            fetched = missing_resources(
                resources=resources,
            )

            for resource in fetched:
                absent.remove(
                    resource,
                )

            return fetched

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="download_resources",
            value=download_resources,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
                "--install-nltk-data",
            ],
        )

        assert result.exit_code == 0, result.output

        assert result.stdout.splitlines()[1] == "downloaded NLTK package punkt_tab"

    def test_without_the_flag_a_missing_package_is_reported_and_nothing_is_fetched(
        self,
        *,
        workspace: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        def missing_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
        ) -> tuple[NltkResource, ...]:
            return REQUIRED_RESOURCES[:1]

        def download_resources(
            *,
            resources: Sequence[NltkResource] = REQUIRED_RESOURCES,
            target: Path | None = None,
            client: object = None,
        ) -> tuple[NltkResource, ...]:
            raise AssertionError(
                "without the flag, nothing is downloaded",
            )

        monkeypatch.setattr(
            target=commands,
            name="missing_resources",
            value=missing_resources,
        )

        monkeypatch.setattr(
            target=commands,
            name="download_resources",
            value=download_resources,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "doctor",
            ],
        )

        assert result.exit_code == 1

        assert "MISSING NLTK package punkt_tab" in result.stdout

        assert "run `wordwinnow setup` to install the missing packages" in result.stdout

        assert "error:" not in result.output


type _Meaning = tuple[DictionaryInformation | None, tuple[LexicalSense, ...]]


def _wordnet_sense() -> LexicalSense:
    return LexicalSense(
        key="foo.n.01",
        part_of_speech=PartOfSpeech.NOUN,
        gloss="a sense of the word",
        example=None,
        synonyms=(),
        hypernyms=(),
        antonyms=(),
        usage_count=1,
        hyponym_count=0,
        category="noun.attribute",
    )


def _unavailable() -> DictionaryInformation:
    return DictionaryInformation(
        outcome=LookupOutcome.UNAVAILABLE,
        failure_reason="circuit_open",
        phonetic=None,
        definitions=(),
    )


# NOTE:
# The ways a word stands in the analyses these tests use, each a dictionary's information and the WordNet senses: with
# a WordNet sense and no dictionary asked, the usual case; with neither, the dictionary not asked; with neither, the
# dictionary unavailable; with neither, the dictionary having no entry; with a dictionary definition and no sense; and
# with a WordNet sense standing in for an unavailable dictionary, which is a meaning.
def _with_sense() -> _Meaning:
    return (
        None,
        (_wordnet_sense(),),
    )


def _with_no_meaning_asked_of_no_dictionary() -> _Meaning:
    return (
        None,
        (),
    )


def _with_no_meaning_the_dictionary_unavailable() -> _Meaning:
    return (
        _unavailable(),
        (),
    )


def _with_a_stand_in_sense() -> _Meaning:
    return (
        _unavailable(),
        (_wordnet_sense(),),
    )


def _with_no_meaning_the_dictionary_has_no_entry() -> _Meaning:
    return (
        DictionaryInformation(
            outcome=LookupOutcome.NOT_FOUND,
            failure_reason=None,
            phonetic=None,
            definitions=(),
        ),
        (),
    )


def _with_a_definition_and_no_sense() -> _Meaning:
    return (
        DictionaryInformation(
            outcome=LookupOutcome.FOUND,
            failure_reason=None,
            phonetic=None,
            definitions=(
                SelectedDefinition(
                    text="a definition of the word",
                    example=None,
                    part_of_speech_matched=True,
                ),
            ),
        ),
        (),
    )


def _tiered(
    *,
    tiers: Sequence[StudyTier] = (
        StudyTier.FOCUS,
        StudyTier.STRETCH,
        StudyTier.REVIEW,
        StudyTier.KNOWN,
    ),
    meanings: Sequence[_Meaning] = (),
) -> Analysis:
    """
    A completed analysis with one item for each entry of `tiers`, in that
    order, so that a tier given twice has two items.

    By default `tiers` holds each tier once, so the analysis has four items,
    of which two are words to study.

    Each item has a WordNet sense, unless `meanings` gives the dictionary
    information and the senses of the item at its place, as many as it has
    entries.

    More than ten entries raise an error, since ten lemmas are all it has.
    """

    unstated = tiers[
        len(
            meanings,
        ) :
    ]

    padded = (
        *meanings,
        *(_with_sense() for _ in unstated),
    )

    analysis = request_an_analysis(
        document=Document(
            title="foo",
            text="foo bar baz qux",
            origin=DocumentOrigin.CUSTOM_TEXT,
            reference="foo.txt",
        ),
        profile=LearnerProfile(
            level=CefrLevel.B1,
            target_level=CefrLevel.B2,
        ),
        options=AnalysisOptions(
            include_dictionary=False,
        ),
        at=_EPOCH,
    )

    analysis.start(
        at=_EPOCH,
    )

    analysis.complete(
        at=_EPOCH,
        items=tuple(
            StudyItem(
                item=VocabularyItem(
                    lemma=lemma,
                    part_of_speech=PartOfSpeech.NOUN,
                    occurrence_count=1,
                    example_sentence="foo bar baz qux",
                ),
                zipf_frequency=4.0,
                level=LevelAssessment(
                    level=CefrLevel.B2,
                    source=LevelSource.REFERENCE_LIST,
                ),
                tier=tier,
                dictionary=dictionary,
                senses=senses,
            )
            for (
                lemma,
                tier,
                (
                    dictionary,
                    senses,
                ),
            ) in zip(
                (
                    "foo",
                    "bar",
                    "baz",
                    "qux",
                    "quux",
                    "corge",
                    "grault",
                    "garply",
                    "waldo",
                    "fred",
                )[
                    : len(
                        tiers,
                    )
                ],
                tiers,
                padded,
                strict=True,
            )
        ),
        stage_timings=(
            StageTiming(
                stage=Stage.WINNOWING,
                seconds=0.5,
            ),
        ),
    )

    return analysis


@final
class TestTheListingAgreesWithTheReport:
    def test_list_counts_the_words_the_report_counts_to_study(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered()

        _stored(
            analysis,
        )

        listed = _runner.invoke(
            app=app,
            args=[
                "list",
            ],
        )

        assert listed.exit_code == 0, listed.output

        lines = _plain(
            listed.output,
        ).splitlines()

        assert "To study" in lines[0]

        (row,) = (
            line
            for line in lines
            if line.startswith(
                str(
                    object=analysis.id,
                ),
            )
        )

        assert row.split()[-1] == "2"

        shown = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
            ],
        )

        assert shown.exit_code == 0, shown.output

        assert "2 words to study: 1 FOCUS, 1 STRETCH; 1 REVIEW, below B1" in _words(
            shown.output,
        )


def _flashcard_fronts(
    text: str,
    /,
    *,
    skipped: int,
) -> tuple[str, ...]:
    return tuple(
        line.split(
            sep="\t",
        )[0]
        for line in text.splitlines()[skipped:]
    )


def _csv_fronts(
    text: str,
    /,
    *,
    only: frozenset[str] | None = None,
) -> tuple[str, ...]:
    return tuple(
        f"{row[1]} ({row[2]})"
        for row in tuple(
            reader(
                StringIO(
                    initial_value=text,
                ),
            ),
        )[1:]
        if only is None or row[0] in only
    )


# NOTE:
# Two items to study in FOCUS and one in STRETCH, then three to look up in REVIEW and one KNOWN, in the order a report
# keeps.
_MIXED: Final = (
    StudyTier.FOCUS,
    StudyTier.FOCUS,
    StudyTier.STRETCH,
    StudyTier.REVIEW,
    StudyTier.REVIEW,
    StudyTier.REVIEW,
    StudyTier.KNOWN,
)


# NOTE:
# Eight words to study and two to look up, as `_gapped_meanings` gives each its meaning, in the order of these tiers.
_GAPPED_TIERS: Final = (
    StudyTier.FOCUS,
    StudyTier.FOCUS,
    StudyTier.FOCUS,
    StudyTier.STRETCH,
    StudyTier.STRETCH,
    StudyTier.STRETCH,
    StudyTier.STRETCH,
    StudyTier.STRETCH,
    StudyTier.REVIEW,
    StudyTier.REVIEW,
)


# NOTE:
# Of the eight words to study, five have a meaning: three a WordNet sense, one a dictionary definition and no sense,
# and one a sense standing in for an unavailable dictionary; and three have none: the dictionary not asked, the
# dictionary unavailable, and the dictionary having no entry.
#
# So three are left out, of which one had the dictionary unavailable.
#
# The two to look up come after, the first with no meaning.
def _gapped_meanings() -> tuple[_Meaning, ...]:
    return (
        _with_sense(),
        _with_no_meaning_asked_of_no_dictionary(),
        _with_sense(),
        _with_no_meaning_the_dictionary_unavailable(),
        _with_a_stand_in_sense(),
        _with_sense(),
        _with_a_definition_and_no_sense(),
        _with_no_meaning_the_dictionary_has_no_entry(),
        _with_no_meaning_asked_of_no_dictionary(),
        _with_sense(),
    )


def _written_with_gaps(
    workspace: Path,
    options: tuple[str, ...],
    /,
    *,
    fronts: tuple[str, ...],
) -> str:
    """
    What `show` says after writing both flashcard files of the analysis with
    gaps with `options`, having checked that each holds the cards of `fronts`.
    """

    analysis = _tiered(
        tiers=_GAPPED_TIERS,
        meanings=_gapped_meanings(),
    )

    _stored(
        analysis,
    )

    result = _runner.invoke(
        app=app,
        args=[
            "show",
            str(
                object=analysis.id,
            ),
            *options,
            "--anki",
            "a.txt",
            "--quizlet",
            "q.txt",
        ],
    )

    assert result.exit_code == 0, result.output

    assert (
        _flashcard_fronts(
            (workspace / "a.txt").read_text(
                encoding="utf-8",
            ),
            skipped=5,
        )
        == fronts
    )

    assert (
        _flashcard_fronts(
            (workspace / "q.txt").read_text(
                encoding="utf-8",
            ),
            skipped=0,
        )
        == fronts
    )

    return _words(
        result.output,
    )


@final
class TestTheFlashcardFiles:
    @mark.parametrize(
        argnames="command",
        argvalues=(
            "analyze",
            "show",
        ),
    )
    def test_the_help_lists_both_options_in_the_showing_panel(
        self,
        *,
        command: str,
    ) -> None:
        result = _runner.invoke(
            app=app,
            args=[
                command,
                "--help",
            ],
        )

        assert result.exit_code == 0, result.output

        panel = _plain(
            result.output,
        ).split(
            "What it shows",
            maxsplit=1,
        )[1]

        assert "--anki" in panel

        assert "--quizlet" in panel

        assert "--csv" in panel

    def test_analyze_writes_all_three_files_and_says_so_once_each(
        self,
        *,
        workspace: Path,
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
                "A1",
                "--rows",
                "3",
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
                "--csv",
                "c.csv",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert search(
            pattern=r"wrote 3 of \d+ rows to c\.csv; --rows raises the limit",
            string=output,
        )

        assert search(
            pattern=r"wrote 3 of \d+ words to study to a\.txt; --rows raises the limit",
            string=output,
        )

        assert search(
            pattern=r"wrote 3 of \d+ words to study to q\.txt; --rows raises the limit",
            string=output,
        )

        anki = (workspace / "a.txt").read_bytes()

        quizlet = (workspace / "q.txt").read_bytes()

        for written in (
            anki,
            quizlet,
        ):
            assert not written.startswith(
                b"\xef\xbb\xbf",
            )

            assert b"\r" not in written

            assert written.endswith(
                b"\n",
            )

        csv_fronts = _csv_fronts(
            (workspace / "c.csv").read_text(
                encoding="utf-8",
            ),
        )

        assert (
            len(
                csv_fronts,
            )
            == 3
        )

        assert (
            _flashcard_fronts(
                anki.decode(
                    encoding="utf-8",
                ),
                skipped=5,
            )
            == csv_fronts
        )

        assert (
            _flashcard_fronts(
                quizlet.decode(
                    encoding="utf-8",
                ),
                skipped=0,
            )
            == csv_fronts
        )

    @mark.parametrize(
        argnames=(
            "selection",
            "tiers",
        ),
        argvalues=(
            (
                (),
                frozenset(
                    (
                        "focus",
                        "stretch",
                    ),
                ),
            ),
            (
                (
                    "--rows",
                    "1000",
                ),
                frozenset(
                    (
                        "focus",
                        "stretch",
                    ),
                ),
            ),
            (
                (
                    "--tier",
                    "focus",
                ),
                None,
            ),
            (
                (
                    "--tier",
                    "stretch",
                    "--rows",
                    "4",
                ),
                None,
            ),
            (
                (
                    "--tier",
                    "focus",
                    "--tier",
                    "stretch",
                    "--known",
                    "known.txt",
                ),
                None,
            ),
            (
                (
                    "--tier",
                    "review",
                    "--rows",
                    "1000",
                ),
                None,
            ),
        ),
        ids=(
            "default",
            "all-rows",
            "focus",
            "stretch-four",
            "known",
            "review-all",
        ),
    )
    def test_the_selection_is_the_csv_s_in_the_same_order_with_tier_and_its_study_words_without(
        self,
        *,
        workspace: Path,
        selection: tuple[str, ...],
        tiers: frozenset[str] | None,
    ) -> None:
        (workspace / "a-scandal-in-bohemia.txt").write_text(
            data=_TEXT,
            encoding="utf-8",
        )

        (workspace / "known.txt").write_text(
            data="photograph\ndrunken\n",
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
                "A2",
                "--target",
                "B1",
                *selection,
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
                "--csv",
                "c.csv",
            ],
        )

        assert result.exit_code == 0, result.output

        # NOTE:
        # The CSV takes every tier, so its rows are narrowed to the study list where no `--tier` chose the cards.
        csv_fronts = _csv_fronts(
            (workspace / "c.csv").read_text(
                encoding="utf-8",
            ),
            only=tiers,
        )

        assert csv_fronts

        assert (
            _flashcard_fronts(
                (workspace / "a.txt").read_text(
                    encoding="utf-8",
                ),
                skipped=5,
            )
            == csv_fronts
        )

        assert (
            _flashcard_fronts(
                (workspace / "q.txt").read_text(
                    encoding="utf-8",
                ),
                skipped=0,
            )
            == csv_fronts
        )

    @mark.parametrize(
        argnames=(
            "rows",
            "line",
        ),
        argvalues=(
            (
                (),
                "wrote 3 words to study to {name}",
            ),
            (
                (
                    "--rows",
                    "2",
                ),
                "wrote 2 of 3 words to study to {name}; --rows raises the limit",
            ),
            (
                (
                    "--rows",
                    "100",
                ),
                "wrote 3 words to study to {name}",
            ),
        ),
        ids=(
            "default",
            "rows-below-the-study-list",
            "rows-above-the-study-list",
        ),
    )
    def test_without_tier_the_files_take_the_words_to_study_alone_up_to_rows(
        self,
        *,
        workspace: Path,
        rows: tuple[str, ...],
        line: str,
    ) -> None:
        analysis = _tiered(
            tiers=_MIXED,
        )

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                *rows,
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        for name in (
            "a.txt",
            "q.txt",
        ):
            assert (
                line.format_map(
                    {
                        "name": name,
                    },
                )
                in output
            )

        fronts = (
            "foo (noun)",
            "bar (noun)",
            "baz (noun)",
        )[: 2 if rows == ("--rows", "2") else 3]

        assert (
            _flashcard_fronts(
                (workspace / "a.txt").read_text(
                    encoding="utf-8",
                ),
                skipped=5,
            )
            == fronts
        )

        assert (
            _flashcard_fronts(
                (workspace / "q.txt").read_text(
                    encoding="utf-8",
                ),
                skipped=0,
            )
            == fronts
        )

    def test_tier_is_obeyed_and_the_line_keeps_its_form(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered(
            tiers=_MIXED,
        )

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--tier",
                "review",
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert "wrote 3 flashcards to a.txt" in output

        assert "wrote 3 flashcards to q.txt" in output

        assert "words to study" not in output

        fronts = (
            "qux (noun)",
            "quux (noun)",
            "corge (noun)",
        )

        assert (
            _flashcard_fronts(
                (workspace / "a.txt").read_text(
                    encoding="utf-8",
                ),
                skipped=5,
            )
            == fronts
        )

        assert (
            _flashcard_fronts(
                (workspace / "q.txt").read_text(
                    encoding="utf-8",
                ),
                skipped=0,
            )
            == fronts
        )

    def test_an_analysis_with_no_word_to_study_writes_an_empty_selection(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered(
            tiers=(
                StudyTier.REVIEW,
                StudyTier.REVIEW,
                StudyTier.KNOWN,
            ),
        )

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert "wrote 0 words to study to a.txt" in output

        assert "wrote 0 words to study to q.txt" in output

        assert (
            len(
                (workspace / "a.txt")
                .read_text(
                    encoding="utf-8",
                )
                .splitlines(),
            )
            == 5
        )

        assert (workspace / "q.txt").read_bytes() == b""

    def test_the_csv_beside_a_flashcard_file_is_the_csv_alone(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered(
            tiers=_MIXED,
        )

        _stored(
            analysis,
        )

        lines: list[str] = []

        for (
            name,
            cards,
        ) in (
            (
                "alone.csv",
                (),
            ),
            (
                "beside.csv",
                (
                    "--anki",
                    "a.txt",
                ),
            ),
        ):
            result = _runner.invoke(
                app=app,
                args=[
                    "show",
                    str(
                        object=analysis.id,
                    ),
                    "--rows",
                    "5",
                    *cards,
                    "--csv",
                    name,
                ],
            )

            assert result.exit_code == 0, result.output

            lines.append(
                next(
                    line
                    for line in _words(
                        result.output,
                    ).split(
                        sep="wrote ",
                    )
                    if f"to {name}" in line
                ).strip(),
            )

        assert (
            lines[0].replace(
                "alone",
                "beside",
            )
            == lines[1]
        )

        assert lines[0].startswith(
            "5 of 7 rows to alone.csv; --rows raises the limit",
        )

        assert (workspace / "alone.csv").read_bytes() == (workspace / "beside.csv").read_bytes()

        assert _csv_fronts(
            (workspace / "beside.csv").read_text(
                encoding="utf-8",
            ),
        ) == (
            "foo (noun)",
            "bar (noun)",
            "baz (noun)",
            "qux (noun)",
            "quux (noun)",
        )

    def test_the_files_leave_out_the_words_with_no_meaning_and_say_how_many(
        self,
        *,
        workspace: Path,
    ) -> None:
        output = _written_with_gaps(
            workspace,
            (),
            fronts=(
                "foo (noun)",
                "baz (noun)",
                "quux (noun)",
                "corge (noun)",
                "grault (noun)",
            ),
        )

        assert "wrote 5 words to study to a.txt, leaving out 3 with no meaning" in output

        assert "wrote 5 words to study to q.txt, leaving out 3 with no meaning" in output

        assert "--rows raises the limit" not in output

    def test_rows_fills_from_the_next_word_with_a_meaning(
        self,
        *,
        workspace: Path,
    ) -> None:
        output = _written_with_gaps(
            workspace,
            (
                "--rows",
                "2",
            ),
            fronts=(
                "foo (noun)",
                "baz (noun)",
            ),
        )

        for name in (
            "a.txt",
            "q.txt",
        ):
            assert (
                f"wrote 2 of 5 words to study to {name}, leaving out 3 with no meaning; --rows raises the limit"
                in output
            )

    def test_tier_leaves_out_the_words_with_no_meaning_too_and_the_line_says_flashcards(
        self,
        *,
        workspace: Path,
    ) -> None:
        output = _written_with_gaps(
            workspace,
            (
                "--tier",
                "focus",
            ),
            fronts=(
                "foo (noun)",
                "baz (noun)",
            ),
        )

        assert "wrote 2 flashcards to a.txt, leaving out 1 with no meaning" in output

        assert "wrote 2 flashcards to q.txt, leaving out 1 with no meaning" in output

        assert "words to study" not in output

    def test_when_no_word_has_a_meaning_the_files_are_empty_and_the_line_counts_them_all(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered(
            tiers=_MIXED,
            meanings=(_with_no_meaning_asked_of_no_dictionary(),)
            * len(
                _MIXED,
            ),
        )

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert "wrote 0 words to study to a.txt, leaving out 3 with no meaning" in output

        assert "wrote 0 words to study to q.txt, leaving out 3 with no meaning" in output

        assert (
            len(
                (workspace / "a.txt")
                .read_text(
                    encoding="utf-8",
                )
                .splitlines(),
            )
            == 5
        )

        assert (workspace / "q.txt").read_bytes() == b""

    def test_one_note_says_how_many_words_left_out_had_the_dictionary_unavailable(
        self,
        *,
        workspace: Path,
    ) -> None:
        output = _written_with_gaps(
            workspace,
            (),
            fronts=(
                "foo (noun)",
                "baz (noun)",
                "quux (noun)",
                "corge (noun)",
                "grault (noun)",
            ),
        )

        # NOTE:
        # Of the three words left out, the one whose dictionary was unavailable counts, and the one it was not asked
        # of and the one it had no entry for do not.
        note = "the dictionary was unavailable for 1 of the words left out; analyzing again may find their meanings"

        noted = output.count(
            note,
        )

        after = output.index(
            "to q.txt",
        )

        assert noted == 1

        assert after < output.index(
            note,
        )

    def test_no_note_without_an_unavailable_dictionary_or_without_a_flashcard_file(
        self,
        *,
        workspace: Path,
    ) -> None:
        asked_of_none = _tiered(
            tiers=_MIXED,
            meanings=(_with_no_meaning_asked_of_no_dictionary(),)
            * len(
                _MIXED,
            ),
        )

        gapped = _tiered(
            tiers=_GAPPED_TIERS,
            meanings=_gapped_meanings(),
        )

        _stored(
            asked_of_none,
        )

        _stored(
            gapped,
        )

        for (
            analysis,
            option,
            name,
            left_out,
        ) in (
            (
                asked_of_none,
                "--anki",
                "a.txt",
                "leaving out 3 with no meaning",
            ),
            (
                gapped,
                "--csv",
                "c.csv",
                "",
            ),
        ):
            result = _runner.invoke(
                app=app,
                args=[
                    "show",
                    str(
                        object=analysis.id,
                    ),
                    option,
                    name,
                ],
            )

            assert result.exit_code == 0, result.output

            output = _words(
                result.output,
            )

            assert left_out in output

            assert "the dictionary was unavailable" not in output

    def test_the_csv_beside_the_files_still_holds_the_words_with_no_meaning(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered(
            tiers=_GAPPED_TIERS,
            meanings=_gapped_meanings(),
        )

        _stored(
            analysis,
        )

        lines: list[str] = []

        for (
            name,
            cards,
        ) in (
            (
                "alone.csv",
                (),
            ),
            (
                "beside.csv",
                (
                    "--anki",
                    "a.txt",
                    "--quizlet",
                    "q.txt",
                ),
            ),
        ):
            result = _runner.invoke(
                app=app,
                args=[
                    "show",
                    str(
                        object=analysis.id,
                    ),
                    "--rows",
                    "5",
                    *cards,
                    "--csv",
                    name,
                ],
            )

            assert result.exit_code == 0, result.output

            lines.append(
                next(
                    line
                    for line in _words(
                        result.output,
                    ).split(
                        sep="wrote ",
                    )
                    if f"to {name}" in line
                ).strip(),
            )

        assert (workspace / "alone.csv").read_bytes() == (workspace / "beside.csv").read_bytes()

        assert lines == [
            "5 of 10 rows to alone.csv; --rows raises the limit",
            "5 of 10 rows to beside.csv; --rows raises the limit",
        ]

        assert _csv_fronts(
            (workspace / "beside.csv").read_text(
                encoding="utf-8",
            ),
        ) == (
            "foo (noun)",
            "bar (noun)",
            "baz (noun)",
            "qux (noun)",
            "quux (noun)",
        )

        assert _flashcard_fronts(
            (workspace / "q.txt").read_text(
                encoding="utf-8",
            ),
            skipped=0,
        ) == (
            "foo (noun)",
            "baz (noun)",
            "quux (noun)",
            "corge (noun)",
            "grault (noun)",
        )

    def test_show_writes_each_file_and_says_how_many_words_it_left_out(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered()

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--rows",
                "1",
                "--detail",
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert "wrote 1 of 2 words to study to a.txt; --rows raises the limit" in output

        assert "wrote 1 of 2 words to study to q.txt; --rows raises the limit" in output

        assert "shows this again" not in output

        assert (
            len(
                (workspace / "a.txt")
                .read_text(
                    encoding="utf-8",
                )
                .splitlines(),
            )
            == 6
        )

        assert (
            len(
                (workspace / "q.txt")
                .read_text(
                    encoding="utf-8",
                )
                .splitlines(),
            )
            == 1
        )

        whole = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--quizlet",
                "q.txt",
            ],
        )

        assert whole.exit_code == 0, whole.output

        assert "wrote 2 words to study to q.txt" in _words(
            whole.output,
        )

    def test_detail_reaches_both_flashcard_files_and_its_absence_leaves_them_standard(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _completed(
            meaning="a first sense",
            other_meaning="a second sense",
        )

        _stored(
            analysis,
        )

        for (
            option,
            fuller,
            standard,
        ) in (
            (
                "--anki",
                "a-detail.txt",
                "a.txt",
            ),
            (
                "--quizlet",
                "q-detail.txt",
                "q.txt",
            ),
        ):
            for (
                name,
                detail,
            ) in (
                (
                    fuller,
                    ("--detail",),
                ),
                (
                    standard,
                    (),
                ),
            ):
                result = _runner.invoke(
                    app=app,
                    args=[
                        "show",
                        str(
                            object=analysis.id,
                        ),
                        *detail,
                        option,
                        name,
                    ],
                )

                assert result.exit_code == 0, result.output

            assert "Other meaning: a second sense" in (workspace / fuller).read_text(
                encoding="utf-8",
            )

            assert "Other meaning:" not in (workspace / standard).read_text(
                encoding="utf-8",
            )

    @mark.parametrize(
        argnames="option",
        argvalues=(
            "--anki",
            "--quizlet",
        ),
    )
    def test_a_flashcard_file_of_an_unfinished_analysis_is_followed_by_the_note_that_says_why_it_is_empty(
        self,
        *,
        workspace: Path,
        option: str,
    ) -> None:
        analysis = _processing()

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                option,
                "cards.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        output = _words(
            result.output,
        )

        assert "wrote 0 words to study to cards.txt" in output

        assert output.endswith(
            "a local analysis goes on only while the command that runs it is running; if that command has stopped, "
            "run `wordwinnow analyze` on the text again",
        )

        assert output.index(
            "wrote 0 words to study",
        ) < output.index(
            "a local analysis goes on",
        )

    def test_with_json_the_json_is_printed_and_no_flashcard_file_is_written(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _completed()

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--json",
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
            ],
        )

        assert result.exit_code == 0, result.output

        assert (
            loads(
                s=result.stdout,
            )["status"]
            == "completed"
        )

        assert not (workspace / "a.txt").exists()

        assert not (workspace / "q.txt").exists()

    def test_a_text_that_may_not_be_kept_has_no_sentence_and_the_note_says_so_once(
        self,
        *,
        workspace: Path,
    ) -> None:
        # NOTE:
        # The store deletes a text kept for a while once that while has passed since it was requested, so it is
        # requested now.
        analysis = _completed(
            retention=timedelta(
                hours=24,
            ),
            at=datetime.now(
                tz=UTC,
            ),
        )

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--anki",
                "a.txt",
                "--quizlet",
                "q.txt",
                "--csv",
                "c.csv",
            ],
        )

        assert result.exit_code == 0, result.output

        assert (
            _words(
                result.output,
            ).count(
                "the sentences were left out: this text may not be kept",
            )
            == 1
        )

        for name in (
            "a.txt",
            "q.txt",
        ):
            assert "In the text" not in (workspace / name).read_text(
                encoding="utf-8",
            )

    def test_the_csv_alone_is_written_as_it_was_and_prints_what_it_printed(
        self,
        *,
        workspace: Path,
    ) -> None:
        analysis = _tiered()

        _stored(
            analysis,
        )

        result = _runner.invoke(
            app=app,
            args=[
                "show",
                str(
                    object=analysis.id,
                ),
                "--csv",
                "c.csv",
            ],
        )

        assert result.exit_code == 0, result.output

        assert (
            _words(
                result.output,
            )
            == "wrote 4 rows to c.csv"
        )

        assert (
            (workspace / "c.csv")
            .read_text(
                encoding="utf-8",
            )
            .startswith(
                "tier,lemma,part_of_speech,",
            )
        )

        assert not (workspace / "a.txt").exists()
