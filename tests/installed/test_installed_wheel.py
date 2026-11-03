"""
The built wheel, installed and run as a learner runs it: built from this
checkout, installed with the command the README gives, a fresh resolution into
a home of its own, set up, and used to analyze the sample story with the
network closed to it.

`make test-installed` runs these tests and `make test` leaves them out,
because they need uv and the network, for the packages, the NLTK data, and the
reference lists.
"""

from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import (
    dataclass,
)
from hashlib import (
    sha256,
)
from json import (
    loads,
)
from os import (
    environ,
)
from pathlib import (
    Path,
)
from shlex import (
    split,
)
from shutil import (
    which,
)
from subprocess import (
    CompletedProcess,
    run,
)
from sys import (
    platform,
)
from types import (
    MappingProxyType,
)
from typing import (
    Final,
    final,
)
from zipfile import (
    ZipFile,
)

from pytest import (
    FixtureRequest,
    TempPathFactory,
    fixture,
    mark,
    raises,
)

from wordwinnow.infrastructure.lexicon.reference_sources import (
    REFERENCE_CITATIONS,
)

_ROOT: Final = Path(
    __file__,
).parents[2]

_README: Final = _ROOT / "README.md"

# NOTE:
# The minor version the gate, the image, and the guides run, which every installed command here must run on too.
_MINOR: Final = (
    (_ROOT / ".python-version")
    .read_text(
        encoding="utf-8",
    )
    .strip()
)

# NOTE:
# The sample story is read by its absolute path, from the checkout, because the working directory of every command
# here is empty on purpose and a learner has no checkout.
_SAMPLE_STORY: Final = _ROOT / "data/corpus/a-scandal-in-bohemia.txt"

_SYSTEM_PATH: Final = "/usr/bin:/bin:/usr/sbin:/sbin"

# NOTE:
# A proxy that refuses every connection stops every client that honors these variables, as `httpx2` and NLTK's
# downloader do, and nothing else: a client that ignored them would reach the network unseen.
#
# So "offline" means offline for those two clients, and the positive control below shows the proxy stops `setup`.
_CLOSED_PROXY: Final = MappingProxyType(
    mapping=dict.fromkeys(
        (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ),
        "http://127.0.0.1:9",
    ),
)

# NOTE:
# A command that waits on the network for hours would hold a CI runner for as long, so every command gives up after a
# quarter of an hour, far longer than a cold install and a setup take.
_TIMEOUT_SECONDS: Final = 900

# NOTE:
# Where the installed command keeps its store, its model, and the lists it downloads, under a home that is new: the
# directories `platformdirs` names on macOS and on Linux when no `XDG_` variable is set.
_USER_DATA: Final = "Library/Application Support/wordwinnow" if platform == "darwin" else ".local/share/wordwinnow"

_MIGRATIONS: Final = frozenset(
    (
        "wordwinnow/infrastructure/persistence/migration_scripts/env.py",
        "wordwinnow/infrastructure/persistence/migration_scripts/script.py.mako",
        "wordwinnow/infrastructure/persistence/migration_scripts/versions/256cfbc06a10_initial_schema.py",
    ),
)

_DATA_FILES: Final = (
    "cefr_level_model.joblib",
    "cefr_level_model.joblib.metadata.json",
    "reference/cefrj-vocabulary-profile-1.5.csv",
    "reference/octanove-vocabulary-profile-c1c2-1.0.csv",
    "wordwinnow.db",
)


@dataclass(
    frozen=True,
    kw_only=True,
    slots=True,
)
@final
class Installation:
    """
    What the installed command did for a learner, in the order a learner runs
    it: the wheel, the places it worked in, and the result of each command.
    """

    wheel: Path

    home: Path

    working_directory: Path

    uv_version: str

    readme_command: tuple[str, ...]

    install: CompletedProcess[str]

    verbose_help: CompletedProcess[str]

    first_setup: CompletedProcess[str]

    second_setup: CompletedProcess[str]

    analysis: CompletedProcess[str]

    listing: CompletedProcess[str]

    shown: CompletedProcess[str]

    unreachable_lists: Path

    unreachable_setup: CompletedProcess[str]


def _execute(
    *,
    arguments: Sequence[str | Path],
    environment: Mapping[str, str],
    working_directory: Path,
) -> CompletedProcess[str]:
    return run(
        args=arguments,
        capture_output=True,
        cwd=working_directory,
        timeout=_TIMEOUT_SECONDS,
        check=False,
        text=True,
        env=environment,
    )


def _transcript(
    result: CompletedProcess[str],
    /,
) -> str:
    return f"{result.args} exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _succeeded(
    result: CompletedProcess[str],
    /,
) -> CompletedProcess[str]:
    assert result.returncode == 0, _transcript(
        result,
    )

    return result


def _analysis_id(
    output: str,
    /,
) -> str:
    # NOTE:
    # A run that printed no analysis still lets `show` run, so that its failure is the test's own and not the
    # fixture's.
    try:
        return str(
            object=loads(
                s=output,
            )["analysis_id"],
        )

    except (
        ValueError,
        KeyError,
    ):
        return "nonexistent"


def _words(
    text: str,
    /,
) -> str:
    return " ".join(
        text.split(),
    )


def _python_option(
    command: Sequence[str],
    /,
) -> str | None:
    for (
        index,
        argument,
    ) in enumerate(
        iterable=command,
    ):
        if argument == "--python":
            following = command[index + 1 :]

            return following[0] if following else None

        if argument.startswith(
            "--python=",
        ):
            return argument.removeprefix(
                "--python=",
            )

    return None


def _install_command(
    readme: str,
    /,
) -> tuple[str, ...]:
    # NOTE:
    # The command is the one line of the README that begins `uv tool install` once its indentation is taken off, split
    # as a shell splits it, so that the check installs as the guides say, and as the release's wheel URL will: only
    # its last argument, the source, is replaced, by the wheel built here.
    lines = tuple(
        stripped
        for stripped in (line.lstrip() for line in readme.splitlines())
        if stripped.startswith(
            "uv tool install",
        )
    )

    count = len(
        lines,
    )

    assert count == 1, f"{_README} needs exactly one line that begins `uv tool install`, and has {count}"

    command = tuple(
        split(
            s=lines[0],
        ),
    )

    option = _python_option(
        command,
    )

    assert option == _MINOR, f"the README's install command names --python {option}, not {_MINOR}: {lines[0]}"

    return command


def _readme_install_command() -> tuple[str, ...]:
    return _install_command(
        _README.read_text(
            encoding="utf-8",
        ),
    )


@fixture(
    scope="module",
)
def installation(
    *,
    request: FixtureRequest,
    tmp_path_factory: TempPathFactory,
) -> Installation:
    found = which(
        cmd="uv",
    )

    assert found is not None, "uv is needed on PATH: this check installs the wheel as a learner does"

    uv = Path(
        found,
    )

    root = tmp_path_factory.mktemp(
        basename="installed",
    )

    home = root / "home"

    tool_dir = root / "tools"

    tool_bin_dir = root / "bin"

    wheels = root / "wheels"

    working_directory = root / "empty"

    unreachable_lists = root / "no-lists"

    for directory in (
        home,
        tool_dir,
        tool_bin_dir,
        wheels,
        working_directory,
        unreachable_lists,
    ):
        directory.mkdir()

    # NOTE:
    # `uv` is found on the test's own `PATH` and its directory is put on the learner's, so on a developer's machine uv
    # may take a 3.14 found there, and on a runner, where that directory holds uv alone, it downloads one.
    #
    # The cache is the test's own, so that files already downloaded are reused while the resolution is still fresh,
    # and no `WORDWINNOW_`, `XDG_`, `NLTK_DATA`, `VIRTUAL_ENV`, or other `UV_` variable reaches any command.
    cache_dir = _succeeded(
        _execute(
            arguments=[
                uv,
                "cache",
                "dir",
            ],
            environment=environ,
            working_directory=_ROOT,
        ),
    ).stdout.strip()

    readme_command = _readme_install_command()

    uv_version = _succeeded(
        _execute(
            arguments=[
                uv,
                "--version",
            ],
            environment=environ,
            working_directory=_ROOT,
        ),
    ).stdout.strip()

    environment = {
        "PATH": f"{tool_bin_dir}:{uv.parent}:{_SYSTEM_PATH}",
        "HOME": str(
            object=home,
        ),
        "UV_CACHE_DIR": cache_dir,
        "UV_TOOL_DIR": str(
            object=tool_dir,
        ),
        "UV_TOOL_BIN_DIR": str(
            object=tool_bin_dir,
        ),
    }

    closed = environment | _CLOSED_PROXY

    _succeeded(
        _execute(
            arguments=[
                uv,
                "build",
                "--wheel",
                "--out-dir",
                wheels,
            ],
            environment=environment,
            working_directory=_ROOT,
        ),
    )

    (wheel,) = wheels.glob(
        pattern="*.whl",
    )

    # NOTE:
    # The digest of the file the next command installs, in the form `sha256sum` writes, so that the log of a run shows
    # which bytes were tested and a reader can compare them with a release's notes.
    #
    # It goes through the terminal reporter, which ends the line pytest has open before it writes, and not through
    # `print`, which pytest captures and shows only for a failure.
    digest = sha256(
        data=wheel.read_bytes(),
    ).hexdigest()

    reporter = request.config.pluginmanager.get_plugin(
        name="terminalreporter",
    )

    assert reporter is not None, "the digest of the installed wheel needs pytest's terminal reporter"

    reporter.write_line(
        line=f"{digest}  {wheel.name}",
    )

    install = _succeeded(
        _execute(
            arguments=[
                uv,
                *readme_command[1:-1],
                wheel,
            ],
            environment=environment,
            working_directory=working_directory,
        ),
    )

    command = tool_bin_dir / "wordwinnow"

    # NOTE:
    # In verbose mode CPython prints its version on standard error as it starts, so the line names the interpreter
    # that runs the installed command itself, not one beside it.
    verbose_help = _execute(
        arguments=[
            command,
            "--help",
        ],
        environment=environment
        | {
            "PYTHONVERBOSE": "1",
        },
        working_directory=working_directory,
    )

    first_setup = _execute(
        arguments=[
            command,
            "setup",
        ],
        environment=environment,
        working_directory=working_directory,
    )

    second_setup = _execute(
        arguments=[
            command,
            "setup",
        ],
        environment=environment,
        working_directory=working_directory,
    )

    analysis = _execute(
        arguments=[
            command,
            "analyze",
            _SAMPLE_STORY,
            "--level",
            "A2",
            "--target",
            "B1",
            "--no-dictionary",
            "--json",
        ],
        environment=closed,
        working_directory=working_directory,
    )

    listing = _execute(
        arguments=[
            command,
            "list",
        ],
        environment=closed,
        working_directory=working_directory,
    )

    shown = _execute(
        arguments=[
            command,
            "show",
            _analysis_id(
                analysis.stdout,
            ),
        ],
        environment=closed,
        working_directory=working_directory,
    )

    unreachable_setup = _execute(
        arguments=[
            command,
            "setup",
        ],
        environment=closed
        | {
            "WORDWINNOW_REFERENCE_LISTS_DIR": str(
                object=unreachable_lists,
            ),
        },
        working_directory=working_directory,
    )

    return Installation(
        wheel=wheel,
        home=home,
        working_directory=working_directory,
        uv_version=uv_version,
        readme_command=readme_command,
        install=install,
        verbose_help=verbose_help,
        first_setup=first_setup,
        second_setup=second_setup,
        analysis=analysis,
        listing=listing,
        shown=shown,
        unreachable_lists=unreachable_lists,
        unreachable_setup=unreachable_setup,
    )


@mark.installed
@final
class TestInstalledWheel:
    def test_the_wheel_carries_its_migrations_and_no_third_party_data(
        self,
        *,
        installation: Installation,
    ) -> None:
        with ZipFile(
            file=installation.wheel,
        ) as wheel:
            names = frozenset(
                name
                for name in wheel.namelist()
                if not name.endswith(
                    "/",
                )
            )

        assert frozenset(name for name in names if "/migration_scripts/" in name) == _MIGRATIONS

        assert not {
            name
            for name in names
            if name.endswith(
                (
                    ".csv",
                    ".joblib",
                ),
            )
        }

    def test_the_install_ran_the_readmes_command_with_only_its_source_replaced(
        self,
        *,
        installation: Installation,
    ) -> None:
        ran = tuple(
            str(
                object=argument,
            )
            for argument in installation.install.args
        )

        assert (
            Path(
                ran[0],
            ).name
            == "uv"
        ), _transcript(
            installation.install,
        )

        assert ran[1:-1] == installation.readme_command[1:-1], _transcript(
            installation.install,
        )

        assert ran[-1] == str(
            object=installation.wheel,
        ), _transcript(
            installation.install,
        )

        assert installation.readme_command[-1] != ran[-1]

    def test_the_readme_reader_finds_an_indented_command_and_wants_exactly_one(
        self,
    ) -> None:
        # TEST:
        # Positive controls of the reader the fixture installs by: an indented command is found, and a second one,
        # indented beside one at the left margin, is counted, so that the fixture refuses a README with two.
        indented = f"  uv tool install --python {_MINOR} git+https://example.invalid/foo\n"

        assert _install_command(
            indented,
        ) == (
            "uv",
            "tool",
            "install",
            "--python",
            _MINOR,
            "git+https://example.invalid/foo",
        )

        with raises(
            expected_exception=AssertionError,
            match="exactly one line",
        ):
            _install_command(
                f"uv tool install --python {_MINOR} git+https://example.invalid/bar\n{indented}",
            )

    def test_the_installed_command_runs_on_the_python_the_gate_tests(
        self,
        *,
        installation: Installation,
    ) -> None:
        result = installation.verbose_help

        # NOTE:
        # An interpreter that stopped printing its version line would leave no line to find, and this test would fail
        # on it, never pass.
        banners = tuple(
            line
            for line in result.stderr.splitlines()
            if line.startswith(
                "Python ",
            )
        )

        assert result.returncode == 0, _transcript(
            result,
        )

        assert any(
            banner.startswith(
                f"Python {_MINOR}.",
            )
            for banner in banners
        ), (
            f"{result.args} printed the lines {banners} that begin `Python `, not `Python {_MINOR}.`; "
            f"{installation.uv_version}"
        )

    def test_setup_prepares_everything_and_says_so(
        self,
        *,
        installation: Installation,
    ) -> None:
        lines = _succeeded(
            installation.first_setup,
        ).stdout.splitlines()

        assert lines[-6].startswith(
            "ready   ",
        ), _transcript(
            installation.first_setup,
        )

        assert lines[-3] == "        next: wordwinnow analyze FILE --level LEVEL", _transcript(
            installation.first_setup,
        )

        assert (
            tuple(
                lines[-2:],
            )
            == REFERENCE_CITATIONS
        ), _transcript(
            installation.first_setup,
        )

    def test_a_second_setup_prints_only_that_it_is_ready_and_the_citations(
        self,
        *,
        installation: Installation,
    ) -> None:
        lines = _succeeded(
            installation.second_setup,
        ).stdout.splitlines()

        assert (
            len(
                lines,
            )
            == 6
        ), _transcript(
            installation.second_setup,
        )

        assert lines[0].startswith(
            "ready   ",
        ), _transcript(
            installation.second_setup,
        )

        assert (
            tuple(
                lines[4:],
            )
            == REFERENCE_CITATIONS
        ), _transcript(
            installation.second_setup,
        )

    def test_an_analysis_with_no_mode_option_runs_offline_on_the_level_model(
        self,
        *,
        installation: Installation,
    ) -> None:
        payload = loads(
            s=_succeeded(
                installation.analysis,
            ).stdout,
        )

        assert payload["status"] == "completed", _transcript(
            installation.analysis,
        )

        sources = frozenset(item["level_source"] for item in payload["items"])

        assert "model" in sources

        assert "frequency_heuristic" not in sources

    def test_list_and_show_with_no_mode_option_find_that_analysis(
        self,
        *,
        installation: Installation,
    ) -> None:
        analysis_id = _analysis_id(
            installation.analysis.stdout,
        )

        assert (
            analysis_id
            in _succeeded(
                installation.listing,
            ).stdout
        ), _transcript(
            installation.listing,
        )

        assert (
            "completed"
            in _succeeded(
                installation.shown,
            ).stdout
        ), _transcript(
            installation.shown,
        )

    def test_setup_cannot_reach_the_network_under_the_closed_proxy(
        self,
        *,
        installation: Installation,
    ) -> None:
        # TEST:
        # The positive control of the offline analysis: the same proxy stops `setup`, which downloads, so an analysis
        # that succeeds under it did not need the network.
        result = installation.unreachable_setup

        assert result.returncode == 1, _transcript(
            result,
        )

        message = _words(
            result.stdout + result.stderr,
        )

        assert "could not be fetched" in message, _transcript(
            result,
        )

        assert "ConnectError" in message, _transcript(
            result,
        )

        assert not tuple(
            installation.unreachable_lists.iterdir(),
        )

    def test_nothing_is_written_but_the_users_own_directory(
        self,
        *,
        installation: Installation,
    ) -> None:
        assert not tuple(
            installation.working_directory.iterdir(),
        )

        data = installation.home / _USER_DATA

        assert (
            tuple(
                sorted(
                    path.relative_to(
                        other=data,
                    ).as_posix()
                    for path in data.rglob(
                        pattern="*",
                    )
                    if path.is_file()
                ),
            )
            == _DATA_FILES
        )
