"""
The `wordwinnow` command.

`analyze`, `show`, `list`, and `stats` are the learner's commands; `admin`,
`db`, `serve`, `work`, `ml`, `bench`, and `doctor` are the operator's.

Every command reads the settings once, configures the process, and calls into
a module that a test can call without a terminal.
"""

from asyncio import (
    run,
)
from collections.abc import (
    Sequence,
)
from enum import (
    auto,
)
from pathlib import (
    Path,
)
from sys import (
    stderr,
    stdin,
)
from types import (
    MappingProxyType,
)
from typing import (
    Annotated,
    Final,
)

from pydantic import (
    ValidationError,
)
from rich.console import (
    Console,
)
from typer import (
    Argument,
    BadParameter,
    Exit,
    Option,
    Typer,
)

from wordwinnow.application.errors import (
    AnalysisNotFoundError,
    AnalysisNotPublishedError,
    FactStoreUnavailableError,
    LinguisticResourcesMissingError,
    MessagingUnavailableError,
    SourceNotConfiguredError,
    SourceRejectedError,
    SourceUnavailableError,
)
from wordwinnow.domain.analysis import (
    Analysis,
    AnalysisStatus,
)
from wordwinnow.domain.cefr import (
    CefrLevel,
)
from wordwinnow.domain.document import (
    InvalidDocumentError,
)
from wordwinnow.domain.enums import (
    UnorderedStrEnum,
)
from wordwinnow.domain.identifiers import (
    AnalysisId,
    InvalidIdentifierError,
)
from wordwinnow.domain.learner import (
    InvalidLearnerProfileError,
)
from wordwinnow.domain.study import (
    StudyTier,
)
from wordwinnow.infrastructure.composition import (
    configure_process,
)
from wordwinnow.infrastructure.settings import (
    Settings,
    load_settings,
)
from wordwinnow.infrastructure.sources.custom_text import (
    read_custom_text,
)
from wordwinnow.services.bench import (
    runner,
)
from wordwinnow.services.cli import (
    commands,
)
from wordwinnow.services.cli.intake_client import (
    IntakeRejectedError,
    IntakeUnavailableError,
)
from wordwinnow.services.cli.operator import (
    OperatorProgress,
)
from wordwinnow.services.cli.progress import (
    TerminalProgress,
)
from wordwinnow.services.cli.report import (
    DEFAULT_ROW_LIMIT,
    render_cards,
    render_items,
    render_listing,
    render_summary,
    render_vocabulary_summary,
    select_items,
    to_csv,
    to_json,
)
from wordwinnow.services.intake.schemas import (
    AnalysisRequest,
)
from wordwinnow.services.workers import (
    run_aggregation_worker,
    run_analysis_worker,
)

app: Final = Typer(
    no_args_is_help=True,
    help="Winnow a text into the vocabulary worth a learner's attention.",
    add_completion=False,
)

admin_app: Final = Typer(
    no_args_is_help=True,
    help="Operator commands against the running services.",
)

db_app: Final = Typer(
    no_args_is_help=True,
    help="The analysis store's schema.",
)

serve_app: Final = Typer(
    no_args_is_help=True,
    help="Run an HTTP service.",
)

work_app: Final = Typer(
    no_args_is_help=True,
    help="Run a worker.",
)

ml_app: Final = Typer(
    no_args_is_help=True,
    help="The level model: run the experiment, or train the shipped configuration.",
)

bench_app: Final = Typer(
    no_args_is_help=True,
    help="Performance experiments.",
)

for (
    name,
    sub_app,
) in (
    (
        "admin",
        admin_app,
    ),
    (
        "db",
        db_app,
    ),
    (
        "serve",
        serve_app,
    ),
    (
        "work",
        work_app,
    ),
    (
        "ml",
        ml_app,
    ),
    (
        "bench",
        bench_app,
    ),
):
    app.add_typer(
        typer_instance=sub_app,
        name=name,
    )

# WARN:
# An option's declaration such as `"--detail"` stays positional in every `Option(` below: the slot is spelled
# `default`, but inside `Annotated` Typer reads a string there as the first option declaration, so naming it
# `default=` would name the argument after something it is not.
_console: Final = Console()

_errors: Final = Console(
    stderr=True,
)

# NOTE:
# What each source takes as its topic, as `analyze --help` lists it; a test holds the names to the sources the
# composition root registers, so a source cannot be added without saying here what it asks for.
SOURCE_TOPICS: Final = MappingProxyType(
    mapping={
        "nyt-rss": "a feed as The New York Times names it: Science, World, HomePage",
        "nyt-top-stories": "a section: science, world, books/review",
        "nyt-most-popular": "viewed, emailed, or shared, with an optional period: viewed/30",
        "nyt-article-search": "a query; ten articles per request, newest first",
        "nyt-archive": "a month: 2026-08; one response of about twenty megabytes",
    },
)

_SOURCE_ROWS: Final = "\n".join(
    f"  {name:<20} {topic}"
    for (
        name,
        topic,
    ) in SOURCE_TOPICS.items()
)

# NOTE:
# Typer keeps the epilog's line breaks and indentation in its Rich help, and the epilog holds what a first-time user
# needs at the point of use and would otherwise look for in `docs/operating.md`: what each source takes, which sources
# need the key, and two commands that work as written.
_ANALYZE_EPILOG: Final = (
    "[bold]Sources[/bold] for --source, each with the --topic it takes:\n\n"
    f"{_SOURCE_ROWS}\n\n"
    "Every source but nyt-rss needs WORDWINNOW_NYT_API_KEY, and `wordwinnow doctor` lists the sources this setup "
    "enables.\n\n"
    "[bold]Examples[/bold]\n\n"
    "  wordwinnow analyze data/corpus/a-scandal-in-bohemia.txt --level A2 --local --no-dictionary\n"
    "  wordwinnow analyze --source nyt-rss --topic Science --level B1 --local"
)

# NOTE:
# The panels `analyze --help` groups its options into after the default one, which holds what to analyze and for whom.
_RUNNING: Final = "How it runs"

_SHOWING: Final = "What it shows"


class View(
    UnorderedStrEnum,
):
    """
    What standard error shows while `analyze` runs: the learner's line, or,
    for an operator, that line with what the dictionary is doing under it.
    """

    LEARNER = auto()

    OPERATOR = auto()


# NOTE:
# The errors a command turns into one line and exit code 1, because the learner or the operator can act on them.
_EXPECTED: Final = (
    SourceRejectedError,
    SourceNotConfiguredError,
    SourceUnavailableError,
    AnalysisNotFoundError,
    AnalysisNotPublishedError,
    MessagingUnavailableError,
    FactStoreUnavailableError,
    LinguisticResourcesMissingError,
    InvalidDocumentError,
    InvalidIdentifierError,
    InvalidLearnerProfileError,
    IntakeUnavailableError,
    IntakeRejectedError,
    commands.AnalysisTimedOutError,
)


def _settings() -> Settings:
    settings = load_settings()

    # NOTE:
    # A learner's terminal gets warnings and errors only; `WORDWINNOW_LOG_LEVEL=INFO` brings back the start-up events,
    # such as the sources configured, and `serve` and `work` raise their own process to INFO when they start.
    configure_process(
        settings=settings,
        service_name="wordwinnow-cli",
        tracing=False,
        default_log_level="WARNING",
    )

    return settings


def _fail(
    exception: Exception,
    /,
) -> Exit:
    _errors.print(
        f"[red]error:[/] {exception}",
    )

    return Exit(
        code=1,
    )


def _validation_message(
    exception: ValidationError,
    /,
) -> str:
    # NOTE:
    # A request the command line built from contradictory options is the learner's mistake, so it is reported in the
    # validator's own words, without the library's prefix, its input dump, and its link.
    return "; ".join(
        str(
            object=error["msg"],
        ).removeprefix(
            "Value error, ",
        )
        for error in exception.errors()
    )


def _tiers(
    tier: Sequence[StudyTier] | None,
    /,
) -> tuple[StudyTier, ...] | None:
    if not tier:
        return None

    return tuple(
        tier,
    )


def _render(
    analysis: Analysis,
    tiers: Sequence[StudyTier] | None,
    rows: int | None,
    detail: bool,
    as_json: bool,
    csv_path: Path | None,
    /,
) -> None:
    if as_json:
        # NOTE:
        # JSON is for another program, so it is written without the wrapping a terminal reader would get.
        _console.print(
            to_json(
                analysis=analysis,
            ),
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

        return

    items = select_items(
        items=analysis.items,
        tiers=tiers,
        limit=rows,
    )

    # NOTE:
    # What a report leaves out is counted, so a learner shown 40 words knows whether there are more.
    available = len(
        select_items(
            items=analysis.items,
            tiers=tiers,
            limit=None,
        ),
    )

    shown = len(
        items,
    )

    if csv_path is not None:
        csv_path.write_text(
            data=to_csv(
                items=items,
                with_sentences=not analysis.document.expires,
            ),
            encoding="utf-8",
        )

        _console.print(
            f"wrote {shown} of {available} rows to {csv_path}; --rows raises the limit"
            if shown < available
            else f"wrote {shown} rows to {csv_path}",
        )

        if analysis.document.expires:
            _console.print(
                "the sentences were left out: this text may not be kept",
                style="dim",
            )

        return

    render_summary(
        analysis=analysis,
        console=_console,
    )

    if not analysis.items:
        if analysis.status is AnalysisStatus.COMPLETED:
            _console.print(
                "no words to study: the text holds only function words, names, and numbers",
                style="dim",
            )

        return

    if detail:
        render_cards(
            items=items,
            console=_console,
        )

    else:
        render_items(
            items=items,
            console=_console,
        )

    if shown < available:
        _console.print(
            f"showing {shown} of {available} words; --rows raises the limit",
            style="dim",
        )


def _request(
    text_file: Path | None,
    source: str | None,
    topic: str | None,
    limit: int,
    level: CefrLevel,
    target: CefrLevel | None,
    known: Path | None,
    dictionary: bool,
    /,
) -> AnalysisRequest:
    if (source is None) != (topic is None):
        raise BadParameter(
            message="analyze requires --source and --topic together",
        )

    if (text_file is None) == (source is None):
        raise BadParameter(
            message="analyze requires exactly one of a text file or --source with --topic",
        )

    known_lemmas: list[str] = []

    if known is not None:
        try:
            known_lemmas = known.read_text(
                encoding="utf-8",
            ).splitlines()

        except UnicodeDecodeError as exception:
            raise BadParameter(
                message=f"{known} is not UTF-8 text",
                param_hint="--known",
            ) from exception

    if source is not None:
        return AnalysisRequest(
            source=source,
            topic=topic,
            limit=limit,
            level=level,
            target_level=target,
            known_lemmas=known_lemmas,
            include_dictionary=dictionary,
        )

    if text_file is not None and (
        str(
            object=text_file,
        )
        == "-"
    ):
        return AnalysisRequest(
            text=stdin.read(),
            title="standard input",
            reference="stdin",
            level=level,
            target_level=target,
            known_lemmas=known_lemmas,
            include_dictionary=dictionary,
        )

    document = read_custom_text(
        path=text_file if text_file is not None else Path(),
    )

    return AnalysisRequest(
        text=document.text,
        title=document.title,
        reference=document.reference,
        level=level,
        target_level=target,
        known_lemmas=known_lemmas,
        include_dictionary=dictionary,
    )


@app.command(
    epilog=_ANALYZE_EPILOG,
)
def analyze(
    level: Annotated[
        CefrLevel,
        Option(
            help="The level you say you have.",
        ),
    ],
    text_file: Annotated[
        Path | None,
        Argument(
            help="A text, Markdown, SRT, or WebVTT file; `-` reads standard input.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        Option(
            help="Fetch the text from a source instead of a file; the sources are listed below.",
        ),
    ] = None,
    topic: Annotated[
        str | None,
        Option(
            help="What to ask the source for, in the form listed below for each source.",
        ),
    ] = None,
    limit: Annotated[
        int,
        Option(
            min=1,
            max=100,
            help="How many items of the source to combine.",
        ),
    ] = 15,
    target: Annotated[
        CefrLevel | None,
        Option(
            help="The level you are working toward; one above your level when omitted.",
        ),
    ] = None,
    known: Annotated[
        Path | None,
        Option(
            help="A file of words you already know, one per line.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
    dictionary: Annotated[
        bool,
        Option(
            "--dictionary/--no-dictionary",
            help=(
                "Ask the dictionary provider for definitions, examples, and pronunciation; Wordwinnow asks it for "
                "about two words a second, so a long text takes minutes, which --no-dictionary skips."
            ),
            rich_help_panel=_RUNNING,
        ),
    ] = True,
    local: Annotated[
        bool,
        Option(
            "--local/--remote",
            help="Run in this process against SQLite, or send the text to the distributed mode's intake service.",
            rich_help_panel=_RUNNING,
        ),
    ] = False,
    wait: Annotated[
        bool,
        Option(
            "--wait/--no-wait",
            help="In remote mode, wait for the result and show it.",
            rich_help_panel=_RUNNING,
        ),
    ] = True,
    timeout: Annotated[
        float,
        Option(
            min=1,
            help="Seconds to wait for a remote result.",
            rich_help_panel=_RUNNING,
        ),
    ] = 900.0,
    tier: Annotated[
        list[StudyTier] | None,
        Option(
            help="Show only these tiers; repeat the option for several.",
            rich_help_panel=_SHOWING,
        ),
    ] = None,
    rows: Annotated[
        int | None,
        Option(
            min=1,
            help="How many words to show.",
            rich_help_panel=_SHOWING,
        ),
    ] = DEFAULT_ROW_LIMIT,
    detail: Annotated[
        bool,
        Option(
            "--detail",
            help="One card per word, with pronunciation, examples, and related words, instead of a table.",
            rich_help_panel=_SHOWING,
        ),
    ] = False,
    as_json: Annotated[
        bool,
        Option(
            "--json",
            help="Print the whole analysis as JSON.",
            rich_help_panel=_SHOWING,
        ),
    ] = False,
    csv_path: Annotated[
        Path | None,
        Option(
            "--csv",
            help="Write the selected words to a CSV file instead of the terminal.",
            rich_help_panel=_SHOWING,
        ),
    ] = None,
    view: Annotated[
        View,
        Option(
            help=(
                "What standard error shows while the analysis runs: the learner's line, or, for an operator, that "
                "line with what the dictionary is doing under it."
            ),
            rich_help_panel=_SHOWING,
        ),
    ] = View.LEARNER,
) -> None:
    """
    Analyze a text for a learner and show the vocabulary in study order.

    The text is a file, standard input, or one of the sources below; with
    --local this process analyzes it, and without it the running services of
    the distributed mode do.
    """

    settings = _settings()

    try:
        request = _request(
            text_file,
            source,
            topic,
            limit,
            level,
            target,
            known,
            dictionary,
        )

        # NOTE:
        # The operator's view draws the learner's line as well, and hears what the dictionary is doing besides.
        operator = (
            OperatorProgress(
                console=_errors,
            )
            if view is View.OPERATOR
            else None
        )

        progress = operator or TerminalProgress(
            console=_errors,
        )

        with progress:
            analysis = run(
                main=(
                    commands.analyze_locally(
                        request=request,
                        settings=settings,
                        progress=progress,
                        watch=operator,
                    )
                    if local
                    else commands.analyze_remotely(
                        request=request,
                        settings=settings,
                        wait=wait,
                        timeout_seconds=timeout,
                        progress=progress,
                        watch=operator,
                    )
                ),
            )

    except _EXPECTED as exception:
        raise _fail(
            exception,
        ) from exception

    except ValidationError as exception:
        raise BadParameter(
            message=_validation_message(
                exception,
            ),
        ) from exception

    except ValueError as exception:
        raise BadParameter(
            message=str(
                object=exception,
            ),
        ) from exception

    if not local and not wait:
        _console.print(
            f"requested analysis {analysis.id}; `wordwinnow show {analysis.id}` shows it when done",
        )

        return

    _render(
        analysis,
        _tiers(
            tier,
        ),
        rows,
        detail,
        as_json,
        csv_path,
    )

    # NOTE:
    # A failed analysis is shown with its reason and fails the command too, so a script can tell it from a result.
    if analysis.status is AnalysisStatus.FAILED:
        raise Exit(
            code=1,
        )


@app.command()
def show(
    analysis_id: Annotated[
        str,
        Argument(
            help="The analysis to show.",
        ),
    ],
    local: Annotated[
        bool,
        Option(
            "--local/--remote",
            help="Read the local SQLite store, or the intake service.",
        ),
    ] = False,
    tier: Annotated[
        list[StudyTier] | None,
        Option(
            help="Show only these tiers; repeat the option for several.",
            rich_help_panel=_SHOWING,
        ),
    ] = None,
    rows: Annotated[
        int | None,
        Option(
            help="How many words to show.",
            min=1,
            rich_help_panel=_SHOWING,
        ),
    ] = DEFAULT_ROW_LIMIT,
    detail: Annotated[
        bool,
        Option(
            "--detail",
            help="One card per word, with pronunciation, examples, and related words, instead of a table.",
            rich_help_panel=_SHOWING,
        ),
    ] = False,
    as_json: Annotated[
        bool,
        Option(
            "--json",
            help="Print the whole analysis as JSON.",
            rich_help_panel=_SHOWING,
        ),
    ] = False,
    csv_path: Annotated[
        Path | None,
        Option(
            "--csv",
            help="Write the selected words to a CSV file instead of the terminal.",
            rich_help_panel=_SHOWING,
        ),
    ] = None,
) -> None:
    """
    Show a stored analysis.
    """

    settings = _settings()

    try:
        analysis = run(
            main=commands.show(
                analysis_id=AnalysisId.parse(
                    text=analysis_id,
                ),
                settings=settings,
                local=local,
            ),
        )

    except _EXPECTED as exception:
        raise _fail(
            exception,
        ) from exception

    _render(
        analysis,
        _tiers(
            tier,
        ),
        rows,
        detail,
        as_json,
        csv_path,
    )


@app.command(
    name="list",
)
def list_command(
    local: Annotated[
        bool,
        Option(
            "--local/--remote",
            help="Read the local SQLite store, or the intake service.",
        ),
    ] = False,
    limit: Annotated[
        int,
        Option(
            help="How many analyses to list.",
            min=1,
            max=200,
        ),
    ] = 20,
) -> None:
    """
    List recent analyses, newest first.
    """

    settings = _settings()

    try:
        summaries = run(
            main=commands.list_recent(
                settings=settings,
                local=local,
                limit=limit,
            ),
        )

    except _EXPECTED as exception:
        raise _fail(
            exception,
        ) from exception

    render_listing(
        summaries=summaries,
        console=_console,
    )


@app.command()
def stats(
    focus_limit: Annotated[
        int,
        Option(
            help="How many focus words to list.",
            min=1,
            max=200,
        ),
    ] = 20,
) -> None:
    """
    Summarize every completed analysis.

    The summary gives the levels and the most common focus words, from the
    distributed mode's fact store, so the running services must be up.
    """

    settings = _settings()

    try:
        summary = run(
            main=commands.summarize(
                settings=settings,
                focus_limit=focus_limit,
            ),
        )

    except _EXPECTED as exception:
        raise _fail(
            exception,
        ) from exception

    render_vocabulary_summary(
        summary=summary,
        console=_console,
    )


@admin_app.command()
def requeue() -> None:
    """
    Hand every analysis that waited or processed too long back to the workers.
    """

    settings = _settings()

    try:
        requeued = run(
            main=commands.requeue(
                settings=settings,
            ),
        )

    except _EXPECTED as exception:
        raise _fail(
            exception,
        ) from exception

    count = len(
        requeued,
    )

    _console.print(
        f"requeued {count} analyses",
    )

    for analysis_id in requeued:
        _console.print(
            f"  {analysis_id}",
        )


@db_app.command()
def upgrade() -> None:
    """
    Bring the analysis store's schema up to date.
    """

    settings = _settings()

    commands.upgrade_database(
        settings=settings,
    )

    _console.print(
        "schema is up to date",
    )


@serve_app.command()
def intake() -> None:
    """
    Run the intake service on the configured port.
    """

    settings = _settings()

    run(
        main=commands.serve_intake(
            settings=settings,
        ),
    )


@serve_app.command()
def enrichment() -> None:
    """
    Run the enrichment service on the configured port.
    """

    settings = _settings()

    run(
        main=commands.serve_enrichment(
            settings=settings,
        ),
    )


@work_app.command()
def analysis() -> None:
    """
    Run an analysis worker.
    """

    settings = _settings()

    run(
        main=run_analysis_worker(
            settings=settings,
        ),
    )


@work_app.command()
def aggregation() -> None:
    """
    Run the aggregation worker.
    """

    settings = _settings()

    run(
        main=run_aggregation_worker(
            settings=settings,
        ),
    )


@ml_app.command()
def experiment(
    seed: Annotated[
        int,
        Option(
            help="The random seed of the split and the models.",
        ),
    ] = 42,
    save_model: Annotated[
        bool,
        Option(
            "--save-model",
            help="Save the chosen model to the configured model path.",
        ),
    ] = False,
    output_dir: Annotated[
        Path | None,
        Option(
            help="Where to write results.json and the figures; nothing is written when omitted.",
        ),
    ] = None,
) -> None:
    """
    Run the level experiment and print what it measured.

    It cross-validates nine candidate models against the reference lists,
    which takes a few minutes.
    """

    settings = _settings()

    try:
        with TerminalProgress(
            console=_errors,
        ) as progress:
            summary = commands.run_level_experiment(
                settings=settings,
                seed=seed,
                save_model=save_model,
                output_dir=output_dir,
                progress=progress,
            )

    except LinguisticResourcesMissingError as exception:
        raise _fail(
            exception,
        ) from exception

    _console.print(
        summary,
        markup=False,
        highlight=False,
    )


@ml_app.command()
def train(
    seed: Annotated[
        int,
        Option(
            help="The random seed of the model.",
        ),
    ] = 42,
    model_path: Annotated[
        Path | None,
        Option(
            help="Where to write the model; the configured model path when omitted.",
        ),
    ] = None,
) -> None:
    """
    Train the shipped model and write it where the pipeline loads it from.

    It fits the shipped configuration to every reference entry.
    """

    settings = _settings()

    try:
        with TerminalProgress(
            console=_errors,
        ) as progress:
            line = commands.train_level_model(
                settings=settings,
                seed=seed,
                model_path=model_path if model_path is not None else settings.level_model_path,
                progress=progress,
            )

    except LinguisticResourcesMissingError as exception:
        raise _fail(
            exception,
        ) from exception

    _console.print(
        line,
        markup=False,
        highlight=False,
    )


@bench_app.command()
def stages(
    text: Annotated[
        list[Path] | None,
        Option(
            help="Texts to time, each at a quarter, a half, and all of its length; the sample story when omitted.",
        ),
    ] = None,
    repetitions: Annotated[
        int,
        Option(
            help="Runs per text.",
            min=1,
        ),
    ] = 3,
    series: Annotated[
        str,
        Option(
            help="What is true of this run, such as `heuristic` or `model`.",
        ),
    ] = "heuristic",
) -> None:
    """
    Time every pipeline stage over the sample story, without the dictionary.
    """

    settings = _settings()

    # NOTE:
    # No progress line here: the runs take seconds, and the stages they time are CPU-bound, so a line redrawn from
    # another thread would compete with them for the interpreter and show in the very numbers being recorded.
    written = run(
        main=runner.run_stages(
            texts=(
                tuple(
                    text,
                )
                if text
                else (runner.SAMPLE_STORY,)
            ),
            settings=settings,
            repetitions=repetitions,
            series=series,
        ),
    )

    _console.print(
        f"wrote {written.rows} rows to {written.path}",
    )


@bench_app.command(
    name="enrichment",
)
def bench_enrichment(
    text: Annotated[
        Path,
        Option(
            help="The text to enrich.",
        ),
    ] = runner.SAMPLE_STORY,
    concurrency: Annotated[
        list[int] | None,
        Option(
            help="Concurrency levels to try; repeat the option for several.",
        ),
    ] = None,
    latency: Annotated[
        float,
        Option(
            help="The replay provider's latency per request, in seconds.",
            min=0,
        ),
    ] = 0.1,
    requests_per_second: Annotated[
        float | None,
        Option(
            help="A provider budget to enforce; unlimited when omitted.",
            min=0.001,
        ),
    ] = None,
    warm: Annotated[
        bool,
        Option(
            "--warm",
            help="Measure a second run over a warm cache instead of a cold one.",
        ),
    ] = False,
    repetitions: Annotated[
        int,
        Option(
            help="Runs per concurrency level.",
            min=1,
        ),
    ] = 2,
    label: Annotated[
        str | None,
        Option(
            help="How to name this series; derived from the options when omitted.",
        ),
    ] = None,
) -> None:
    """
    Measure the dictionary stage at several concurrency levels.

    It runs against the replay provider, and with the defaults it takes about
    ten minutes.
    """

    settings = _settings()

    # NOTE:
    # The line is redrawn once a second rather than ten times: the replay provider answers in this process, so every
    # redraw is time taken from the stage being timed.
    with TerminalProgress(
        console=_errors,
        refresh_per_second=1,
    ) as progress:
        written = run(
            main=runner.run_enrichment(
                text=text,
                settings=settings,
                concurrencies=(
                    tuple(
                        concurrency,
                    )
                    if concurrency
                    else runner.DEFAULT_CONCURRENCIES
                ),
                latency_seconds=latency,
                requests_per_second=requests_per_second,
                warm=warm,
                repetitions=repetitions,
                label=label,
                progress=progress,
            ),
        )

    _console.print(
        f"wrote {written.rows} rows to {written.path}",
    )


@bench_app.command()
def throughput(
    label: Annotated[
        str,
        Option(
            help="What was changed outside this process, such as `workers=2`.",
        ),
    ],
    text: Annotated[
        Path,
        Option(
            help="The text to submit.",
        ),
    ] = runner.SAMPLE_STORY,
    analyses: Annotated[
        int,
        Option(
            help="How many copies to submit at once.",
            min=1,
        ),
    ] = 8,
    dictionary: Annotated[
        bool,
        Option(
            "--dictionary/--no-dictionary",
            help="Ask the dictionary during the analyses.",
        ),
    ] = False,
    timeout: Annotated[
        float,
        Option(
            help="Seconds to wait for every analysis.",
            min=1,
        ),
    ] = 900.0,
) -> None:
    """
    Measure the running services' throughput, latency, and queue wait.

    It submits many analyses to them at once.
    """

    settings = _settings()

    try:
        with TerminalProgress(
            console=_errors,
        ) as progress:
            written = run(
                main=runner.run_throughput(
                    text=text,
                    settings=settings,
                    analyses=analyses,
                    label=label,
                    include_dictionary=dictionary,
                    timeout_seconds=timeout,
                    progress=progress,
                ),
            )

    except _EXPECTED as exception:
        raise _fail(
            exception,
        ) from exception

    _console.print(
        f"wrote {written.rows} rows to {written.path}",
    )


@bench_app.command()
def plot() -> None:
    """
    Draw the figures from every results file present.
    """

    for path in runner.plot_all():
        _console.print(
            f"drew {path}",
        )


@app.command()
def doctor(
    install_nltk_data: Annotated[
        bool,
        Option(
            "--install-nltk-data",
            help="Download any missing NLTK package.",
        ),
    ] = False,
) -> None:
    """
    Check what this installation can do, and fix what it can.
    """

    settings = _settings()

    with TerminalProgress(
        console=_errors,
    ) as progress:
        report = commands.doctor(
            settings=settings,
            install_nltk_data=install_nltk_data,
            progress=progress,
        )

    for line in report.lines:
        _console.print(
            line,
            markup=False,
            highlight=False,
        )

    if not report.healthy:
        raise Exit(
            code=1,
        )


def main() -> None:
    """
    The console script entry point.
    """

    app()


if __name__ == "__main__":
    print(
        "use the `wordwinnow` command",
        file=stderr,
    )

    main()
