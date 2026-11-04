"""
The `.env` step, run as `make up` runs it: a program, started in a working
directory of its own, that writes what Compose will read.
"""

from os import (
    geteuid,
)
from pathlib import (
    Path,
)
from re import (
    compile as compile_pattern,
)
from stat import (
    S_IMODE,
)
from subprocess import (
    CompletedProcess,
    run,
)
from sys import (
    executable,
)
from typing import (
    Final,
    final,
)

from pytest import (
    mark,
    skip,
)

_PROGRAM: Final = (
    Path(
        __file__,
    ).parents[2]
    / "stack"
    / "credentials.py"
)

_TIMEOUT_SECONDS: Final = 30

_CLICKHOUSE: Final = "WORDWINNOW_CLICKHOUSE_PASSWORD"

_GRAFANA: Final = "GRAFANA_ADMIN_PASSWORD"

_CREATED: Final = "created .env, readable by you alone, for the stack's generated passwords\n"

_GENERATED_CLICKHOUSE: Final = (
    "generated WORDWINNOW_CLICKHOUSE_PASSWORD in .env, the password of ClickHouse's user wordwinnow\n"
)

_GENERATED_GRAFANA: Final = "generated GRAFANA_ADMIN_PASSWORD in .env, the password of Grafana's user admin\n"

_PUBLISHED_CLICKHOUSE: Final = (
    "WORDWINNOW_CLICKHOUSE_PASSWORD in .env is wordwinnow, which this repository once published, so a web page can "
    "use ClickHouse with it while the stack runs; delete the line, then run make up, to have one generated\n"
)

_PUBLISHED_GRAFANA: Final = (
    "GRAFANA_ADMIN_PASSWORD in .env is wordwinnow, which this repository once published; delete the line, then run "
    "make up-full, to have one generated\n"
)

# NOTE:
# The alphabet of a generated value: no character that `.env`, Compose's substitution, or a URL needs quoted.
_VALUE: Final = compile_pattern(
    pattern=r"[A-Za-z0-9_-]{22,}",
)


def _empty(
    name: str,
    /,
) -> str:
    return (
        f"{name} in .env is empty, which Compose refuses; delete the line, then run make up, to have one generated\n"
    )


def _step(
    directory: Path,
    /,
) -> CompletedProcess[str]:
    """
    The program, run in `directory` with an empty environment, since it reads
    no variable.
    """

    return run(
        args=[
            executable,
            "-I",
            str(
                object=_PROGRAM,
            ),
        ],
        capture_output=True,
        cwd=directory,
        timeout=_TIMEOUT_SECONDS,
        check=False,
        text=True,
        env={},
    )


def _value(
    contents: str,
    name: str,
    /,
) -> str:
    """
    What the line of `contents` that sets `name` holds.
    """

    (line,) = (
        line
        for line in contents.splitlines()
        if line.startswith(
            f"{name}=",
        )
    )

    return line.removeprefix(
        f"{name}=",
    )


@final
class TestACreatedFile:
    def test_a_missing_file_is_created_readable_by_its_owner_alone_with_both_lines(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        result = _step(
            tmp_path,
        )

        env = tmp_path / ".env"

        assert result.returncode == 0

        assert result.stdout == _CREATED + _GENERATED_CLICKHOUSE + _GENERATED_GRAFANA

        assert result.stderr == ""

        assert (
            S_IMODE(
                env.stat().st_mode,
            )
            == 0o600
        )

        contents = env.read_text(
            encoding="utf-8",
        )

        assert [
            line.split(
                sep="=",
            )[0]
            for line in contents.splitlines()
        ] == [
            _CLICKHOUSE,
            _GRAFANA,
        ]

    def test_each_value_is_long_and_of_the_alphabet_and_none_is_printed(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        result = _step(
            tmp_path,
        )

        contents = (tmp_path / ".env").read_text(
            encoding="utf-8",
        )

        for name in (
            _CLICKHOUSE,
            _GRAFANA,
        ):
            value = _value(
                contents,
                name,
            )

            assert _VALUE.fullmatch(
                string=value,
            )

            assert value not in result.stdout + result.stderr

    def test_two_runs_in_two_directories_generate_different_values(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        for name in (
            "foo",
            "bar",
        ):
            (tmp_path / name).mkdir()

            _step(
                tmp_path / name,
            )

        first = (tmp_path / "foo" / ".env").read_text(
            encoding="utf-8",
        )

        second = (tmp_path / "bar" / ".env").read_text(
            encoding="utf-8",
        )

        for name in (
            _CLICKHOUSE,
            _GRAFANA,
        ):
            assert _value(
                first,
                name,
            ) != _value(
                second,
                name,
            )


@final
class TestAnExistingFile:
    def test_a_file_with_neither_gains_both_at_its_end_and_keeps_every_earlier_byte_and_its_mode(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        env = tmp_path / ".env"

        before = b"# foo\nWORDWINNOW_NYT_API_KEY=\n# WORDWINNOW_CLICKHOUSE_PASSWORD=bar\n"

        env.write_bytes(
            data=before,
        )

        env.chmod(
            mode=0o640,
        )

        result = _step(
            tmp_path,
        )

        after = env.read_bytes()

        assert result.returncode == 0

        assert result.stdout == _GENERATED_CLICKHOUSE + _GENERATED_GRAFANA

        assert after.startswith(
            before,
        )

        added = after.removeprefix(
            before,
        )

        assert [
            line.split(
                sep=b"=",
            )[0]
            for line in added.splitlines()
        ] == [
            _CLICKHOUSE.encode(),
            _GRAFANA.encode(),
        ]

        assert (
            S_IMODE(
                env.stat().st_mode,
            )
            == 0o640
        )

    def test_a_file_without_a_final_line_break_gains_one_before_them(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        env = tmp_path / ".env"

        env.write_bytes(
            data=b"FOO=bar",
        )

        _step(
            tmp_path,
        )

        assert env.read_bytes().startswith(
            b"FOO=bar\n" + _CLICKHOUSE.encode() + b"=",
        )

    def test_only_the_missing_one_is_added(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        env = tmp_path / ".env"

        env.write_bytes(
            data=f"{_GRAFANA}=foo\n".encode(),
        )

        result = _step(
            tmp_path,
        )

        assert result.stdout == _GENERATED_CLICKHOUSE

        assert env.read_bytes().startswith(
            f"{_GRAFANA}=foo\n{_CLICKHOUSE}=".encode(),
        )

    @mark.parametrize(
        argnames="contents",
        argvalues=(
            f"{_CLICKHOUSE}=foo\n{_GRAFANA}=bar\n",
            f"{_CLICKHOUSE}=\"foo\"\n{_GRAFANA}='bar'\n",
            f"export {_CLICKHOUSE}=foo\n  export   {_GRAFANA} = bar\n",
            f"{_CLICKHOUSE}=foo # a comment\n{_GRAFANA}=bar\n",
            f"{_CLICKHOUSE}=foo\n{_GRAFANA}=bar",
        ),
    )
    def test_an_existing_value_is_kept_byte_for_byte_and_nothing_is_printed(
        self,
        *,
        tmp_path: Path,
        contents: str,
    ) -> None:
        env = tmp_path / ".env"

        env.write_text(
            data=contents,
            encoding="utf-8",
        )

        result = _step(
            tmp_path,
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

        assert env.read_bytes() == contents.encode()

    def test_a_commented_line_or_a_longer_name_sets_nothing(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        env = tmp_path / ".env"

        env.write_text(
            data=f"# {_CLICKHOUSE}=foo\n{_GRAFANA}_OLD=bar\n",
            encoding="utf-8",
        )

        result = _step(
            tmp_path,
        )

        assert result.stdout == _GENERATED_CLICKHOUSE + _GENERATED_GRAFANA

    def test_the_last_line_that_sets_a_name_is_the_one_that_counts(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        env = tmp_path / ".env"

        contents = f"{_CLICKHOUSE}=\n{_CLICKHOUSE}=foo\n{_GRAFANA}=bar\n"

        env.write_text(
            data=contents,
            encoding="utf-8",
        )

        result = _step(
            tmp_path,
        )

        assert result.returncode == 0

        assert (
            env.read_text(
                encoding="utf-8",
            )
            == contents
        )

    @mark.parametrize(
        argnames="written",
        argvalues=(
            "wordwinnow",
            '"wordwinnow"',
            "'wordwinnow'",
        ),
    )
    def test_the_published_password_is_kept_and_a_notice_says_to_replace_it(
        self,
        *,
        tmp_path: Path,
        written: str,
    ) -> None:
        env = tmp_path / ".env"

        contents = f"{_CLICKHOUSE}={written}\n{_GRAFANA}={written}\n"

        env.write_text(
            data=contents,
            encoding="utf-8",
        )

        result = _step(
            tmp_path,
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == _PUBLISHED_CLICKHOUSE + _PUBLISHED_GRAFANA

        assert (
            env.read_text(
                encoding="utf-8",
            )
            == contents
        )

    @mark.parametrize(
        argnames="empty",
        argvalues=(
            "",
            '""',
            "''",
            "   ",
            " # a comment",
        ),
    )
    @mark.parametrize(
        argnames="name",
        argvalues=(
            _CLICKHOUSE,
            _GRAFANA,
        ),
    )
    def test_an_empty_value_stops_the_step_with_its_line_and_changes_no_file(
        self,
        *,
        tmp_path: Path,
        name: str,
        empty: str,
    ) -> None:
        env = tmp_path / ".env"

        contents = f"FOO=bar\n{name}={empty}\n"

        env.write_text(
            data=contents,
            encoding="utf-8",
        )

        result = _step(
            tmp_path,
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == _empty(
            name,
        )

        assert (
            env.read_text(
                encoding="utf-8",
            )
            == contents
        )

    def test_a_second_run_changes_nothing_and_prints_nothing(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        _step(
            tmp_path,
        )

        env = tmp_path / ".env"

        first = env.read_bytes()

        result = _step(
            tmp_path,
        )

        assert result.returncode == 0

        assert result.stdout == ""

        assert result.stderr == ""

        assert env.read_bytes() == first


@final
class TestAFileItCannotUse:
    def test_a_directory_it_cannot_write_to_stops_it_with_one_line(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        if geteuid() == 0:
            skip(
                reason="the root user can write to any directory, so there is none to refuse it",
            )

        locked = tmp_path / "foo"

        locked.mkdir()

        locked.chmod(
            mode=0o500,
        )

        try:
            result = _step(
                locked,
            )

        finally:
            locked.chmod(
                mode=0o700,
            )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == "cannot write .env: Permission denied\n"

        assert not (locked / ".env").exists()

    def test_a_file_it_cannot_read_stops_it_with_one_line(
        self,
        *,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".env").mkdir()

        result = _step(
            tmp_path,
        )

        assert result.returncode == 1

        assert result.stdout == ""

        assert result.stderr == "cannot read .env: Is a directory\n"
